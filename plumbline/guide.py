"""Parse the Portaldot V3 testnet guide into documented-call sites: README, demo code, FAQ.

The guide (`ItsCogumellum/portaldot-v3-testnet-guide`, pinned below) is a translation, so prose and
sample code can disagree. Its three parts are kept as separate sources and never merged:

  v3-guide  README.md: every row of a `| Call | Description |` table (also `Function`, `Extrinsic`,
            `Method`), attributed to the pallet named by the enclosing `### <n.n> <Pallet>` heading, and
            every Node Explorer path `Developer → Extrinsics → <Pallet> → <call>` (a call) or
            `Developer → Runtime calls → <api> → <method>` (a runtime API). A table row whose first cell
            is not exactly `name` or `name` / `name` is reported unresolved. Prose mentions are not read;
            fenced code blocks are skipped, but a Developer path or call table that yields no site (fenced
            or not) is reported as unaccounted.
  v3-demo   demo/*.py, read as a Python AST (never imported, never executed): every
            `compose_call(call_module, call_function, call_params)`, every `runtime_call(api, method, …)`
            and every raw `rpc_request("state_call", ["<Api>_<method>", …])`, called as a method or as a
            bare name. A name argument that is not a string literal is resolved only from assignments in
            the same function whose values are string literals or a conditional between two literals.
            Any other reference to those three names (an alias, `getattr`, a string naming one of them, a
            definition, an unresolved argument) and any `getattr` with a non-literal name is an unresolved
            site: reported, never guessed. `unaccounted_references` checks that every identifier or string
            token naming them is covered by a site (a name assembled from pieces, e.g. "compose" + "_call",
            is out of reach of both).
  v3-faq    FAQ.md: each row of the table that has a `Runtime module` column (one module per row), with
            its status reduced to a closed set (the FAQ's own status key), never its text.

This module does no network I/O and runs nothing from the guide; `scripts/fetch_guide.py` clones it.
"""

from __future__ import annotations

import ast
import hashlib
import io
import re
import subprocess
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

from . import verdicts as v

GUIDE_REPO = "https://github.com/ItsCogumellum/portaldot-v3-testnet-guide"
GUIDE_COMMIT = "d671a32758573f4d9ab00d891b2dd6a48f36bcbb"  # "Add Portaldot V3.0 FAQ", 2026-10-07

# how a site's names were read
LITERAL, LOCAL, UNRESOLVED = "literal", "local", "unresolved"
TABLE, EXPLORER, FAQ_TABLE = "call-table", "explorer-path", "faq-table"
RUNTIME_CALL, STATE_CALL = "runtime_call", "state_call"

# the FAQ's status key, as a closed set (its wording is not reproduced)
FAQ_DOCUMENTED, FAQ_LISTED, FAQ_AWAITING, FAQ_UNCLASSIFIED = "documented", "listed", "awaiting", "unclassified"
FAQ_STATUSES = (FAQ_DOCUMENTED, FAQ_LISTED, FAQ_AWAITING, FAQ_UNCLASSIFIED)

SDK_METHODS = ("compose_call", "runtime_call", "rpc_request")


@dataclass(frozen=True)
class Site:
    """One place in the guide that names a call, runtime API or module."""
    source: str                    # verdicts.V3_GUIDE | V3_DEMO | V3_FAQ
    kind: str                      # verdicts.CALL | RUNTIME_API | MODULE
    pallet: str | None             # as written; None = could not be resolved
    calls: tuple[str, ...]         # as written; ("",) for a module row; names seen at an unresolved site
    ref: str                       # "<file>:<line>"
    how: str                       # literal | local | unresolved | call-table | explorer-path | ...
    args: tuple[str, ...] | None = None  # demo: the call_params keys as written; None = not static
    status: str = ""               # FAQ rows: one of FAQ_STATUSES

    @property
    def resolved(self) -> bool:
        return self.how != UNRESOLVED and self.pallet is not None and bool(self.calls)


@dataclass
class GuideResult:
    sites: list[Site] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256 of the bytes parsed
    awaiting_lines: list[int] = field(default_factory=list)  # FAQ lines with a bold "awaiting …" status
    unaccounted: list[str] = field(default_factory=list)  # refs that look like call documentation, no site

    def count(self, source: str, how: str | None = None) -> int:
        return sum(1 for s in self.sites if s.source == source and (how is None or s.how == how))


# ---------------------------------------------------------------- demo/*.py (AST only)

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _enclosing_scopes(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """node -> the innermost function (or the module) it sits in."""
    scope_of: dict[ast.AST, ast.AST] = {}

    def visit(node: ast.AST, scope: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            scope_of[child] = scope
            visit(child, child if isinstance(child, SCOPES) else scope)

    visit(tree, tree)
    return scope_of


def _bound_names(node: ast.AST) -> list[str]:
    """Names `node` binds other than by `name = <value>` / `name: T = <value>`."""
    if isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.NamedExpr, ast.comprehension)):
        targets = [node.target]
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in node.items if item.optional_vars is not None]
    elif isinstance(node, ast.Assign):
        targets = [t for t in node.targets if not isinstance(t, ast.Name)]  # a, b = …
    elif isinstance(node, ast.Delete):
        targets = node.targets
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in node.names]
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        return list(node.names)
    elif isinstance(node, ast.ExceptHandler):
        return [node.name] if node.name else []
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
        return [node.name] if node.name else []
    elif isinstance(node, ast.MatchMapping):
        return [node.rest] if node.rest else []
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    else:
        return []
    return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]


def _scope_assignments(scope: ast.AST, name: str) -> list[ast.expr]:
    """Values bound to `name` directly in `scope` (nested function bodies excluded). A binding that is
    not a plain assignment yields a sentinel Name, which never resolves to a literal."""
    values: list[ast.expr] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in child.targets):
                values.append(child.value)
            elif isinstance(child, ast.AnnAssign) and child.value is not None and \
                    isinstance(child.target, ast.Name) and child.target.id == name:
                values.append(child.value)
            if name in _bound_names(child):
                values.append(ast.Name(id=name))
            if not isinstance(child, SCOPES + (ast.ClassDef,)):
                visit(child)

    visit(scope)
    if isinstance(scope, ast.Module) and any(isinstance(n, ast.Global) and name in n.names for n in ast.walk(scope)):
        values.append(ast.Name(id=name))  # rebound from inside some function
    return values


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
        if isinstance(scope, SCOPES) and expr.id in {a.arg for a in ast.walk(scope.args) if isinstance(a, ast.arg)}:
            return [], UNRESOLVED  # a parameter: the caller decides
        out: list[str] = []
        for value in _scope_assignments(scope, expr.id):
            lit = _literal(value)
            if lit is None:
                return [], UNRESOLVED
            out += [x for x in lit if x not in out]
        if out:
            return out, LOCAL
    return [], UNRESOLVED


def _arg(call: ast.Call, position: int, keyword: str) -> tuple[bool, ast.expr | None]:
    """(given, expression) for one argument; `given` is False when it is omitted. `*args` / `**kw` make
    every later argument unknown (given, None)."""
    for kw in call.keywords:
        if kw.arg == keyword:
            return True, kw.value
    if any(kw.arg is None for kw in call.keywords):
        return True, None
    for i, arg in enumerate(call.args):
        if isinstance(arg, ast.Starred):
            return True, None
        if i == position:
            return True, arg
    return False, None


def _param_keys(given: bool, expr: ast.expr | None) -> tuple[str, ...] | None:
    """The call_params keys; an omitted or None call_params is zero arguments; anything dynamic is None."""
    if not given or (isinstance(expr, ast.Constant) and expr.value is None):
        return ()
    if isinstance(expr, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in expr.keys):
        return tuple(k.value for k in expr.keys)
    return None


def _method_of(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute) and call.func.attr in SDK_METHODS:
        return call.func.attr
    if isinstance(call.func, ast.Name) and call.func.id in SDK_METHODS:
        return call.func.id
    return None


def _lines(node: ast.AST) -> range:
    return range(node.lineno, (node.end_lineno or node.lineno) + 1)


@dataclass
class DemoScan:
    sites: list[Site]
    covered_lines: set[int]   # lines whose SDK-method tokens a site (or an ignored plain RPC) accounts for


def scan_demo(text: str, file: str) -> DemoScan:
    """Call sites in one demo file, in source order, plus the lines they account for."""
    tree = ast.parse(text, filename=file)
    scope_of = _enclosing_scopes(tree)
    sites: list[Site] = []
    covered: set[int] = set()
    call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}

    def unresolved(node: ast.AST, method: str) -> None:
        kind = v.CALL if method == "compose_call" else v.RUNTIME_API
        sites.append(Site(source=v.V3_DEMO, kind=kind, pallet=None, calls=(), ref=f"{file}:{node.lineno}",
                          how=UNRESOLVED))
        defs = (ast.FunctionDef, ast.AsyncFunctionDef)
        covered.update([node.lineno] if isinstance(node, defs) else _lines(node))  # a def: its name line only

    consumed: set[int] = set()  # string constants already accounted for by a getattr site
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
            name_expr = _arg(node, 1, "name")[1]
            names, how = _strings(name_expr, scope_of.get(node, tree))
            hit = [n for n in names if n in SDK_METHODS]
            if hit or how == UNRESOLVED:  # getattr(obj, "compose_call") / getattr(obj, name): not static
                unresolved(node, hit[0] if hit else "compose_call")
                consumed.update(id(n) for n in ast.walk(node) if isinstance(n, ast.Constant))
            continue
        if isinstance(node, ast.Constant) and node.value in SDK_METHODS and id(node) not in consumed:
            unresolved(node, node.value)  # "compose_call" as data: methodcaller, __dict__[…], a dispatch table
            continue
        if not isinstance(node, ast.Call):
            # any other reference to an SDK method name: an alias, a definition, an import
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else \
                node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
            if name in SDK_METHODS and id(node) not in call_funcs:
                unresolved(node, name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if {alias.name.split(".")[-1], alias.asname} & set(SDK_METHODS):
                        unresolved(node, alias.name.split(".")[-1] if alias.name.split(".")[-1] in SDK_METHODS
                                   else alias.asname)
            continue
        method = _method_of(node)
        if method is None:
            continue
        scope = scope_of.get(node, tree)
        ref = f"{file}:{node.lineno}"
        if method == "compose_call":
            modules, how_m = _strings(_arg(node, 0, "call_module")[1], scope)
            funcs, how_f = _strings(_arg(node, 1, "call_function")[1], scope)
            pallet = modules[0] if len(modules) == 1 and how_m != UNRESOLVED else None
            how = UNRESOLVED if UNRESOLVED in (how_m, how_f) or pallet is None else (
                LOCAL if LOCAL in (how_m, how_f) else LITERAL)
            sites.append(Site(source=v.V3_DEMO, kind=v.CALL, pallet=pallet, calls=tuple(funcs) if pallet else (),
                              ref=ref, how=how, args=_param_keys(*_arg(node, 2, "call_params"))))
        elif method == "runtime_call":
            apis, how_a = _strings(_arg(node, 0, "api")[1], scope)
            methods, how_m = _strings(_arg(node, 1, "method")[1], scope)
            pallet = apis[0] if len(apis) == 1 and how_a != UNRESOLVED else None
            ok = pallet is not None and methods and how_m != UNRESOLVED
            sites.append(Site(source=v.V3_DEMO, kind=v.RUNTIME_API, pallet=pallet if ok else None,
                              calls=tuple(methods) if ok else (), ref=ref, how=RUNTIME_CALL if ok else UNRESOLVED))
        else:  # rpc_request
            rpc, how_r = _strings(_arg(node, 0, "method")[1], scope)
            if how_r != UNRESOLVED and "state_call" not in rpc:
                covered.update(_lines(node.func))  # a plain RPC (chain_getBlockHash, …): not a runtime API
                continue
            params = _arg(node, 1, "params")[1]
            first = params.elts[0] if isinstance(params, (ast.List, ast.Tuple)) and params.elts else None
            names, how_n = _strings(first, scope) if how_r != UNRESOLVED and rpc == ["state_call"] else ([], UNRESOLVED)
            api, sep, fn = names[0].partition("_") if len(names) == 1 and how_n != UNRESOLVED else ("", "", "")
            ok = bool(sep and api and fn)
            sites.append(Site(source=v.V3_DEMO, kind=v.RUNTIME_API, pallet=api if ok else None,
                              calls=(fn,) if ok else (), ref=ref, how=STATE_CALL if ok else UNRESOLVED))
        covered.update(_lines(node.func))
    sites.sort(key=lambda s: (int(s.ref.rsplit(":", 1)[1]), s.how, s.calls))
    return DemoScan(sites, covered)


def parse_demo(text: str, file: str) -> list[Site]:
    """Call sites in one demo file, in source order. `file` is the ref prefix, e.g. demo/01_assets_demo.py."""
    return scan_demo(text, file).sites


def unaccounted_references(text: str, file: str, scan: DemoScan | None = None) -> list[str]:
    """Refs of code tokens (not comments or strings) naming an SDK method that no site accounts for."""
    scan = scan or scan_demo(text, file)
    lines = sorted({tok.start[0] for tok in tokenize.generate_tokens(io.StringIO(text).readline)
                    if (tok.type == tokenize.NAME and tok.string in SDK_METHODS)
                    or (tok.type == tokenize.STRING and _string_value(tok.string) in SDK_METHODS)})
    return [f"{file}:{n}" for n in lines if n not in scan.covered_lines]


def _string_value(token: str) -> str | None:
    """The value of a plain string-literal token (parsed, never evaluated as code); None for f-strings."""
    try:
        node = ast.parse(token, mode="eval").body
    except SyntaxError:
        return None
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


# ---------------------------------------------------------------- README.md

HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<num>[\d.]+\s+)?(?P<title>.+?)\s*$")
PALLET_TITLE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
CALL_TABLE_HEADER = re.compile(r"^\|\s*(?:Call|Function|Extrinsic|Method)s?\s*\|", re.IGNORECASE)
TABLE_SEPARATOR = re.compile(r"^\|[\s:|-]+\|?\s*$")
QUOTE_INDENT = re.compile(r"^\s*(?:>\s*)*")
FENCE = re.compile(r"^(?:```|~~~)")
TICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
ONE_TICKED = re.compile(r"^`([A-Za-z_][A-Za-z0-9_]*)`$")
ARROW = r"\s*(?:→|->|>)\s*"
EXPLORER_PATH = re.compile(
    rf"Developer{ARROW}(?P<area>Extrinsics?|Runtime calls?){ARROW}(?P<pallet>[A-Za-z][A-Za-z0-9_]*){ARROW}"
    r"(?P<call>[A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
DEVELOPER_PATH = re.compile(rf"Developer{ARROW}\S", re.IGNORECASE)  # anything that looks like an explorer path


def _cell_names(cell: str) -> list[str] | None:
    """`a` or `a` / `b` -> names; anything else (prose, an unticked name, a call with args) -> None."""
    parts = [p.strip() for p in cell.split("/")]
    matches = [ONE_TICKED.match(p) for p in parts]
    return [m[1] for m in matches] if parts and all(matches) else None


def _plain(line: str) -> str:
    """A line with emphasis and code ticks removed, for path matching."""
    return line.replace("**", "").replace("__", "").replace("`", "")


def scan_readme(text: str, file: str = "README.md") -> tuple[list[Site], list[str]]:
    """Call sites in the README, and refs of lines that look like call documentation but yielded no site
    (a Developer path or a call table inside a code fence, an empty call table, an unreadable path)."""
    sites: list[Site] = []
    unaccounted: list[str] = []
    pallet: str | None = None
    table_pallet: str | None = None
    table_header: str | None = None  # ref of the open call table's header until a row yields a site
    in_table = in_fence = False

    def close_table() -> None:
        nonlocal in_table, table_header
        if table_header:
            unaccounted.append(table_header)
        in_table, table_header = False, None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = QUOTE_INDENT.sub("", raw)
        ref = f"{file}:{lineno}"
        if FENCE.match(line):
            close_table()
            in_fence = not in_fence
            continue
        if in_fence:
            if CALL_TABLE_HEADER.match(line) or DEVELOPER_PATH.search(_plain(line)):
                unaccounted.append(ref)
            continue
        heading = HEADING.match(line)
        if heading:
            close_table()
            level = len(heading["hashes"])
            if level < 3:
                pallet = None
            elif level == 3:  # "### 2.1 Assets": a numbered section names the pallet its call tables belong to
                title = heading["title"]
                pallet = title if heading["num"] and PALLET_TITLE.match(title) else None
            continue
        if CALL_TABLE_HEADER.match(line):
            close_table()
            in_table, table_pallet, table_header = True, pallet, ref
            continue
        if in_table and not line.startswith("|"):
            close_table()
        before = len(sites)
        if in_table and not TABLE_SEPARATOR.match(line):
            table_header = None
            first_cell = line.strip().strip("|").split("|")[0].strip()
            names = _cell_names(first_cell)
            if names and table_pallet:
                sites += [Site(source=v.V3_GUIDE, kind=v.CALL, pallet=table_pallet, calls=(n,), ref=ref, how=TABLE)
                          for n in names]
            else:  # an unreadable cell, or a table under a heading that names no pallet
                sites.append(Site(source=v.V3_GUIDE, kind=v.CALL, pallet=None,
                                  calls=tuple(TICKED.findall(first_cell)), ref=ref, how=UNRESOLVED))
        paths = list(EXPLORER_PATH.finditer(_plain(line)))
        for m in paths:
            kind = v.CALL if m["area"].lower().startswith("extrinsic") else v.RUNTIME_API
            sites.append(Site(source=v.V3_GUIDE, kind=kind, pallet=m["pallet"], calls=(m["call"],), ref=ref,
                              how=EXPLORER))
        if DEVELOPER_PATH.search(_plain(line)) and not paths and len(sites) == before:
            unaccounted.append(ref)
    close_table()
    return sites, unaccounted


def parse_readme(text: str, file: str = "README.md") -> list[Site]:
    return scan_readme(text, file)[0]


# ---------------------------------------------------------------- FAQ.md

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def faq_status(cell: str) -> str:
    """Reduce a status cell to the FAQ's own status key (its text is not kept)."""
    text = cell.strip().lower()
    if text.startswith("documented"):
        return FAQ_DOCUMENTED
    if text.startswith("listed"):
        return FAQ_LISTED
    if "awaiting" in text:
        return FAQ_AWAITING
    return FAQ_UNCLASSIFIED


def parse_faq(text: str, file: str = "FAQ.md") -> tuple[list[Site], list[int]]:
    """Module rows of the `Runtime module` table, and the lines that bold an "awaiting …" status
    (the status-key definition line excluded)."""
    sites: list[Site] = []
    columns: list[str] | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = QUOTE_INDENT.sub("", raw)
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
        names = _cell_names(row.get("runtime module", "")) or []
        status = faq_status(row.get("current status", ""))
        if len(names) == 1:
            sites.append(Site(source=v.V3_FAQ, kind=v.MODULE, pallet=names[0], calls=("",),
                              ref=f"{file}:{lineno}", how=FAQ_TABLE, status=status))
        else:  # zero or several module names in one cell: report it, don't pick one
            sites.append(Site(source=v.V3_FAQ, kind=v.MODULE, pallet=None, calls=tuple(names),
                              ref=f"{file}:{lineno}", how=UNRESOLVED, status=status))
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

    readme_sites, result.unaccounted = scan_readme(read("README.md"), "README.md")
    result.sites += readme_sites
    for path in sorted((guide_dir / "demo").glob("*.py")):
        rel = path.relative_to(guide_dir).as_posix()
        text = read(rel)
        scan = scan_demo(text, rel)
        result.sites += scan.sites
        result.unaccounted += unaccounted_references(text, rel, scan)
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
            "status": site.status}


def site_from_dict(row: dict) -> Site:
    return Site(source=row["source"], kind=row["kind"], pallet=row["pallet"], calls=tuple(row["calls"]),
                ref=row["ref"], how=row["how"], args=tuple(row["args"]) if row["args"] is not None else None,
                status=row.get("status", ""))
