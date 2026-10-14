"""Build the drift ledger for one recorded runtime over every parsed source -> canonical ledger JSON.

    python -I scripts/build_ledger.py --profile stored:fixtures/metadata_1002.scale \\
        --runtime-record evidence/metadata_1002.json --out evidence/ledger_1002.json

Sources (each defaults to its committed call table; pass the fresh-parse option to re-read the inputs):
  v3-guide, v3-demo, v3-faq   --guide-table evidence/guide_calls.json | --guide-dir <clean clone at the pinned commit>
  mainnet-docs                --docs-table evidence/docs_1002.json    | --docs-cache .cache/docs

Writes `<out>` as canonical JSON (sorted keys, no whitespace, UTF-8) and `<out>.blake2-256` with its
blake2-256 hash; the SQLite ledger goes to `--db` (default .cache/ledger.db, rebuilt from scratch).
Refuses, writing nothing: an unrecorded blob, a stand-in (interim-sdk) blob, a runtime record that is
not for this blob, a guide table or clone that is not the pinned commit, an incomplete docs parse.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline import docs, guide, metadata as md, plumb, verdicts as v  # noqa: E402


def fail(msg: str) -> int:
    print(f"refusing to write the ledger: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True, help="stored:<blob> recorded by scripts/record_metadata.py")
    ap.add_argument("--runtime-record", type=Path, required=True, help="the blob's evidence record")
    ap.add_argument("--guide-table", type=Path, default=Path("evidence/guide_calls.json"))
    ap.add_argument("--guide-dir", type=Path, help="parse a clean clone instead of the committed table")
    ap.add_argument("--docs-table", type=Path, default=Path("evidence/docs_1002.json"))
    ap.add_argument("--docs-cache", type=Path, help="parse fetched pages instead of the committed table")
    ap.add_argument("--db", type=Path, default=Path(".cache/ledger.db"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

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
        runtime = plumb.runtime_from_record(record, blob, md.PROFILES[origin].label)
    except plumb.LedgerError as exc:
        return fail(str(exc))
    decoded = md.decode_metadata(blob, prof.preset)
    surface = plumb.Surface(calls=md.call_surface(decoded), pallets=md.pallet_names(decoded))

    if args.guide_dir:
        head, dirty = guide.clone_state(args.guide_dir.expanduser())
        if head != guide.GUIDE_COMMIT or dirty:
            return fail(f"{args.guide_dir} is at {head} (dirty: {dirty}), not a clean {guide.GUIDE_COMMIT}")
        parsed = guide.parse_guide(args.guide_dir.expanduser())
        sites, files = parsed.sites, parsed.files
    else:
        table = json.loads(args.guide_table.read_text())
        if table["commit"] != guide.GUIDE_COMMIT:
            return fail(f"{args.guide_table} is for guide commit {table['commit']}, not {guide.GUIDE_COMMIT}")
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

    entries = plumb.entries_from_guide(sites) + plumb.entries_from_mainnet_docs(rows)
    inputs = {
        "guide": {"repo": guide.GUIDE_REPO, "commit": guide.GUIDE_COMMIT, "files": files,
                  "sites": {s: sum(1 for x in sites if x.source == s) for s in (v.V3_GUIDE, v.V3_DEMO, v.V3_FAQ)}},
        "mainnet-docs": {"base": docs.DOCS_BASE, "pages": len(pages), "documented_calls": len(rows)},
        "metadata": {"file": prof.stored_file, "sha256": runtime.metadata_hash},
    }
    if args.db.exists():
        args.db.unlink()  # rebuilt from its inputs, never patched in place
    args.db.parent.mkdir(parents=True, exist_ok=True)
    try:
        data, conn = plumb.build(entries, [(runtime, surface)], inputs, db=args.db)
    except plumb.LedgerError as exc:
        return fail(str(exc))
    conn.close()
    args.out.write_bytes(data)
    digest = plumb.blake2_256(data)
    Path(f"{args.out}.blake2-256").write_text(f"{digest}  {args.out.name}\n")

    ledger = json.loads(data)
    print(f"ledger {runtime.profile} ({runtime.spec_name}/{runtime.spec_version}, metadata V{runtime.metadata_version} "
          f"sha256 {runtime.metadata_hash[:12]}…): {len(ledger['verdicts'])} verdict rows")
    for source, per in sorted(ledger["counts"][runtime.profile].items()):
        print(f"  {source:13} " + ", ".join(f"{status} {n}" for status, n in sorted(per.items())))
    print(f"  {runtime.label}")
    print(f"wrote {args.out} ({len(data)} bytes, blake2-256 {digest}) and {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
