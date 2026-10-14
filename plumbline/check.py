"""`plumbline check`: what in a dApp's code will not work on a target runtime.

Three scans over a directory, each finding stamped with one verdict from `plumbline.verdicts`:

  1. Contract artifacts (`.wasm`, `.contract`): the wasm import section, read from the bytes (never run).
     `seal0`-only modules and modules importing `seal1`/`seal2`/… (the newer contracts ABI) are looked up
     in `COMPAT`, a table of measured cells per target profile. A cell with no measurement behind it is
     COMPAT-UNMEASURED. A module with no `seal*` import is not a contracts module. PolkaVM blobs
     (`.polkavm`, Revive) are UNSCANNED: their import scan is not implemented.
  2. Python call sites: `compose_call` / `runtime_call` / raw `state_call`, read as an AST by the same
     scanner as the V3 guide's demo code (`plumbline.guide.scan_demo`), so pallet and call names are
     matched exactly, as substrate-interface matches them. A site whose names are not static is UNSCANNED.
  3. JavaScript / TypeScript call sites: `<expr>.tx.<pallet>.<call>(…)` (polkadot.js), found after string
     and comment contents are masked. Pallet names are matched with case and underscores ignored and the
     call name is snake_cased, as polkadot.js maps them. Arguments are positional, so only their count
     is compared. `.tx[<expr>]` is UNSCANNED.

Each call site becomes a documented entry and gets its verdict from `plumb.classify` against the target's
metadata and the explicit rename table, so `check` and the ledger can never disagree on a rule. Exit code:
1 if any finding is blocking (EXPECTED-REJECT, TYPE-TRAP, DRIFTED, ABSENT, ARG_DRIFT), else 0. The
output is plain text with no clock time in it, so a stored golden file can be compared byte for byte.
TYPE-TRAP (raw H160 passed where an SS58 account is needed) is not detected yet.
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
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".cache"}
PY_EXT = {".py"}
JS_EXT = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}
ARTIFACT_EXT = {".wasm", ".contract", ".polkavm"}

SEAL0, SEAL_NEWER = "seal0", "seal1+"

# Measured artifact cells per target profile: (verdict, evidence). Anything not listed is COMPAT-UNMEASURED.
COMPAT: dict[tuple[str, str], tuple[str, str]] = {
    ("mainnet-1002", SEAL0): (
        v.UNPROVEN,
        "a seal0-only module instantiated on a local spec-1002 chain running mainnet's exact runtime code "
        "(baseline measurement 2026-10-11, cited, not re-run here; no receipt in this repository)"),
    ("mainnet-1002", SEAL_NEWER): (
        v.EXPECTED_REJECT,
        "an ink! 5.1.1 artifact importing seal1/seal2 functions was rejected with System.Other on a local "
        "spec-1002 chain running mainnet's exact runtime code (baseline measurement 2026-10-11, cited, not "
        "re-run here)"),
}
COMPAT_FIX = {
    ("mainnet-1002", SEAL_NEWER): "rebuild for the rent-era seal0 ABI, or target V3",
}


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
    if path.suffix != ".contract":
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
    if path.suffix == ".polkavm":
        return Finding(rel, "PolkaVM blob", v.UNSCANNED, detail="PolkaVM (Revive) import scan is not implemented")
    try:
        imports = wasm_imports(artifact_wasm(path))
    except WasmError as exc:
        return Finding(rel, "unreadable artifact", v.UNSCANNED, detail=str(exc))
    family = abi_family(imports)
    modules = sorted({m for m, _, _ in imports})
    funcs = sum(1 for _, _, k in imports if k == "func")
    subject = f"imports {'/'.join(modules) or 'nothing'} ({funcs} functions)"
    if family is None:
        return Finding(rel, subject, v.UNSCANNED, detail="no seal* import: not a pallet-contracts module")
    status, evidence = COMPAT.get((target, family), (v.COMPAT_UNMEASURED, f"no measurement of {family} modules "
                                                                        f"on {target} yet"))
    label = "seal0 only" if family == SEAL0 else "seal1+ ABI"
    return Finding(rel, f"{subject}, {label}", status, COMPAT_FIX.get((target, family), ""), evidence)


# ---------------------------------------------------------------- JS / TS call sites

def mask_js(text: str) -> str:
    """The source with comment and string contents replaced by spaces (newlines kept), so a regex over it
    sees code only and line numbers are unchanged. Template-literal `${…}` contents are masked too."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]
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
        elif c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n" and c != "`":
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
        else:
            i += 1
    return "".join(out)


_TX_CALL = re.compile(r"\.tx\s*\.\s*([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
_TX_DYNAMIC = re.compile(r"\.tx\s*(\[|\.\s*[A-Za-z_$][\w$]*\s*\[)")


def _arity(masked: str, open_paren: int) -> int | None:
    """Top-level argument count of the call whose `(` is at `open_paren`; None when it cannot be counted
    (a spread argument, or no closing parenthesis)."""
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
        if depth == 1 and masked.startswith("...", k):
            return None
        if depth >= 1 and not ch.isspace():
            seen = True
    return None


def js_sites(text: str) -> list[tuple[int, str | None, str | None, int | None]]:
    """(line, pallet, call, arity) per `.tx.<pallet>.<call>(` site; pallet None = a dynamic `.tx[…]` site."""
    masked = mask_js(text)
    sites = []
    for m in _TX_CALL.finditer(masked):
        sites.append((masked.count("\n", 0, m.start()) + 1, m.group(1), m.group(2), _arity(masked, m.end() - 1)))
    for m in _TX_DYNAMIC.finditer(masked):
        sites.append((masked.count("\n", 0, m.start()) + 1, None, None, None))
    return sorted(sites, key=lambda s: (s[0], s[1] or "", s[2] or ""))


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
            detail = f"{detail}; {arity} arguments passed, {live} takes {len(live_args)}"
    elif status == v.ARG_DRIFT:
        fix = f"-> live shape {live}({', '.join(live_args)})"
    elif status == v.ARGS_UNCHECKED and arity is not None and live in surface.calls:
        if arity != len(live_args):
            status = v.ARG_DRIFT
            fix = f"-> live shape {live}({', '.join(live_args)})"
            detail = f"{arity} positional arguments passed, {live} takes {len(live_args)}"
        else:
            detail = f"{arity} positional arguments, as {live} takes; names are not in the source"
    elif status == v.ABSENT:
        fix = f"not on {rt.profile}"
    elif status == v.PROVEN:
        fix = f"proven on {rt.profile}"
    if status in (v.UNPROVEN, v.ARGS_UNCHECKED) and not detail:
        detail = f"present on {rt.profile}; no receipt"
    return Finding(rel_ref, written, status, fix, detail or "")


def check(root: Path, rt: plumb.Runtime, surface: plumb.Surface, renames: tuple[Rename, ...] = RENAMES,
          proven: set[str] = frozenset()) -> list[Finding]:
    """Every finding for the files under `root`, ordered by path, then line."""
    rows = plumb.check_renames(renames, rt.profile, surface)
    findings: list[tuple[str, int, Finding]] = []
    files = sorted(p for p in root.rglob("*") if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & SKIP_DIRS))
    for path in files:
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        if ext in ARTIFACT_EXT:
            findings.append((rel, 0, check_artifact(path, rel, rt.profile)))
        elif ext in PY_EXT:
            try:
                sites = guide_mod.scan_demo(path.read_text(encoding="utf-8"), rel).sites
            except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
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
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                findings.append((rel, 0, Finding(rel, "unparsed file", v.UNSCANNED, detail="not UTF-8")))
                continue
            for line, pallet, call, arity in js_sites(text):
                ref = f"{rel}:{line}"
                if pallet is None:
                    findings.append((rel, line, Finding(ref, "dynamic api.tx[...]", v.UNSCANNED,
                                                        detail="names are not static")))
                    continue
                entry = plumb.DocEntry(v.MAINNET_DOCS, v.CALL, pallet, docs_mod.snake(call), None, True, (ref,))
                findings.append((rel, line, _finding(ref, f"{pallet}.{call}", entry, rt, surface, rows, proven,
                                                     exact=False, arity=arity)))
    findings.sort(key=lambda f: (f[0], f[1], f[2].subject))
    return [f for _, _, f in findings]


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.status in BLOCKING for f in findings) else 0


def render(findings: list[Finding], rt: plumb.Runtime, mode: str) -> str:
    """The report as text. No clock time: the same inputs give the same bytes."""
    lines = [f"PLUMBLINE check · target {rt.profile} · {rt.spec_name}/{rt.spec_version} · metadata "
             f"V{rt.metadata_version} sha256 {rt.metadata_hash[:12]}… · {mode}",
             f"  {rt.label}"]
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
    lines.append(f"summary: {len(findings)} findings · "
                 + (", ".join(f"{s} {counts[s]}" for s in v.VERDICTS if s in counts) or "none")
                 + f" · blocking {blocking} · exit {exit_code(findings)}")
    return "\n".join(lines) + "\n"
