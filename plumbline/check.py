"""`plumbline check`: what in a dApp's code will not work on a target runtime.

Three scans over a directory, each finding stamped with one verdict from `plumbline.verdicts`:

  1. Contract artifacts (`.wasm`, `.contract`, also under `target/ink/`): the wasm import section, read
     from the bytes (never run). A module is matched against measured cells only (`artifact_cell`): on
     mainnet-1002 a seal0-only module, and a module importing one of the exact newer-ABI functions a
     rejected artifact imported. Anything else is COMPAT-UNMEASURED. A module with no `seal*` import is
     not a contracts module. PolkaVM blobs (`.polkavm`, Revive) are UNSCANNED (no import scan yet).
  2. Python call sites: `compose_call` / `runtime_call` / raw `state_call`, read as an AST by the same
     scanner as the V3 guide's demo code (`plumbline.guide.scan_demo`), so pallet and call names are
     matched exactly, as substrate-interface matches them. A site whose names are not static is UNSCANNED.
  3. JavaScript / TypeScript call sites: `<expr>.tx.<pallet>.<call>(…)` (polkadot.js), found after string
     and comment contents are masked. Pallet names are matched with case and underscores ignored and the
     call name is snake_cased, as polkadot.js maps them. Arguments are positional, so only their count
     is compared, and not at all when an argument holds a spread or a `<` (a TypeScript type argument
     cannot be told from a comparison). Optional chaining (`api.tx?.x?.y()`) is read. `.tx[<expr>]`, a
     `.tx.<pallet>.<call>` reference that is not called, and a destructured `{ tx }` are UNSCANNED.
     `.vue` / `.svelte` files are listed as UNSCANNED (not parsed).

Each call site becomes a documented entry and gets its verdict from `plumb.classify` against the target's
metadata and the explicit rename table, so `check` and the ledger can never disagree on a rule. Exit code:
1 if any finding is blocking (EXPECTED-REJECT, TYPE-TRAP, DRIFTED, ABSENT, ARG_DRIFT), else 0. The
output is plain text with no clock time in it, so a stored golden file can be compared byte for byte.
TYPE-TRAP (raw H160 passed where an SS58 account is needed) is not detected yet; the report says so.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import docs as docs_mod
from . import guide as guide_mod
from . import plumb
from . import verdicts as v
from .renames import RENAMES, Rename

BLOCKING = (v.EXPECTED_REJECT, v.TYPE_TRAP, v.DRIFTED, v.ABSENT, v.ARG_DRIFT)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".cache"}
PY_EXT = {".py"}
JS_EXT = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx", ".jsx"}
UNPARSED_EXT = {".vue", ".svelte"}
ARTIFACT_EXT = {".wasm", ".contract", ".polkavm"}

SEAL0, SEAL_NEWER = "seal0", "seal1+"

BASELINE = ("baseline measurement 2026-10-11 on a local spec-1002 chain running mainnet's exact runtime code, "
            "made before this repository existed: cited, not re-run here, no receipt in this repository")
# The one rejected artifact measured on mainnet-1002 (an ink! 5.1.1 build) imported these newer-ABI functions.
REJECTED_IMPORTS = {"mainnet-1002": {("seal1", "get_storage"), ("seal2", "set_storage")}}


def artifact_cell(target: str, imports: list[tuple[str, str, str]]) -> tuple[str, str, str]:
    """(verdict, evidence, fix) for a contracts module on a target: measured cells only, never a guess."""
    family = abi_family(imports)
    if target == "mainnet-1002" and family == SEAL0:
        return v.UNPROVEN, f"a seal0-only module instantiated on spec 1002 ({BASELINE})", ""
    if target == "mainnet-1002" and family == SEAL_NEWER:
        hits = sorted({(m, f) for m, f, _ in imports} & REJECTED_IMPORTS[target])
        if hits:
            names = ", ".join(f"{m}.{f}" for m, f in hits)
            return (v.EXPECTED_REJECT,
                    f"imports {names}, as did an ink! 5.1.1 artifact that spec 1002 rejected with System.Other "
                    f"({BASELINE}); the error did not name an import, so which one caused it is inferred",
                    "rebuild for the rent-era seal0 ABI")
        return (v.COMPAT_UNMEASURED, f"newer-ABI imports that no measurement on {target} covers (the one rejected "
                                     f"artifact measured imported other functions)", "")
    return v.COMPAT_UNMEASURED, f"no measurement of {family} modules on {target} yet", ""


@dataclass(frozen=True)
class Finding:
    ref: str          # path[:line], relative to the scanned directory
    subject: str      # Pallet.call as written, or what the artifact imports
    status: str       # one verdict
    fix: str = ""     # the live equivalent or shape, or what to do
    detail: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ---------------------------------------------------------------- wasm artifacts

class WasmError(ValueError):
    """Bytes that are not a wasm module the import scan can read."""


def _uleb(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise WasmError("truncated LEB128")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 35:
            raise WasmError("LEB128 too long")


def _name(data: bytes, pos: int) -> tuple[str, int]:
    n, pos = _uleb(data, pos)
    if pos + n > len(data):
        raise WasmError("truncated name")
    try:
        return data[pos:pos + n].decode("utf-8"), pos + n
    except UnicodeDecodeError as exc:
        raise WasmError("import name is not UTF-8") from exc


def _limits(data: bytes, pos: int) -> int:
    if pos >= len(data):
        raise WasmError("truncated limits")
    flag = data[pos]
    _, pos = _uleb(data, pos + 1)
    if flag & 1:
        _, pos = _uleb(data, pos)
    return pos


IMPORT_KINDS = {0: "func", 1: "table", 2: "memory", 3: "global"}


def wasm_imports(data: bytes) -> list[tuple[str, str, str]]:
    """(module, name, kind) for every import of a wasm module, in order. Reads bytes only."""
    if data[:4] != b"\0asm" or data[4:8] != b"\x01\0\0\0":
        raise WasmError("not a wasm module (magic/version)")
    pos = 8
    while pos < len(data):
        section = data[pos]
        size, body = _uleb(data, pos + 1)
        end = body + size
        if end > len(data):
            raise WasmError("truncated section")
        if section == 2:
            count, p = _uleb(data, body)
            out = []
            for _ in range(count):
                module, p = _name(data, p)
                field, p = _name(data, p)
                if p >= end:
                    raise WasmError("truncated import")
                kind = data[p]
                p += 1
                if kind == 0:
                    _, p = _uleb(data, p)
                elif kind == 1:
                    p = _limits(data, p + 1)  # reftype byte, then limits
                elif kind == 2:
                    p = _limits(data, p)
                elif kind == 3:
                    p += 2  # valtype, mutability
                else:
                    raise WasmError(f"unknown import kind {kind}")
                out.append((module, field, IMPORT_KINDS[kind]))
            if p != end:
                raise WasmError("import section length mismatch")
            return out
        pos = end
    return []


def artifact_wasm(path: Path) -> bytes:
    """The wasm bytes of a `.wasm` file or of a `.contract` bundle (`source.wasm`, 0x-hex)."""
    data = path.read_bytes()
    if path.suffix.lower() != ".contract":
        return data
    try:
        hex_code = json.loads(data)["source"]["wasm"]
        return bytes.fromhex(hex_code[2:] if hex_code.startswith("0x") else hex_code)
    except (ValueError, KeyError, TypeError) as exc:
        raise WasmError(f".contract bundle without a readable source.wasm: {exc}") from exc


def abi_family(imports: list[tuple[str, str, str]]) -> str | None:
    """SEAL0 (seal0 only), SEAL_NEWER (any seal1/seal2/…/__unstable__ import), or None (no seal* import)."""
    modules = {m for m, _, _ in imports}
    seal = {m for m in modules if m.startswith("seal") or m == "__unstable__"}
    if not seal:
        return None
    return SEAL0 if seal == {"seal0"} else SEAL_NEWER


def check_artifact(path: Path, rel: str, target: str) -> Finding:
    if path.suffix.lower() == ".polkavm":
        return Finding(rel, "PolkaVM blob", v.UNSCANNED, detail="PolkaVM (Revive) import scan is not implemented")
    try:
        imports = wasm_imports(artifact_wasm(path))
    except (WasmError, OSError) as exc:
        return Finding(rel, "unreadable artifact", v.UNSCANNED, detail=str(exc))
    family = abi_family(imports)
    modules = sorted({m for m, _, _ in imports})
    funcs = sum(1 for _, _, k in imports if k == "func")
    subject = f"imports {'/'.join(modules) or 'nothing'} ({_n(funcs, 'function')})"
    if family is None:
        return Finding(rel, subject, v.UNSCANNED, detail="no seal* import: not a pallet-contracts module")
    status, evidence, fix = artifact_cell(target, imports)
    label = "seal0 only" if family == SEAL0 else "seal1+ ABI"
    return Finding(rel, f"{subject}, {label}", status, fix, evidence)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


# ---------------------------------------------------------------- JS / TS call sites

REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
REGEX_AFTER_WORDS = {"return", "typeof", "case", "in", "of", "new", "delete", "void", "throw", "yield", "await",
                     "else", "do"}


def _regex_allowed(out: list[str], i: int) -> bool:
    """Whether a `/` at `i` starts a regex literal (not a division), judged by the code before it."""
    k = i - 1
    while k >= 0 and out[k].isspace():
        k -= 1
    if k < 0 or out[k] in REGEX_AFTER:
        return True
    if out[k].isalnum() or out[k] in "_$":
        j = k
        while j >= 0 and (out[j].isalnum() or out[j] in "_$"):
            j -= 1
        return "".join(out[j + 1:k + 1]) in REGEX_AFTER_WORDS
    return False


def mask_js(text: str) -> str:
    """The source with the contents of comments, strings, template-literal text and regex literals replaced
    by spaces (newlines kept), so a regex over it sees code only and line numbers are unchanged. Code inside
    a template literal's `${…}` stays visible."""
    out = list(text)
    n = len(text)
    holes: list[int] = []      # one entry per open `${`: the brace depth inside that expression
    in_template = False

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = text[i]
        if in_template:
            if c == "\\":
                blank(i, i + 2)
                i += 2
            elif c == "`":
                in_template = False
                i += 1
            elif text.startswith("${", i):
                holes.append(0)
                in_template = False
                i += 2
            else:
                blank(i, i + 1)
                i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c in "'\"":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        elif c == "`":
            in_template = True
            i += 1
        elif c == "/" and _regex_allowed(out, i):
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            if j < n and text[j] == "/":
                blank(i + 1, j)
                i = j + 1
            else:
                i += 1  # not a regex literal after all
        elif c == "{" and holes:
            holes[-1] += 1
            i += 1
        elif c == "}" and holes:
            if holes[-1] == 0:
                holes.pop()
                in_template = True
            else:
                holes[-1] -= 1
            i += 1
        else:
            i += 1
    return "".join(out)


_ID = r"([A-Za-z_$][\w$]*)"
_DOT = r"\s*\??\.\s*"                      # `.` or `?.`
_TX = r"(?:\?\.|\.)\s*tx\b"                # `.tx` / `?.tx` as a property
_TX_CALL = re.compile(_TX + _DOT + _ID + _DOT + _ID + r"\s*(?:\?\.\s*)?\(")
_TX_REF = re.compile(_TX + _DOT + _ID + r"(?:" + _DOT + _ID + r")?")
_TX_DYNAMIC = re.compile(_TX + r"\s*(?:\[|" + _DOT + r"[A-Za-z_$][\w$]*\s*\??\.?\s*\[)")
_TX_DESTRUCTURED = re.compile(r"\{[^{}]*\btx\b[^{}]*\}\s*=")


def _arity(masked: str, open_paren: int) -> int | None:
    """Top-level argument count of the call whose `(` is at `open_paren`; None when it cannot be counted
    (a spread, a `<` that may open a TypeScript type argument, or no closing parenthesis)."""
    depth, count, seen = 0, 0, False
    for k in range(open_paren, len(masked)):
        ch = masked[k]
        if ch in "([{":
            depth += 1
            if depth == 1:
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return count + (1 if seen else 0)
        elif ch == "," and depth == 1:
            count += 1
            seen = False
            continue
        if masked.startswith("...", k) or ch == "<":
            return None
        if depth >= 1 and not ch.isspace():
            seen = True
    return None


def js_sites(text: str) -> list[tuple[int, str | None, str | None, int | None, str]]:
    """(line, pallet, call, arity, note) per site. pallet None = a site whose names cannot be read
    statically; `note` then says why (dynamic, reference, destructured)."""
    masked = mask_js(text)

    def line(pos: int) -> int:
        return masked.count("\n", 0, pos) + 1

    sites, called = [], set()
    for m in _TX_CALL.finditer(masked):
        called.add(m.start())
        sites.append((line(m.start()), m.group(1), m.group(2), _arity(masked, m.end() - 1), ""))
    dynamic = set()
    for m in _TX_DYNAMIC.finditer(masked):
        dynamic.add(m.start())
        sites.append((line(m.start()), None, None, None, "dynamic api.tx[...]"))
    for m in _TX_REF.finditer(masked):
        if m.start() not in called and m.start() not in dynamic:
            sites.append((line(m.start()), None, None, None, "api.tx reference, not a call"))
    for m in _TX_DESTRUCTURED.finditer(masked):
        sites.append((line(m.start()), None, None, None, "api.tx destructured"))
    return sorted(sites, key=lambda s: (s[0], s[1] or "", s[2] or "", s[4]))


# ---------------------------------------------------------------- verdicts per site

def _finding(rel_ref: str, written: str, entry: plumb.DocEntry, rt: plumb.Runtime, surface: plumb.Surface,
             renames: list[Rename], proven: set[str], exact: bool, arity: int | None = None) -> Finding:
    status, live, detail = plumb.classify(entry, rt, surface, renames, proven, exact=exact)
    live_args = [name for name, _ in surface.calls.get(live, [])] if live else []
    if detail and detail.startswith("{"):  # ARG_DRIFT detail from classify: say it in words
        d = json.loads(detail)
        detail = f"written with ({', '.join(d['doc_args'])})" + ("; differs only by a leading underscore"
                                                               if d.get("underscore_only") else "")
    fix = ""
    if status == v.DRIFTED:
        fix = f"-> {live}({', '.join(live_args)})"
        if arity is not None and live in surface.calls and arity != len(live_args):
            detail = f"{detail}; {_n(arity, 'argument')} passed, {live} takes {len(live_args)}"
    elif status == v.ARG_DRIFT:
        fix = f"-> live shape {live}({', '.join(live_args)})"
    elif status == v.ARGS_UNCHECKED and arity is not None and live in surface.calls:
        if arity != len(live_args):
            status = v.ARG_DRIFT
            fix = f"-> live shape {live}({', '.join(live_args)})"
            detail = f"{_n(arity, 'positional argument')} passed, {live} takes {len(live_args)}"
        else:
            detail = f"{_n(arity, 'positional argument')}, as {live} takes; names are not in the source"
    elif status == v.ABSENT:
        fix = f"not on {rt.profile}"
    elif status == v.PROVEN:
        fix = f"proven on {rt.profile}"
    if status in (v.UNPROVEN, v.ARGS_UNCHECKED) and not detail:
        detail = f"present on {rt.profile}; no receipt"
    return Finding(rel_ref, written, status, fix, detail or "")


def _skipped(dir_parts: tuple[str, ...]) -> bool:
    """Generated or vendored directories are not scanned, except an ink! build's `target/ink/`."""
    for idx, part in enumerate(dir_parts):
        if part in SKIP_DIRS:
            return True
        if part == "target" and dir_parts[idx + 1:idx + 2] != ("ink",):
            return True
    return False


def check(root: Path, rt: plumb.Runtime, surface: plumb.Surface, renames: tuple[Rename, ...] = RENAMES,
          proven: set[str] = frozenset(), stats: dict | None = None) -> list[Finding]:
    """Every finding for the files under `root`, ordered by path, then line. `stats["files"]` is set to
    the number of files scanned."""
    rows = plumb.check_renames(renames, rt.profile, surface)
    findings: list[tuple[str, int, Finding]] = []
    files = sorted(p for p in root.rglob("*") if p.is_file() and not _skipped(p.relative_to(root).parts[:-1]))
    scanned = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        if ext in ARTIFACT_EXT:
            scanned += 1
            findings.append((rel, 0, check_artifact(path, rel, rt.profile)))
        elif ext in UNPARSED_EXT:
            scanned += 1
            findings.append((rel, 0, Finding(rel, "file not parsed", v.UNSCANNED,
                                             detail=f"{ext} files are not scanned yet")))
        elif ext in PY_EXT:
            scanned += 1
            try:
                sites = guide_mod.scan_demo(path.read_text(encoding="utf-8"), rel).sites
            except (SyntaxError, UnicodeDecodeError, ValueError, OSError) as exc:
                findings.append((rel, 0, Finding(rel, "unparsed file", v.UNSCANNED, detail=type(exc).__name__)))
                continue
            for site in sites:
                line = int(site.ref.rsplit(":", 1)[1])
                if not site.resolved:
                    findings.append((rel, line, Finding(site.ref, "dynamic call", v.UNSCANNED,
                                                        detail="names are not static")))
                    continue
                for call in site.calls:
                    entry = plumb.DocEntry(v.V3_DEMO, site.kind, site.pallet, call, site.args, False, (site.ref,))
                    findings.append((rel, line, _finding(site.ref, f"{site.pallet}.{call}", entry, rt, surface,
                                                         rows, proven, exact=True)))
        elif ext in JS_EXT:
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                findings.append((rel, 0, Finding(rel, "unparsed file", v.UNSCANNED, detail=type(exc).__name__)))
                continue
            for line, pallet, call, arity, note in js_sites(text):
                ref = f"{rel}:{line}"
                if pallet is None:
                    findings.append((rel, line, Finding(ref, note, v.UNSCANNED, detail="names are not static")))
                    continue
                entry = plumb.DocEntry(v.MAINNET_DOCS, v.CALL, pallet, docs_mod.snake(call), None, True, (ref,))
                findings.append((rel, line, _finding(ref, f"{pallet}.{call}", entry, rt, surface, rows, proven,
                                                     exact=False, arity=arity)))
    if stats is not None:
        stats["files"] = scanned
    findings.sort(key=lambda f: (f[0], f[1], f[2].subject))
    return [f for _, _, f in findings]


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.status in BLOCKING for f in findings) else 0


def render(findings: list[Finding], rt: plumb.Runtime, mode: str, files: int | None = None) -> str:
    """The report as text. No clock time: the same inputs give the same bytes."""
    lines = [f"PLUMBLINE check · target {rt.profile} · {rt.spec_name}/{rt.spec_version} · metadata "
             f"V{rt.metadata_version} sha256 {rt.metadata_hash[:12]}… · {mode}",
             f"  {rt.label}",
             "  not checked yet: TYPE-TRAP (a raw H160 passed where an SS58 account is needed)"]
    w_ref = max((len(f.ref) for f in findings), default=0)
    w_sub = max((len(f.subject) for f in findings), default=0)
    w_st = max((len(f.status) for f in findings), default=0)
    for f in findings:
        lines.append(f"{f.ref:<{w_ref}}  {f.subject:<{w_sub}}  {f.status:<{w_st}}  {f.fix}".rstrip())
        if f.detail:
            lines.append(f"{'':<{w_ref}}    {f.detail}")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    blocking = sum(n for s, n in counts.items() if s in BLOCKING)
    scanned = "" if files is None else f" in {_n(files, 'file')} scanned"
    if not findings:
        lines.append(f"nothing to check{scanned}: no call sites or contract artifacts found")
    lines.append(f"summary: {_n(len(findings), 'finding')}{scanned} · "
                 + (", ".join(f"{s} {counts[s]}" for s in v.VERDICTS if s in counts) or "none")
                 + f" · blocking {blocking} · exit {exit_code(findings)}")
    return "\n".join(lines) + "\n"
