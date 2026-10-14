"""Documented mainnet surface vs a runtime's call surface -> evidence JSON.

    python scripts/fetch_docs.py
    python scripts/docs_baseline.py --profile stored:fixtures/metadata_1002.scale@mainnet-1002 \
        --label mainnet-1002 --out evidence/docs_1002.json

Classes are raw comparisons (match / arg-mismatch / call-absent / pallet-absent); ledger verdicts
are assigned by the ledger, not here.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline import docs, metadata as md  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", type=Path, default=Path(".cache/docs"))
    ap.add_argument("--profile", required=True, help="a stored: profile, e.g. stored:fixtures/metadata_1002.scale@mainnet-1002")
    ap.add_argument("--label", required=True, help="profile label written into every row, e.g. mainnet-1002")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    prof = md.get_profile(args.profile)
    if not prof.stored_file:
        raise SystemExit("docs_baseline compares against a stored blob; record one with scripts/record_metadata.py")
    blob = Path(prof.stored_file).read_bytes()
    live = md.call_surface(md.decode_metadata(blob, prof.preset))

    results = docs.parse_dir(args.cache)
    documented = docs.documented_calls(results)
    out = docs.compare(documented, live)
    manifest_path = args.cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}

    record = {
        "profile": args.label,
        "metadata_file": prof.stored_file,
        "metadata_sha256": md.sha256_hex(blob),
        "metadata_version": md.metadata_version(blob),
        "pages": len(results),
        "pages_parsed": sum(r.parsed for r in results),
        "interfaces": sum(r.interfaces for r in results),
        "per_page": {r.page: r.interfaces for r in results},
        "unpaired": {r.page: r.unpaired for r in results if r.unpaired},
        "layouts": {r.page: sorted({c.layout for c in r.calls}) for r in results},
        "sources": {page: {k: v for k, v in entry.items() if k in ("url", "sha256", "bytes", "fetched_at")}
                    for page, entry in sorted(manifest["files"].items())},
    } | out
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")

    c = out["counts"]
    print(f"parsed {record['pages_parsed']}/{record['pages']} pages, {c['documented']} documented calls "
          f"({record['interfaces']} interface lines, unpaired {sum(len(v) for v in record['unpaired'].values())})")
    print(f"vs {args.label} ({c['live_calls']} live calls): absent {c['absent']} "
          f"(call {c['call-absent']}, pallet {c['pallet-absent']}), arg-mismatch {c['arg-mismatch']}, match {c['match']}, "
          f"undocumented live {c['undocumented']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
