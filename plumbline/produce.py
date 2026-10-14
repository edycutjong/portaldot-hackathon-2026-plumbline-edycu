"""Producers shared by the scripts and the `plumbline` CLI: the ledger build (scripts/build_ledger.py,
`plumbline plumb`). Each takes plain arguments, prints through a callback and returns an exit code."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from . import docs, guide, metadata as md, plumb, verdicts as v


@dataclass
class Args:
    profile: str
    runtime_record: Path
    out: Path
    guide_table: Path = Path("evidence/guide_calls.json")
    guide_dir: Path | None = None
    docs_table: Path = Path("evidence/docs_1002.json")
    docs_cache: Path | None = None
    db: Path = Path(".cache/ledger.db")


BASELINE_NOTE = ("mainnet-1002 is the read-only baseline: verdicts of the V3 guide's entries (v3-guide, v3-demo, "
                 "v3-faq) on it say what those calls look like on mainnet spec 1002, not on V3")


def ledger(args: Args, say=print, warn=None) -> int:
    """Build the ledger files described by `args`. Returns 0, or 1 after `warn`-ing why nothing was written."""
    def fail(msg: str) -> int:
        (warn or (lambda m: print(m, file=sys.stderr)))(f"refusing to write the ledger: {msg}")
        return 1

    try:
        prof = md.get_profile(args.profile)
    except md.ProfileError as exc:
        return fail(str(exc))
    if not prof.stored_file:
        return fail("--profile must be a stored:<file> profile (record one with scripts/record_metadata.py)")
    blob = Path(prof.stored_file).read_bytes()
    origin = md.recorded_origin(blob)
    if origin is None:
        return fail(f"{prof.stored_file}: origin not recorded (sha256 not in KNOWN_BLOBS)")
    if origin == "interim-sdk":
        return fail(f"{prof.stored_file} is the stand-in, not V3: test input only, no verdicts are computed from it")
    record = json.loads(args.runtime_record.read_text())
    if record.get("profile") != origin:
        return fail(f"{args.runtime_record} is for profile {record.get('profile')}, the blob was recorded from {origin}")
    try:
        runtime = plumb.runtime_from_record(record, blob, md.PROFILES[origin].label, report_state=v.CACHED)
    except plumb.LedgerError as exc:
        return fail(str(exc))
    surface = plumb.surface_from_blob(blob, prof.preset)

    if args.guide_dir:
        head, dirty = guide.clone_state(args.guide_dir.expanduser())
        if head != guide.GUIDE_COMMIT or dirty:
            return fail(f"{args.guide_dir} is at {head} (dirty: {dirty}), not a clean {guide.GUIDE_COMMIT}")
        parsed = guide.parse_guide(args.guide_dir.expanduser())
        if parsed.unaccounted:
            return fail(f"guide references no site accounts for: {', '.join(parsed.unaccounted)}")
        sites, files = parsed.sites, parsed.files
    else:
        table = json.loads(args.guide_table.read_text())
        if table["commit"] != guide.GUIDE_COMMIT:
            return fail(f"{args.guide_table} is for guide commit {table['commit']}, not {guide.GUIDE_COMMIT}")
        if table.get("unaccounted"):
            return fail(f"{args.guide_table} records unaccounted references: {table['unaccounted']}")
        sites, files = [guide.site_from_dict(s) for s in table["sites"]], table["files"]

    if args.docs_cache:
        results = docs.parse_dir(args.docs_cache)
        if not all(r.complete for r in results) or docs.duplicate_calls(results):
            return fail("the docs parse is incomplete or has duplicate calls (run plumbline docs to see where)")
        rows = docs.compare(docs.documented_calls(results), {}, [])["rows"]  # docs-side fields only
        pages = {r.page: r.interfaces for r in results}
    else:
        docs_record = json.loads(args.docs_table.read_text())
        if docs_record["pages_complete"] != docs_record["pages"]:
            return fail(f"{args.docs_table} records an incomplete parse")
        rows, pages = docs_record["rows"], docs_record["per_page"]
        doubled = sorted({r["doc_call"] for r in rows if sum(x["doc_call"] == r["doc_call"] for x in rows) > 1})
        if doubled:
            return fail(f"{args.docs_table} documents these calls twice: {doubled}")

    entries = plumb.entries_from_guide(sites) + plumb.entries_from_mainnet_docs(rows)
    inputs = {
        "guide": {"repo": guide.GUIDE_REPO, "commit": guide.GUIDE_COMMIT, "files": files,
                  "sites": {s: sum(1 for x in sites if x.source == s) for s in (v.V3_GUIDE, v.V3_DEMO, v.V3_FAQ)}},
        "mainnet-docs": {"base": docs.DOCS_BASE, "pages": len(pages), "documented_calls": len(rows)},
        "metadata": {"file": Path(prof.stored_file).name, "sha256": runtime.metadata_hash},
        "note": BASELINE_NOTE if runtime.profile == "mainnet-1002" else "",
    }
    args.db.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = args.db.with_name(args.db.name + ".tmp")
    tmp_db.unlink(missing_ok=True)
    try:
        data, conn = plumb.build(entries, [(runtime, surface)], inputs, db=tmp_db)
        conn.close()
    except (plumb.LedgerError, sqlite3.Error) as exc:  # e.g. a malformed table colliding on a primary key
        tmp_db.unlink(missing_ok=True)
        return fail(f"{type(exc).__name__}: {exc}")
    tmp_db.replace(args.db)  # the ledger is rebuilt from its inputs, never patched in place
    args.out.write_bytes(data)
    digest = plumb.blake2_256(data)
    Path(f"{args.out}.blake2-256").write_text(f"{digest}  {args.out.name}\n")

    ledger = json.loads(data)
    say(f"ledger {runtime.profile} ({runtime.spec_name}/{runtime.spec_version}, metadata V{runtime.metadata_version} "
          f"sha256 {runtime.metadata_hash[:12]}…): {len(ledger['verdicts'])} verdict rows")
    for source, per in sorted(ledger["counts"][runtime.profile].items()):
        say(f"  {source:13} " + ", ".join(f"{status} {n}" for status, n in sorted(per.items())))
    say(f"  {runtime.label}")
    say(f"wrote {args.out} ({len(data)} bytes, blake2-256 {digest}) and {args.db}")
    return 0
