"""The drift ledger: every documented entry, checked against one runtime, gets exactly one verdict.

Inputs are the parsed sources (V3 guide README, its demo code, its FAQ, the mainnet docs) and a runtime
(a stored metadata blob plus the record of where and when it was read). Output is a SQLite ledger with
the schema below and a canonical `ledger.json` export: sorted keys, no whitespace, UTF-8, so that two
runs over the same inputs give byte-identical files and the same blake2-256 hash.

Rules (verdict names and meanings: `plumbline.verdicts`):
  - Pallets are matched by name with case and underscores ignored (`conviction_voting` = `ConvictionVoting`,
    the snake_case and CamelCase spellings of one identifier); calls by exact snake_case name. Demo code
    is matched exactly as written (pallet and call), because the SDK it calls matches exactly. Nothing
    else is matched: a near name is ABSENT, not a guess.
  - An absent call or module is DRIFTED only through an explicit row in `plumbline.renames` for the same
    profile, checked against the runtime (I3). The live side of every applied rename gets its own row:
    PROVEN with a receipt, else UNPROVEN.
  - Arguments: the mainnet docs are positional, so names are compared in order; the demo code passes
    keyword `call_params`, so names are compared as a set. A source with no arguments is ARGS-UNCHECKED.
  - PROVEN needs a receipt for this runtime (its `profile` and `spec_version` must be the runtime's, else
    plumb refuses it) with a tx hash, a block hash, success, and a read-back whose
    decoded call is the same pallet.call (I1). Without one a present call is UNPROVEN / ARGS-UNCHECKED.
  - A ledger holds at most one runtime per profile; every verdict row is stored against it and exported
    with its profile, spec version and metadata hash (I2, I5). Re-measuring means rebuilding.
  - The interim stand-in chain is test input only: `plumb` refuses it unless a test asks explicitly.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import diff as diff_mod
from . import docs as docs_mod
from . import guide as guide_mod
from . import metadata as md
from . import verdicts as v
from .renames import RENAMES, Rename

FORMAT = "plumbline-ledger/1"
UNRESOLVED_PREFIX = "?"  # call name of a documented entry the parser could not resolve
STANDIN_PROFILE = "interim-sdk"


def _in(values) -> str:
    return "(" + ",".join(f"'{x}'" for x in values) + ")"


SCHEMA = f"""
CREATE TABLE runtime   (id INTEGER PRIMARY KEY, profile TEXT NOT NULL UNIQUE, label TEXT NOT NULL, endpoint TEXT,
                        spec_name TEXT NOT NULL, spec_version INT NOT NULL, tx_version INT, metadata_version INT NOT NULL,
                        metadata_hash TEXT NOT NULL, code_hash TEXT, block_hash TEXT, fetched_at TEXT NOT NULL,
                        report_state TEXT CHECK (report_state IS NULL OR report_state IN {_in(v.REPORT_STATES)}));
CREATE TABLE live_call (runtime_id INT NOT NULL REFERENCES runtime(id), pallet TEXT NOT NULL, call TEXT NOT NULL,
                        args_json TEXT NOT NULL, PRIMARY KEY (runtime_id, pallet, call));
CREATE TABLE live_pallet (runtime_id INT NOT NULL REFERENCES runtime(id), pallet TEXT NOT NULL,
                        PRIMARY KEY (runtime_id, pallet));
CREATE TABLE doc_call  (source TEXT NOT NULL CHECK (source IN {_in(v.SOURCES)}),
                        kind TEXT NOT NULL CHECK (kind IN {_in(v.DOC_KINDS)}),
                        pallet TEXT NOT NULL, call_snake TEXT NOT NULL,          -- call_snake '' = module-level (FAQ)
                        signature TEXT, args_json TEXT, args_ordered INT NOT NULL,
                        source_ref TEXT NOT NULL, refs_json TEXT NOT NULL, note TEXT NOT NULL,
                        PRIMARY KEY (source, kind, pallet, call_snake));
CREATE TABLE rename    (pallet TEXT NOT NULL, doc_call TEXT NOT NULL, live_call TEXT NOT NULL, profile TEXT NOT NULL,
                        evidence TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN {_in(v.RENAME_KINDS)}),
                        module INT NOT NULL CHECK (module IN (0,1)),
                        PRIMARY KEY (profile, module, pallet, doc_call));
CREATE TABLE verdict   (runtime_id INT NOT NULL REFERENCES runtime(id),
                        source TEXT NOT NULL CHECK (source IN {_in(v.SOURCES + (v.RUNTIME,))}),
                        kind TEXT NOT NULL CHECK (kind IN {_in(v.DOC_KINDS)}),
                        pallet TEXT NOT NULL, call TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN {_in(v.VERDICTS)}),
                        live_equivalent TEXT, detail TEXT, receipt_id INT REFERENCES receipt(id),
                        PRIMARY KEY (runtime_id, source, kind, pallet, call));
CREATE TABLE diff_row  (from_runtime INT REFERENCES runtime(id), to_runtime INT REFERENCES runtime(id),
                        pallet TEXT, item TEXT,
                        kind TEXT CHECK (kind IN {_in(diff_mod.DIFF_KINDS)}),
                        before_json TEXT, after_json TEXT, receipt_id INT REFERENCES receipt(id),
                        PRIMARY KEY (from_runtime, to_runtime, pallet, item, kind));
CREATE TABLE compat_cell (artifact_sha256 TEXT, source TEXT, toolchain TEXT, profile TEXT,
                        kind TEXT CHECK (kind IN ('artifact','control','imported')),
                        imports_json TEXT, receipt_id INT REFERENCES receipt(id), result TEXT,
                        PRIMARY KEY (artifact_sha256, profile, kind));
CREATE TABLE receipt   (id INTEGER PRIMARY KEY, run_id INT REFERENCES run(id),
                        runtime_id INT NOT NULL REFERENCES runtime(id), side TEXT CHECK (side IN ('substrate','eth')),
                        pallet TEXT NOT NULL, call TEXT NOT NULL, tx_hash TEXT, block_hash TEXT, block_number INT,
                        success INT CHECK (success IN (0,1)), dispatch_error TEXT, events_json TEXT, weight_json TEXT,
                        fee TEXT, readback_ok INT CHECK (readback_ok IN (0,1)), readback_call TEXT);
CREATE TABLE run       (id INTEGER PRIMARY KEY, started_at TEXT, runtime_id INT REFERENCES runtime(id),
                        report_hash TEXT, attest_tx TEXT);
"""
TABLES = ("runtime", "live_call", "live_pallet", "doc_call", "rename", "verdict", "diff_row", "compat_cell",
          "receipt", "run")
RECEIPT_FIELDS = ("side", "pallet", "call", "tx_hash", "block_hash", "block_number", "success", "dispatch_error",
                  "events_json", "weight_json", "fee", "readback_ok", "readback_call")
# I1 in SQL: a substrate receipt for this call, included in a numbered block without a dispatch error, with
# 32-byte 0x-hex tx and block hashes, whose read-back decoded to the same call
PROVEN_RECEIPT = ("runtime_id = ? AND side = 'substrate' AND success = 1 AND dispatch_error IS NULL "
                  "AND readback_ok = 1 AND readback_call = pallet || '.' || call "
                  "AND block_number IS NOT NULL AND length(tx_hash) = 66 AND length(block_hash) = 66 "
                  "AND lower(tx_hash) GLOB '0x[0-9a-f]*' AND lower(block_hash) GLOB '0x[0-9a-f]*' "
                  "AND NOT lower(substr(tx_hash, 3)) GLOB '*[^0-9a-f]*' AND NOT lower(substr(block_hash, 3)) GLOB '*[^0-9a-f]*'")


class LedgerError(ValueError):
    """Inputs the ledger refuses: an inconsistent runtime record, a rename row its runtime contradicts,
    a surface from another blob, a stand-in runtime, or a second runtime for one profile."""


# ---------------------------------------------------------------- inputs

@dataclass(frozen=True)
class DocEntry:
    source: str
    kind: str
    pallet: str                         # as documented
    call: str                           # snake_case; '' for a module; '?<ref>#<n>' when unresolved
    args: tuple[str, ...] | None        # argument names; None = the source gives none
    ordered: bool                       # compare argument names in order (positional) or as a set (keywords)
    refs: tuple[str, ...]               # every place the source names it
    signature: str | None = None
    note: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.pallet) and not self.call.startswith(UNRESOLVED_PREFIX)


@dataclass(frozen=True)
class Runtime:
    profile: str
    label: str
    endpoint: str | None
    spec_name: str
    spec_version: int
    tx_version: int | None
    metadata_version: int
    metadata_hash: str                  # sha256 of the metadata blob
    code_hash: str | None
    block_hash: str | None
    fetched_at: str
    report_state: str | None = None     # verdicts.CACHED when measured from a stored blob


@dataclass
class Surface:
    calls: dict[str, list[tuple[str, str]]]   # "Pallet.call" -> [(arg, type)]
    pallets: list[str] = field(default_factory=list)
    metadata_hash: str = ""                   # sha256 of the blob the surface was decoded from


def surface_from_blob(blob: bytes, preset: str | None) -> Surface:
    decoded = md.decode_metadata(blob, preset)
    return Surface(md.call_surface(decoded), md.pallet_names(decoded), md.sha256_hex(blob))


def entries_from_guide(sites: list[guide_mod.Site]) -> list[DocEntry]:
    """One entry per (source, kind, pallet, call); a site with two candidate calls names both, and says so.
    Prose call names are snake_cased (an explorer path may spell `transferKeepAlive`); demo names are kept
    exactly as the code passes them to the SDK. Sites of one call that
    disagree on their argument names give no arguments (ARGS-UNCHECKED), with a note. Every unresolved
    site is its own entry."""
    grouped: dict[tuple, list[guide_mod.Site]] = {}
    out: list[DocEntry] = []
    seen_refs: dict[str, int] = {}
    for site in sites:
        if not site.resolved:
            n = seen_refs[site.ref] = seen_refs.get(site.ref, 0) + 1
            names = f"; names {', '.join(c for c in site.calls if c)}" if any(site.calls) else ""
            out.append(DocEntry(site.source, site.kind, site.pallet or "", f"{UNRESOLVED_PREFIX}{site.ref}#{n}",
                                None, False, (site.ref,), note=f"unresolved {site.how}{names}"))
            continue
        for call in site.calls:
            name = docs_mod.snake(call) if site.kind == v.CALL and site.source != v.V3_DEMO else call
            grouped.setdefault((site.source, site.kind, site.pallet, name), []).append(site)
    for (source, kind, pallet, call), group in grouped.items():
        arg_sets = {s.args for s in group}
        args = next(iter(arg_sets)) if len(arg_sets) == 1 else None
        notes = sorted({f"faq status {s.status}" for s in group if s.status} |
                       {f"one of {len(s.calls)} candidates ({s.how}) at {s.ref}" for s in group if len(s.calls) > 1})
        if len(arg_sets) > 1:
            notes.append("sites disagree on argument names")
        out.append(DocEntry(source, kind, pallet, call, args, False, tuple(s.ref for s in group),
                            note="; ".join(notes)))
    return out


def entries_from_mainnet_docs(rows: list[dict]) -> list[DocEntry]:
    """From docs comparison rows (`docs.compare(...)["rows"]` or the committed evidence/docs_1002.json)."""
    out = []
    for row in rows:
        pallet, call = row["doc_call"].split(".")
        args = None if row["doc_args"] is None else tuple(name for name, _ in row["doc_args"])
        signature = None if row["doc_args"] is None else \
            f"{call}({', '.join(f'{n}: {t}' for n, t in row['doc_args'])})"
        out.append(DocEntry(v.MAINNET_DOCS, v.CALL, pallet, docs_mod.snake(call), args, True,
                            (row["source_ref"],), signature=signature))
    return out


def runtime_from_record(record: dict, blob: bytes, label: str, report_state: str | None = None) -> Runtime:
    """A runtime from an evidence record written by scripts/record_metadata.py, checked against the blob."""
    sha = hashlib.sha256(blob).hexdigest()
    if record.get("metadata_sha256") != sha:
        raise LedgerError(f"runtime record is for metadata {record.get('metadata_sha256')}, blob is {sha}")
    return Runtime(profile=record["profile"], label=label, endpoint=record.get("endpoint"),
                   spec_name=record["spec_name"], spec_version=record["spec_version"],
                   tx_version=record.get("tx_version"), metadata_version=record["metadata_version"],
                   metadata_hash=sha, code_hash=record.get("code_hash"), block_hash=record.get("block_hash"),
                   fetched_at=record["fetched_at"], report_state=report_state)


# ---------------------------------------------------------------- verdicts

def _norm(name: str) -> str:
    return name.replace("_", "").lower()


def _live_pallet(name: str, surface: Surface, exact: bool = False) -> str | None:
    if exact:
        return name if name in surface.pallets else None
    return next((p for p in surface.pallets if _norm(p) == _norm(name)), None)


def check_renames(renames: tuple[Rename, ...], profile: str, surface: Surface) -> list[Rename]:
    """The rows for `profile`, each verified against the runtime: the documented side is absent and, for a
    rename or substitute, the live side is present. A `none` row only records that nothing replaces it."""
    rows = [r for r in renames if r.profile == profile]
    for r in rows:
        label = f"rename {'module ' if r.module else ''}{r.pallet}.{r.doc_call} -> {r.live_call or '-'}"
        if not r.evidence.strip():
            raise LedgerError(f"{label}: a rename row needs evidence")
        doc_present = _live_pallet(r.doc_call, surface) if r.module else f"{r.pallet}.{r.doc_call}" in surface.calls
        if doc_present:
            raise LedgerError(f"{label}: the documented {'module' if r.module else 'call'} exists in {profile}")
        if r.kind == v.NONE:
            continue
        live_present = r.pallet in surface.pallets if r.module else f"{r.pallet}.{r.live_call}" in surface.calls
        if not live_present:
            raise LedgerError(f"{label}: no live {r.pallet if r.module else r.live_call} in {profile}")
    return rows


def classify(entry: DocEntry, rt: Runtime, surface: Surface, renames: list[Rename],
             proven: set[str] = frozenset(), exact: bool | None = None) -> tuple[str, str | None, str | None]:
    """(status, live equivalent, detail) for one documented entry on one runtime. `exact` = match the
    pallet name exactly (default: only for demo code, which the SDK matches exactly)."""
    exact = entry.source == v.V3_DEMO if exact is None else exact
    if not entry.resolved:
        return v.UNKNOWN_DOC, None, entry.note or "unresolved"
    if entry.kind == v.RUNTIME_API:
        return v.UNKNOWN_DOC, None, (f"metadata V{rt.metadata_version} lists no runtime APIs"
                                     if rt.metadata_version < 15 else "runtime API lookup not implemented")
    pallet = _live_pallet(entry.pallet, surface, exact=exact)
    if entry.kind == v.MODULE:
        if pallet:
            return v.UNPROVEN, pallet, entry.note or None
        rename = next((r for r in renames if r.module and _norm(r.doc_call) == _norm(entry.pallet)), None)
        return _absent(rename, rename.pallet if rename else None, "pallet absent")
    live_key = f"{pallet}.{entry.call}" if pallet else None
    if live_key not in surface.calls:
        rename = next((r for r in renames if not r.module and r.doc_call == entry.call and
                       (r.pallet == entry.pallet if exact else _norm(r.pallet) == _norm(entry.pallet))), None)
        live = f"{rename.pallet}.{rename.live_call}" if rename else None
        status, live, detail = _absent(rename, live, "pallet absent" if pallet is None else "call absent")
        if status == v.DRIFTED and entry.args is not None and live in surface.calls:
            live_names = [name for name, _ in surface.calls[live]]
            if not _same_args(entry, live_names):  # the fix line must show the live shape too
                detail += f"; args differ: documented {list(entry.args)}, live {live_names}"
        return status, live, detail
    live_names = [name for name, _ in surface.calls[live_key]]
    if entry.args is not None:
        if not _same_args(entry, live_names):
            detail = {"doc_args": list(entry.args), "live_args": live_names}
            if [n.lstrip("_") for n in entry.args] == [n.lstrip("_") for n in live_names]:
                detail["underscore_only"] = True
            return v.ARG_DRIFT, live_key, json.dumps(detail, sort_keys=True, separators=(",", ":"))
    if live_key in proven:
        return v.PROVEN, live_key, None
    if entry.args is None:
        return v.ARGS_UNCHECKED, live_key, entry.note or None
    return v.UNPROVEN, live_key, entry.note or None


def _same_args(entry: DocEntry, live_names: list[str]) -> bool:
    return list(entry.args) == live_names if entry.ordered else sorted(entry.args) == sorted(live_names)


def _absent(rename: Rename | None, live: str | None, why: str) -> tuple[str, str | None, str | None]:
    if rename is None:
        return v.ABSENT, None, why
    if rename.kind == v.NONE:
        return v.ABSENT, None, f"{why}; none: {rename.evidence}"
    return v.DRIFTED, live, f"{rename.kind}: {rename.evidence}"


# ---------------------------------------------------------------- the SQLite ledger

def open_ledger(path: str | Path = ":memory:") -> sqlite3.Connection:
    """A ledger database with the schema applied (a new file, or an in-memory one)."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime'").fetchone():
        conn.executescript(SCHEMA)
    return conn


def add_entries(conn: sqlite3.Connection, entries: list[DocEntry]) -> None:
    for e in entries:
        conn.execute("INSERT INTO doc_call VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (e.source, e.kind, e.pallet, e.call, e.signature,
                      None if e.args is None else json.dumps(list(e.args)), int(e.ordered),
                      e.refs[0], json.dumps(sorted(e.refs)), e.note))


def _entries(conn: sqlite3.Connection) -> list[DocEntry]:
    rows = conn.execute("SELECT source, kind, pallet, call_snake, args_json, args_ordered, refs_json, signature, note "
                        "FROM doc_call ORDER BY source, kind, pallet, call_snake").fetchall()
    return [DocEntry(s, k, p, c, None if a is None else tuple(json.loads(a)), bool(o), tuple(json.loads(r)), sig, n)
            for s, k, p, c, a, o, r, sig, n in rows]


def plumb(conn: sqlite3.Connection, rt: Runtime, surface: Surface, renames: tuple[Rename, ...] = RENAMES,
          receipts: list[dict] = (), allow_standin: bool = False) -> int:
    """Store the runtime, its call surface and its canary receipts, then a verdict for every documented
    entry, every live side of an applied rename, and every undocumented live call. Returns the runtime id."""
    if not allow_standin and (rt.profile == STANDIN_PROFILE or md.KNOWN_BLOBS.get(rt.metadata_hash) == STANDIN_PROFILE):
        raise LedgerError("the interim-sdk chain is a stand-in, not V3: test input only, no verdicts are computed from it")
    if surface.metadata_hash != rt.metadata_hash:
        raise LedgerError(f"surface is from metadata {surface.metadata_hash or '(unknown)'}, runtime is {rt.metadata_hash}")
    if conn.execute("SELECT 1 FROM runtime WHERE profile = ?", (rt.profile,)).fetchone():
        raise LedgerError(f"the ledger already holds a {rt.profile} runtime; rebuild it to re-measure")
    rows = check_renames(renames, rt.profile, surface)
    for receipt in receipts:  # a receipt proves a call only on the runtime it was sent to
        if receipt.get("profile") != rt.profile or receipt.get("spec_version") != rt.spec_version:
            raise LedgerError(f"receipt {receipt.get('tx_hash')} is from {receipt.get('profile')}/"
                              f"{receipt.get('spec_version')}, not {rt.profile}/{rt.spec_version}")
    cur = conn.execute("INSERT INTO runtime (profile, label, endpoint, spec_name, spec_version, tx_version, "
                       "metadata_version, metadata_hash, code_hash, block_hash, fetched_at, report_state) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rt.profile, rt.label, rt.endpoint, rt.spec_name, rt.spec_version, rt.tx_version,
                        rt.metadata_version, rt.metadata_hash, rt.code_hash, rt.block_hash, rt.fetched_at,
                        rt.report_state))
    rid = cur.lastrowid
    conn.executemany("INSERT INTO live_pallet VALUES (?,?)", [(rid, p) for p in surface.pallets])
    for key, args in surface.calls.items():
        pallet, call = key.split(".", 1)
        conn.execute("INSERT INTO live_call VALUES (?,?,?,?)", (rid, pallet, call, json.dumps([list(a) for a in args])))
    for r in rows:
        conn.execute("INSERT INTO rename VALUES (?,?,?,?,?,?,?)",
                     (r.pallet, r.doc_call, r.live_call, r.profile, r.evidence, r.kind, int(r.module)))
    # receipts in content order, so their row ids do not depend on the order they were passed in
    for receipt in sorted(receipts, key=lambda r: json.dumps(r, sort_keys=True, default=str)):
        conn.execute(f"INSERT INTO receipt (runtime_id, {', '.join(RECEIPT_FIELDS)}) VALUES (?{',?' * len(RECEIPT_FIELDS)})",
                     (rid, *(receipt.get(f) for f in RECEIPT_FIELDS)))
    proven = {f"{p}.{c}": i for i, p, c in conn.execute(
        f"SELECT min(id), pallet, call FROM receipt WHERE {PROVEN_RECEIPT} GROUP BY pallet, call", (rid,))}

    def insert(source, kind, pallet, call, status, live, detail):
        receipt = proven.get(live) if status == v.PROVEN else None
        conn.execute("INSERT INTO verdict VALUES (?,?,?,?,?,?,?,?,?)",
                     (rid, source, kind, pallet, call, status, live, detail, receipt))

    covered: set[str] = set()
    live_sides: dict[tuple[str, str], list[str]] = {}
    entries = _entries(conn)
    for e in entries:
        status, live, detail = classify(e, rt, surface, rows, set(proven))
        if e.kind == v.CALL and live:
            covered.add(live)
        if status == v.DRIFTED:
            live_sides.setdefault((e.kind, live), []).append(f"{e.source} {e.pallet}.{e.call}" if e.call
                                                              else f"{e.source} {e.pallet}")
        insert(e.source, e.kind, e.pallet, e.call, status, live, detail)
    for (kind, live), docs in sorted(live_sides.items()):  # I3: the live side of a rename has a verdict too
        pallet, _, call = live.partition(".")
        status = v.PROVEN if kind == v.CALL and live in proven else v.UNPROVEN
        insert(v.RUNTIME, kind, pallet, call, status, live, f"live side of DRIFTED {'; '.join(sorted(docs))}")
    unresolved_calls = sum(1 for e in entries if e.kind == v.CALL and not e.resolved)
    caveat = (f"{unresolved_calls} documented call entries are unresolved; this call may be one of them"
              if unresolved_calls else None)
    for key in sorted(set(surface.calls) - covered):
        pallet, call = key.split(".", 1)
        insert(v.RUNTIME, v.CALL, pallet, call, v.UNDOCUMENTED, key, caveat)
    conn.commit()
    return rid


# ---------------------------------------------------------------- canonical export

def canonical(obj) -> bytes:
    """Sorted keys, no whitespace, UTF-8: the bytes the report hash is taken over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def blake2_256(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def export(conn: sqlite3.Connection, inputs: dict | None = None) -> dict:
    """The ledger as a plain object (row order fixed, no wall-clock time of the export). Receipt ids are
    stable: receipts are stored in content order."""
    cols = ("profile", "label", "endpoint", "spec_name", "spec_version", "tx_version", "metadata_version",
            "metadata_hash", "code_hash", "block_hash", "fetched_at", "report_state")
    stamp = {row[0]: dict(zip(cols, row[1:])) for row in conn.execute(f"SELECT id, {', '.join(cols)} FROM runtime")}

    verdicts, counts, live_calls = [], {}, {}
    for rid, source, kind, pallet, call, status, live, detail, receipt in conn.execute(
            "SELECT runtime_id, source, kind, pallet, call, status, live_equivalent, detail, receipt_id FROM verdict"):
        rt = stamp[rid]
        verdicts.append({"profile": rt["profile"], "spec_name": rt["spec_name"], "spec_version": rt["spec_version"],
                         "metadata_hash": rt["metadata_hash"], "source": source, "kind": kind, "pallet": pallet,
                         "call": call, "status": status, "live_equivalent": live,
                         "detail": json.loads(detail) if detail and detail.startswith("{") else detail,
                         "receipt": receipt})
        per = counts.setdefault(rt["profile"], {}).setdefault(source, {})
        per[status] = per.get(status, 0) + 1
    verdicts.sort(key=lambda r: (r["profile"], r["source"], r["kind"], r["pallet"].lower(), r["pallet"], r["call"]))
    for rid, pallet, call, args in conn.execute("SELECT runtime_id, pallet, call, args_json FROM live_call"):
        live_calls.setdefault(stamp[rid]["profile"], {})[f"{pallet}.{call}"] = json.loads(args)

    doc_calls = [{"source": s, "kind": k, "pallet": p, "call": c, "signature": sig,
                  "args": None if a is None else json.loads(a), "args_ordered": bool(o), "refs": json.loads(r),
                  "note": n}
                 for s, k, p, c, sig, a, o, r, n in conn.execute(
                     "SELECT source, kind, pallet, call_snake, signature, args_json, args_ordered, refs_json, note "
                     "FROM doc_call")]
    doc_calls.sort(key=lambda r: (r["source"], r["kind"], r["pallet"].lower(), r["pallet"], r["call"]))
    renames = [dict(zip(("pallet", "doc_call", "live_call", "profile", "evidence", "kind", "module"), row))
               for row in conn.execute("SELECT pallet, doc_call, live_call, profile, evidence, kind, module FROM rename")]
    for r in renames:
        r["module"] = bool(r["module"])
    renames.sort(key=lambda r: (r["profile"], r["module"], r["pallet"], r["doc_call"]))
    receipts = [{"profile": stamp[row[0]]["profile"], "id": row[1], **dict(zip(RECEIPT_FIELDS, row[2:]))}
                for row in conn.execute(f"SELECT runtime_id, id, {', '.join(RECEIPT_FIELDS)} FROM receipt ORDER BY id")]
    return {
        "format": FORMAT,
        "vocabulary": {"verdicts": list(v.VERDICTS), "report_states": list(v.REPORT_STATES)},
        "runtimes": sorted(stamp.values(), key=lambda r: r["profile"]),
        "inputs": inputs or {},
        "doc_calls": doc_calls,
        "renames": renames,
        "verdicts": verdicts,
        "counts": counts,
        "live_calls": live_calls,
        "receipts": receipts,
    }


def build(entries: list[DocEntry], runtimes: list[tuple[Runtime, Surface]], inputs: dict | None = None,
          db: str | Path = ":memory:", renames: tuple[Rename, ...] = RENAMES,
          receipts: dict[str, list[dict]] | None = None) -> tuple[bytes, sqlite3.Connection]:
    """Entries × runtimes -> (canonical ledger.json bytes, the open ledger). `db` must not exist yet.
    `receipts` maps a profile name to the canary receipts sent to it; each is checked against its runtime."""
    if str(db) != ":memory:" and Path(db).exists():
        raise LedgerError(f"{db} exists; the ledger is rebuilt from its inputs, never patched in place")
    conn = open_ledger(db)
    add_entries(conn, entries)
    for rt, surface in runtimes:
        plumb(conn, rt, surface, renames, receipts=(receipts or {}).get(rt.profile, []))
    return canonical(export(conn, inputs)), conn
