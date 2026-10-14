"""Parse the Portaldot V3 testnet guide into documented-call sites: README, demo code, FAQ.

The guide (`ItsCogumellum/portaldot-v3-testnet-guide`, pinned below) is a translation, so prose and
sample code can disagree. Its three parts are kept as separate sources and never merged:

  v3-guide  README.md: every name in a `| Call | Description |` table, attributed to the pallet named
            by the enclosing `### <n.n> <Pallet>` heading, and every Node Explorer path
            `Developer → Extrinsics → <Pallet> → <call>` (a call) or
            `Developer → Runtime calls → <api> → <method>` (a runtime API). Prose mentions are not read.
  v3-demo   demo/*.py, read as a Python AST (never imported, never executed): every
            `<obj>.compose_call(call_module, call_function, call_params)`, every
            `<obj>.runtime_call(api, method, …)` and every raw `<obj>.rpc_request("state_call",
            ["<Api>_<method>", …])`. A name argument that is not a string literal is resolved only from
            assignments in the same function whose values are string literals or a conditional between
            two literals; anything else stays unresolved and is reported, never guessed.
  v3-faq    FAQ.md: each row of the table that has a `Runtime module` column (one module per row).

This module does no network I/O and runs nothing from the guide; `scripts/fetch_guide.py` clones it.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import verdicts as v

GUIDE_REPO = "https://github.com/ItsCogumellum/portaldot-v3-testnet-guide"
GUIDE_COMMIT = "d671a32758573f4d9ab00d891b2dd6a48f36bcbb"  # "Add Portaldot V3.0 FAQ", 2026-10-07

# how a site's names were read
LITERAL, LOCAL, UNRESOLVED = "literal", "local", "unresolved"
TABLE, EXPLORER, FAQ_TABLE = "call-table", "explorer-path", "faq-table"
RUNTIME_CALL, STATE_CALL = "runtime_call", "state_call"


@dataclass(frozen=True)
class Site:
    """One place in the guide that names a call, runtime API or module."""
    source: str                    # verdicts.V3_GUIDE | V3_DEMO | V3_FAQ
    kind: str                      # verdicts.CALL | RUNTIME_API | MODULE
    pallet: str | None             # as written; None = could not be resolved
    calls: tuple[str, ...]         # as written; () = unresolved; ("",) for a module row
    ref: str                       # "<file>:<line>"
    how: str                       # literal | local | unresolved | call-table | explorer-path | ...
    args: tuple[str, ...] | None = None  # demo: the call_params keys as written; None = not static
    note: str = ""                 # FAQ: the "Current status" cell

    @property
    def resolved(self) -> bool:
        return self.pallet is not None and bool(self.calls)


@dataclass
class GuideResult:
    sites: list[Site] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256 of the bytes parsed
    awaiting_lines: list[int] = field(default_factory=list)  # FAQ lines with a bold "awaiting …" status

    def count(self, source: str, how: str | None = None) -> int:
        return sum(1 for s in self.sites if s.source == source and (how is None or s.how == how))


# ---------------------------------------------------------------- demo/*.py (AST only)

def _enclosing_scopes(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """node -> the innermost function (or the module) it sits in."""
    scope_of: dict[ast.AST, ast.AST] = {}

    def visit(node: ast.AST, scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            scope_of[child] = scope
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) else scope
            visit(child, inner)

    visit(tree, tree)
    return scope_of


def _scope_assignments(scope: ast.AST, name: str) -> list[ast.expr]:
    """Values assigned to `name` directly in `scope` (nested functions excluded)."""
    values: list[ast.expr] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in child.targets):
                values.append(child.value)
            elif isinstance(child, ast.AnnAssign) and child.value is not None and \
                    isinstance(child.target, ast.Name) and child.target.id == name:
                values.append(child.value)
            elif _binds_otherwise(child, name):
                values.append(ast.Name(id=name))  # bound some other way: never a plain literal
            visit(child)

    visit(scope)
    return values


def _binds_otherwise(node: ast.AST, name: str) -> bool:
    """`name` is bound by an augmented assignment, a loop/with/walrus target or a tuple unpacking."""
    if isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.NamedExpr)):
        targets = [node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
    elif isinstance(node, ast.Assign):
        targets = [t for t in node.targets if not isinstance(t, ast.Name)]  # a, b = …
    else:
        return False
    return any(isinstance(n, ast.Name) and n.id == name for t in targets for n in ast.walk(t))


def _literal(expr: ast.expr) -> list[str] | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return [expr.value]
    if isinstance(expr, ast.IfExp):
        body, orelse = _literal(expr.body), _literal(expr.orelse)
        if body is not None and orelse is not None:
            return body + [x for x in orelse if x not in body]
    return None


def _strings(expr: ast.expr | None, scope: ast.AST) -> tuple[list[str], str]:
    """Resolve a name argument to its candidate strings, and say how."""
    if expr is None:
        return [], UNRESOLVED
    direct = _literal(expr)
    if direct is not None:
        return direct, LITERAL if isinstance(expr, ast.Constant) else LOCAL
    if isinstance(expr, ast.Name):
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and expr.id in {
                a.arg for a in ast.walk(scope.args) if isinstance(a, ast.arg)}:
            return [], UNRESOLVED  # a parameter: the caller decides
        values = _scope_assignments(scope, expr.id)
        out: list[str] = []
        for value in values:
            lit = _literal(value)
            if lit is None:
                return [], UNRESOLVED
            out += [x for x in lit if x not in out]
        if out:
            return out, LOCAL
    return [], UNRESOLVED


def _arg(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[position] if len(call.args) > position else None


def _param_keys(expr: ast.expr | None) -> tuple[str, ...] | None:
    if isinstance(expr, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(k.value, str)
                                          for k in expr.keys):
        return tuple(k.value for k in expr.keys)
    return None


def parse_demo(text: str, file: str) -> list[Site]:
    """Call sites in one demo file, in source order. `file` is the ref prefix, e.g. demo/01_assets_demo.py."""
    tree = ast.parse(text, filename=file)
    scope_of = _enclosing_scopes(tree)
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        scope = scope_of.get(node, tree)
        ref = f"{file}:{node.lineno}"
        method = node.func.attr
        if method == "compose_call":
            modules, how_m = _strings(_arg(node, 0, "call_module"), scope)
            funcs, how_f = _strings(_arg(node, 1, "call_function"), scope)
            pallet = modules[0] if len(modules) == 1 else None
            how = UNRESOLVED if UNRESOLVED in (how_m, how_f) or pallet is None else (
                LOCAL if LOCAL in (how_m, how_f) else LITERAL)
            sites.append(Site(source=v.V3_DEMO, kind=v.CALL, pallet=pallet,
                              calls=tuple(funcs) if pallet else (), ref=ref, how=how,
                              args=_param_keys(_arg(node, 2, "call_params"))))
        elif method == "runtime_call":
            apis, how_a = _strings(_arg(node, 0, "api"), scope)
            methods, how_m = _strings(_arg(node, 1, "method"), scope)
            pallet = apis[0] if len(apis) == 1 else None
            sites.append(Site(source=v.V3_DEMO, kind=v.RUNTIME_API, pallet=pallet,
                              calls=tuple(methods) if pallet else (), ref=ref,
                              how=RUNTIME_CALL if pallet and methods else UNRESOLVED))
        elif method == "rpc_request":
            rpc, _ = _strings(_arg(node, 0, "method"), scope)
            params = _arg(node, 1, "params")
            if rpc != ["state_call"]:
                continue
            first = params.elts[0] if isinstance(params, (ast.List, ast.Tuple)) and params.elts else None
            names, _ = _strings(first, scope)
            api, sep, fn = names[0].partition("_") if len(names) == 1 else ("", "", "")
            sites.append(Site(source=v.V3_DEMO, kind=v.RUNTIME_API, pallet=api if sep else None,
                              calls=(fn,) if sep and fn else (), ref=ref,
                              how=STATE_CALL if sep and fn else UNRESOLVED))
    sites.sort(key=lambda s: int(s.ref.rsplit(":", 1)[1]))
    return sites


# ---------------------------------------------------------------- README.md

HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?:[\d.]+\s+)?(?P<title>.+?)\s*$")
PALLET_TITLE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
CALL_TABLE_HEADER = re.compile(r"^\|\s*Call\s*\|", re.IGNORECASE)
TICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
EXPLORER_PATH = re.compile(
    r"Developer\s*→\s*(?P<area>Extrinsics|Runtime calls)\s*→\s*(?P<pallet>[A-Za-z][A-Za-z0-9_]*)\s*→\s*"
    r"(?P<call>[A-Za-z_][A-Za-z0-9_]*)")


def parse_readme(text: str, file: str = "README.md") -> list[Site]:
    sites: list[Site] = []
    pallet: str | None = None
    table_pallet: str | None = None
    in_table = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        heading = HEADING.match(line)
        if heading:
            in_table = False
            level = len(heading["hashes"])
            if level < 3:
                pallet = None
            elif level == 3:  # "### 2.1 Assets": the pallet a section's call tables belong to
                pallet = heading["title"] if PALLET_TITLE.match(heading["title"]) else None
            continue
        if CALL_TABLE_HEADER.match(line):
            in_table, table_pallet = True, pallet
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
            elif not re.match(r"^\|[\s:|-]+\|?\s*$", line):  # skip the |---|---| separator
                first_cell = line.strip().strip("|").split("|")[0]
                for name in TICKED.findall(first_cell):
                    sites.append(Site(source=v.V3_GUIDE, kind=v.CALL, pallet=table_pallet,
                                      calls=(name,) if table_pallet else (), ref=f"{file}:{lineno}",
                                      how=TABLE if table_pallet else UNRESOLVED))
                continue
        for m in EXPLORER_PATH.finditer(line):
            kind = v.CALL if m["area"] == "Extrinsics" else v.RUNTIME_API
            sites.append(Site(source=v.V3_GUIDE, kind=kind, pallet=m["pallet"], calls=(m["call"],),
                              ref=f"{file}:{lineno}", how=EXPLORER))
    return sites


# ---------------------------------------------------------------- FAQ.md

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_faq(text: str, file: str = "FAQ.md") -> tuple[list[Site], list[int]]:
    """Module rows of the `Runtime module` table, and the lines that bold an "awaiting …" status
    (the status-key definition line excluded)."""
    sites: list[Site] = []
    columns: list[str] | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("|"):
            columns = None
            continue
        cells = _cells(line)
        if columns is None:
            lowered = [c.lower() for c in cells]
            if "runtime module" in lowered:
                columns = lowered
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        row = dict(zip(columns, cells))
        names = TICKED.findall(row.get("runtime module", ""))
        status = row.get("current status", "")
        if len(names) == 1:
            sites.append(Site(source=v.V3_FAQ, kind=v.MODULE, pallet=names[0], calls=("",),
                              ref=f"{file}:{lineno}", how=FAQ_TABLE, note=status))
        else:  # zero or several module names in one cell: report it, don't pick one
            sites.append(Site(source=v.V3_FAQ, kind=v.MODULE, pallet=None, calls=(), ref=f"{file}:{lineno}",
                              how=UNRESOLVED, note=status))
    awaiting = [n for n, line in enumerate(text.splitlines(), start=1)
                if re.search(r"\*\*awaiting", line, re.IGNORECASE) and not line.lstrip().startswith("- **Awaiting")]
    return sites, awaiting


# ---------------------------------------------------------------- the whole guide

def parse_guide(guide_dir: Path) -> GuideResult:
    """Parse README.md, FAQ.md and every demo/*.py of a guide checkout (read as text)."""
    guide_dir = Path(guide_dir)
    result = GuideResult()

    def read(rel: str) -> str:
        data = (guide_dir / rel).read_bytes()
        result.files[rel] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8")

    result.sites += parse_readme(read("README.md"), "README.md")
    for path in sorted((guide_dir / "demo").glob("*.py")):
        rel = path.relative_to(guide_dir).as_posix()
        result.sites += parse_demo(read(rel), rel)
    faq_sites, result.awaiting_lines = parse_faq(read("FAQ.md"), "FAQ.md")
    result.sites += faq_sites
    return result


def clone_state(guide_dir: Path) -> tuple[str | None, bool]:
    """(HEAD commit, has local changes) of a guide checkout; (None, True) if it is not its own git checkout."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(guide_dir), *args], capture_output=True, text=True)

    head = git("rev-parse", "HEAD")
    top = git("rev-parse", "--show-toplevel")
    if head.returncode != 0 or Path(top.stdout.strip()).resolve() != Path(guide_dir).resolve():
        return None, True  # not a checkout, or a folder inside some other repository
    status = git("status", "--porcelain", "--untracked-files=all")
    return head.stdout.strip(), bool(status.stdout.strip()) or status.returncode != 0


def site_to_dict(site: Site) -> dict:
    return {"source": site.source, "kind": site.kind, "pallet": site.pallet, "calls": list(site.calls),
            "ref": site.ref, "how": site.how, "args": list(site.args) if site.args is not None else None,
            "note": site.note}


def site_from_dict(row: dict) -> Site:
    return Site(source=row["source"], kind=row["kind"], pallet=row["pallet"], calls=tuple(row["calls"]),
                ref=row["ref"], how=row["how"], args=tuple(row["args"]) if row["args"] is not None else None,
                note=row.get("note", ""))
