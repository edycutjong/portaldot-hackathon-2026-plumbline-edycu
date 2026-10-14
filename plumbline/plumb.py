"""The drift ledger: every documented entry, checked against one runtime, gets exactly one verdict.

Inputs are the parsed sources (V3 guide README, its demo code, its FAQ, the mainnet docs) and a runtime
(a stored metadata blob plus the record of where and when it was read). Output is a SQLite ledger with
the schema below and a canonical `ledger.json` export: sorted keys, no whitespace, UTF-8, so that two
runs over the same inputs give byte-identical files and the same blake2-256 hash.

Rules (verdict names and meanings: `plumbline.verdicts`):
  - Pallets are matched by name with case and underscores ignored (`conviction_voting` = `ConvictionVoting`);
    calls by exact snake_case name. Nothing else is matched: a near name is ABSENT, not a guess.
  - An absent call is DRIFTED only through an explicit row in `plumbline.renames` for the same profile (I3).
  - Arguments: the mainnet docs are positional, so names are compared in order; the demo code passes
    keyword `call_params`, so names are compared as a set. A source with no arguments is ARGS-UNCHECKED.
  - PROVEN needs a receipt for this runtime whose read-back decoded to the same call (I1). Without one a
    present call with matching arguments is UNPROVEN.
  - Every verdict row is stored against a runtime row and exported with its profile, spec version and
    metadata hash (I2, I5).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import docs as docs_mod
from . import guide as guide_mod
from . import verdicts as v
from .renames import RENAMES, Rename

FORMAT = "plumbline-ledger/1"
UNRESOLVED_PREFIX = "?"  # call name of a documented entry the parser could not resolve


def _in(values) -> str:
    return "(" + ",".join(f"'{x}'" for x in values) + ")"


SCHEMA = f"""
CREATE TABLE runtime   (id INTEGER PRIMARY KEY, profile TEXT NOT NULL, label TEXT NOT NULL, endpoint TEXT,
                        spec_name TEXT NOT NULL, spec_version INT NOT NULL, tx_version INT, metadata_version INT NOT NULL,
                        metadata_hash TEXT NOT NULL, code_hash TEXT, block_hash TEXT, fetched_at TEXT NOT NULL,
                        UNIQUE (profile, metadata_hash));
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
                        PRIMARY KEY (profile, pallet, doc_call));
CREATE TABLE verdict   (runtime_id INT NOT NULL REFERENCES runtime(id),
                        source TEXT NOT NULL CHECK (source IN {_in(v.SOURCES + (v.RUNTIME,))}),
                        kind TEXT NOT NULL CHECK (kind IN {_in(v.DOC_KINDS)}),
                        pallet TEXT NOT NULL, call TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN {_in(v.VERDICTS)}),
                        live_equivalent TEXT, detail TEXT, receipt_id INT REFERENCES receipt(id),
                        PRIMARY KEY (runtime_id, source, kind, pallet, call));
CREATE TABLE diff_row  (from_runtime INT REFERENCES runtime(id), to_runtime INT REFERENCES runtime(id),
                        pallet TEXT, item TEXT,
                        kind TEXT CHECK (kind IN ('added','removed','renamed','arg-changed','const-changed','event-changed')),
                        before_json TEXT, after_json TEXT, receipt_id INT REFERENCES receipt(id),
                        PRIMARY KEY (from_runtime, to_runtime, pallet, item, kind));
CREATE TABLE compat_cell (artifact_sha256 TEXT, source TEXT, toolchain TEXT, profile TEXT,
                        kind TEXT CHECK (kind IN ('artifact','control','imported')),
                        imports_json TEXT, receipt_id INT REFERENCES receipt(id), result TEXT,
                        PRIMARY KEY (artifact_sha256, profile, kind));
CREATE TABLE receipt   (id INTEGER PRIMARY KEY, run_id INT REFERENCES run(id),
                        runtime_id INT NOT NULL REFERENCES runtime(id), side TEXT CHECK (side IN ('substrate','eth')),
                        pallet TEXT, call TEXT, tx_hash TEXT, block_hash TEXT, block_number INT, success INT,
                        dispatch_error TEXT, events_json TEXT, weight_json TEXT, fee TEXT, readback_ok INT);
CREATE TABLE run       (id INTEGER PRIMARY KEY, started_at TEXT, runtime_id INT REFERENCES runtime(id),
                        report_hash TEXT, attest_tx TEXT);
"""
TABLES = ("runtime", "live_call", "live_pallet", "doc_call", "rename", "verdict", "diff_row", "compat_cell",
          "receipt", "run")


class LedgerError(ValueError):
    """Inputs the ledger refuses: an inconsistent runtime record or a rename row its runtime contradicts."""


# ---------------------------------------------------------------- inputs

@dataclass(frozen=True)
class DocEntry:
    source: str
    kind: str
    pallet: str                         # as documented
    call: str                           # snake_case; '' for a module; '?<ref>' when unresolved
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


@dataclass
class Surface:
    calls: dict[str, list[tuple[str, str]]]   # "Pallet.call" -> [(arg, type)]
    pallets: list[str] = field(default_factory=list)


def entries_from_guide(sites: list[guide_mod.Site]) -> list[DocEntry]:
    """One entry per (source, kind, pallet, call); a site with two candidate calls names both. Sites of
    one call that disagree on their argument names give no arguments (ARGS-UNCHECKED), with a note."""
    grouped: dict[tuple, list[guide_mod.Site]] = {}
    out: list[DocEntry] = []
    for site in sites:
        if not site.resolved:
            out.append(DocEntry(site.source, site.kind, site.pallet or "", f"{UNRESOLVED_PREFIX}{site.ref}",
                                None, False, (site.ref,), note=f"unresolved {site.how}"))
            continue
        for call in site.calls:
            grouped.setdefault((site.source, site.kind, site.pallet, call), []).append(site)
    for (source, kind, pallet, call), group in grouped.items():
        arg_sets = {s.args for s in group}
        args = next(iter(arg_sets)) if len(arg_sets) == 1 else None
        notes = sorted({s.note for s in group if s.note})
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


def runtime_from_record(record: dict, blob: bytes, label: str) -> Runtime:
    """A runtime from an evidence record written by scripts/record_metadata.py, checked against the blob."""
    sha = hashlib.sha256(blob).hexdigest()
    if record.get("metadata_sha256") != sha:
        raise LedgerError(f"runtime record is for metadata {record.get('metadata_sha256')}, blob is {sha}")
    return Runtime(profile=record["profile"], label=label, endpoint=record.get("endpoint"),
                   spec_name=record["spec_name"], spec_version=record["spec_version"],
                   tx_version=record.get("tx_version"), metadata_version=record["metadata_version"],
                   metadata_hash=sha, code_hash=record.get("code_hash"), block_hash=record.get("block_hash"),
                   fetched_at=record["fetched_at"])


# ---------------------------------------------------------------- verdicts

def _norm(name: str) -> str:
    return name.replace("_", "").lower()


def check_renames(renames: tuple[Rename, ...], profile: str, surface: Surface) -> list[Rename]:
    """The rows for `profile`, each verified against the runtime: documented side absent, live side present."""
    rows = [r for r in renames if r.profile == profile]
    for r in rows:
        if f"{r.pallet}.{r.doc_call}" in surface.calls:
            raise LedgerError(f"rename {r.pallet}.{r.doc_call} -> {r.live_call}: the documented call exists in {profile}")
        if f"{r.pallet}.{r.live_call}" not in surface.calls:
            raise LedgerError(f"rename {r.pallet}.{r.doc_call} -> {r.live_call}: no live {r.live_call} in {profile}")
    return rows


def classify(entry: DocEntry, rt: Runtime, surface: Surface, renames: list[Rename],
             proven: set[str] = frozenset()) -> tuple[str, str | None, str | None]:
    """(status, live equivalent, detail) for one documented entry on one runtime."""
    if not entry.resolved:
        return v.UNKNOWN_DOC, None, entry.note or "unresolved"
    pallet = next((p for p in surface.pallets if _norm(p) == _norm(entry.pallet)), None)
    if entry.kind == v.RUNTIME_API:
        return v.UNKNOWN_DOC, None, (f"metadata V{rt.metadata_version} lists no runtime APIs"
                                     if rt.metadata_version < 15 else "runtime API lookup not implemented")
    if entry.kind == v.MODULE:
        return (v.UNPROVEN, pallet, None) if pallet else (v.ABSENT, None, "pallet absent")
    live_key = f"{pallet}.{entry.call}" if pallet else None
    if live_key not in surface.calls:
        rename = next((r for r in renames if _norm(r.pallet) == _norm(entry.pallet) and r.doc_call == entry.call), None)
        if rename and rename.kind != v.NONE:
            return v.DRIFTED, f"{rename.pallet}.{rename.live_call}", f"{rename.kind}: {rename.evidence}"
        return v.ABSENT, None, "pallet absent" if pallet is None else "call absent"
    live_names = [name for name, _ in surface.calls[live_key]]
    if entry.args is not None:
        same = list(entry.args) == live_names if entry.ordered else sorted(entry.args) == sorted(live_names)
        if not same:
            detail = {"doc_args": list(entry.args), "live_args": live_names}
            if [n.lstrip("_") for n in entry.args] == [n.lstrip("_") for n in live_names]:
                detail["underscore_only"] = True
            return v.ARG_DRIFT, live_key, json.dumps(detail, sort_keys=True, separators=(",", ":"))
    if live_key in proven:  # a receipt whose read-back decoded to this call (I1)
        return v.PROVEN, live_key, None
    if entry.args is None:
        return v.ARGS_UNCHECKED, live_key, entry.note or None
    return v.UNPROVEN, live_key, None


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
                        "FROM doc_call").fetchall()
    return [DocEntry(s, k, p, c, None if a is None else tuple(json.loads(a)), bool(o), tuple(json.loads(r)), sig, n)
            for s, k, p, c, a, o, r, sig, n in rows]


RECEIPT_FIELDS = ("side", "pallet", "call", "tx_hash", "block_hash", "block_number", "success", "dispatch_error",
                  "events_json", "weight_json", "fee", "readback_ok")


def plumb(conn: sqlite3.Connection, rt: Runtime, surface: Surface,
          renames: tuple[Rename, ...] = RENAMES, receipts: list[dict] = ()) -> int:
    """Store the runtime, its call surface and its canary receipts, then a verdict for every documented
    entry and every undocumented live call. Returns the runtime id."""
    rows = check_renames(renames, rt.profile, surface)
    cur = conn.execute("INSERT INTO runtime (profile, label, endpoint, spec_name, spec_version, tx_version, "
                       "metadata_version, metadata_hash, code_hash, block_hash, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (rt.profile, rt.label, rt.endpoint, rt.spec_name, rt.spec_version, rt.tx_version,
                        rt.metadata_version, rt.metadata_hash, rt.code_hash, rt.block_hash, rt.fetched_at))
    rid = cur.lastrowid
    conn.executemany("INSERT INTO live_pallet VALUES (?,?)", [(rid, p) for p in surface.pallets])
    for key, args in surface.calls.items():
        pallet, call = key.split(".", 1)
        conn.execute("INSERT INTO live_call VALUES (?,?,?,?)", (rid, pallet, call, json.dumps([list(a) for a in args])))
    for r in rows:
        conn.execute("INSERT OR REPLACE INTO rename VALUES (?,?,?,?,?,?)",
                     (r.pallet, r.doc_call, r.live_call, r.profile, r.evidence, r.kind))
    for receipt in receipts:
        conn.execute(f"INSERT INTO receipt (runtime_id, {', '.join(RECEIPT_FIELDS)}) VALUES (?{',?' * len(RECEIPT_FIELDS)})",
                     (rid, *(receipt.get(f) for f in RECEIPT_FIELDS)))
    proven = {f"{p}.{c}" for p, c in conn.execute(
        "SELECT pallet, call FROM receipt WHERE runtime_id = ? AND success = 1 AND readback_ok = 1", (rid,))}
    covered: set[str] = set()
    for e in _entries(conn):
        status, live, detail = classify(e, rt, surface, rows, proven)
        if e.kind == v.CALL and live:
            covered.add(live)
        receipt = None
        if status == v.PROVEN:
            receipt = conn.execute("SELECT min(id) FROM receipt WHERE runtime_id = ? AND pallet || '.' || call = ? "
                                   "AND success = 1 AND readback_ok = 1", (rid, live)).fetchone()[0]
        conn.execute("INSERT INTO verdict VALUES (?,?,?,?,?,?,?,?,?)",
                     (rid, e.source, e.kind, e.pallet, e.call, status, live, detail, receipt))
    for key in sorted(set(surface.calls) - covered):
        pallet, call = key.split(".", 1)
        conn.execute("INSERT INTO verdict VALUES (?,?,?,?,?,?,?,?,?)",
                     (rid, v.RUNTIME, v.CALL, pallet, call, v.UNDOCUMENTED, key, None, None))
    conn.commit()
    return rid


# ---------------------------------------------------------------- canonical export

def canonical(obj) -> bytes:
    """Sorted keys, no whitespace, UTF-8: the bytes the report hash is taken over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def blake2_256(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def export(conn: sqlite3.Connection, inputs: dict | None = None, profiles: list[str] | None = None) -> dict:
    """The ledger as a plain object (row order fixed, no row ids, no wall-clock time of the export)."""
    runtimes = conn.execute("SELECT id, profile, label, endpoint, spec_name, spec_version, tx_version, metadata_version, "
                            "metadata_hash, code_hash, block_hash, fetched_at FROM runtime").fetchall()
    keep = {r[0]: r for r in runtimes if profiles is None or r[1] in profiles}
    cols = ("profile", "label", "endpoint", "spec_name", "spec_version", "tx_version", "metadata_version",
            "metadata_hash", "code_hash", "block_hash", "fetched_at")
    stamp = {rid: dict(zip(cols, r[1:])) for rid, r in keep.items()}

    verdicts, counts, live_calls = [], {}, {}
    for rid, source, kind, pallet, call, status, live, detail, receipt in conn.execute(
            "SELECT runtime_id, source, kind, pallet, call, status, live_equivalent, detail, receipt_id FROM verdict"):
        if rid not in keep:
            continue
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
        if rid in keep:
            live_calls.setdefault(stamp[rid]["profile"], {})[f"{pallet}.{call}"] = json.loads(args)

    doc_calls = [{"source": s, "kind": k, "pallet": p, "call": c, "signature": sig,
                  "args": None if a is None else json.loads(a), "args_ordered": bool(o), "refs": json.loads(r),
                  "note": n}
                 for s, k, p, c, sig, a, o, r, n in conn.execute(
                     "SELECT source, kind, pallet, call_snake, signature, args_json, args_ordered, refs_json, note "
                     "FROM doc_call")]
    doc_calls.sort(key=lambda r: (r["source"], r["kind"], r["pallet"].lower(), r["pallet"], r["call"]))
    renames = [dict(zip(("pallet", "doc_call", "live_call", "profile", "evidence", "kind"), row))
               for row in conn.execute("SELECT pallet, doc_call, live_call, profile, evidence, kind FROM rename")
               if profiles is None or row[3] in profiles]
    renames.sort(key=lambda r: (r["profile"], r["pallet"], r["doc_call"]))
    receipts = [{"profile": stamp[row[0]]["profile"], **dict(zip(RECEIPT_FIELDS, row[2:])), "id": row[1]}
                for row in conn.execute(f"SELECT runtime_id, id, {', '.join(RECEIPT_FIELDS)} FROM receipt ORDER BY id")
                if row[0] in keep]
    return {
        "format": FORMAT,
        "vocabulary": {"verdicts": list(v.VERDICTS), "report_states": list(v.REPORT_STATES)},
        "runtimes": sorted(stamp.values(), key=lambda r: (r["profile"], r["metadata_hash"])),
        "inputs": inputs or {},
        "doc_calls": doc_calls,
        "renames": renames,
        "verdicts": verdicts,
        "counts": counts,
        "live_calls": live_calls,
        "receipts": receipts,
    }


def build(entries: list[DocEntry], runtimes: list[tuple[Runtime, Surface]], inputs: dict | None = None,
          db: str | Path = ":memory:", renames: tuple[Rename, ...] = RENAMES) -> tuple[bytes, sqlite3.Connection]:
    """Entries × runtimes -> (canonical ledger.json bytes, the open ledger). `db` must not exist yet."""
    if str(db) != ":memory:" and Path(db).exists():
        raise LedgerError(f"{db} exists; the ledger is rebuilt from its inputs, never patched in place")
    conn = open_ledger(db)
    add_entries(conn, entries)
    for rt, surface in runtimes:
        plumb(conn, rt, surface, renames)
    return canonical(export(conn, inputs)), conn
