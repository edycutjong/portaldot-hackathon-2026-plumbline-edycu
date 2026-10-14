"""Documented mainnet surface vs a stored runtime's call surface -> evidence JSON.

    python scripts/fetch_docs.py
    python scripts/docs_baseline.py --profile stored:fixtures/metadata_1002.scale --out evidence/docs_1002.json

The profile label written into the evidence comes from the blob's recorded origin (its sha256 in
`plumbline.metadata.KNOWN_BLOBS`), never from free text. Exits 1, writing nothing, if any page is not
completely parsed, a call is documented twice, or a cached page differs from its manifest entry. Classes are raw comparisons (match / arg-mismatch /
args-unknown / call-absent / pallet-absent); ledger verdicts are assigned by the ledger, not here.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline import docs, metadata as md  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", type=Path, default=Path(".cache/docs"))
    ap.add_argument("--profile", required=True, help="a stored: profile, e.g. stored:fixtures/metadata_1002.scale")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    try:
        prof = md.get_profile(args.profile)
    except md.ProfileError as exc:
        raise SystemExit(f"error: {exc}")
    if not prof.stored_file:
        raise SystemExit("docs_baseline compares against a stored blob; record one with scripts/record_metadata.py")
    blob = Path(prof.stored_file).read_bytes()
    if md.recorded_origin(blob) is None:
        raise SystemExit(f"error: {prof.stored_file}: origin not recorded (sha256 not in KNOWN_BLOBS); refusing to label it")
    decoded = md.decode_metadata(blob, prof.preset)
    live = md.call_surface(decoded)

    try:
        results = docs.parse_dir(args.cache)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc} (run scripts/fetch_docs.py)")
    incomplete = [r.page for r in results if not r.complete]
    duplicates = docs.duplicate_calls(results)
    if incomplete or duplicates:
        print(f"refusing to write evidence: incomplete pages {incomplete}, duplicate calls {sorted(duplicates)}",
              file=sys.stderr)
        return 1
    out = docs.compare(docs.documented_calls(results), live, md.pallet_names(decoded))

    manifest_path = args.cache / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": {}}
    sources = {}
    for page in ["index"] + [r.page for r in results]:
        body = (args.cache / f"{page}.html").read_bytes()  # hash what was parsed, not what the manifest says
        entry = manifest["files"].get(page, {})
        if entry and entry.get("sha256") != hashlib.sha256(body).hexdigest():
            print(f"refusing to write evidence: {page}.html differs from manifest.json (partial re-fetch?)",
                  file=sys.stderr)
            return 1
        sources[page] = {"url": entry.get("url", f"{docs.DOCS_BASE}{page}.html"), "bytes": len(body),
                         "sha256": hashlib.sha256(body).hexdigest(), "fetched_at": entry.get("fetched_at")}

    record = {
        "profile": prof.measured_as,
        "label": prof.label,
        "metadata_file": prof.stored_file,
        "metadata_sha256": md.sha256_hex(blob),
        "metadata_version": md.metadata_version(blob),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": len(results),
        "pages_parsed": sum(r.parsed for r in results),
        "pages_complete": sum(r.complete for r in results),
        "interfaces": sum(r.interfaces for r in results),
        "signatures": sum(r.signatures for r in results),
        "per_page": {r.page: r.interfaces for r in results},
        "per_page_signatures": {r.page: r.signatures for r in results},
        "unpaired": {r.page: r.unpaired for r in results if r.unpaired},
        "layouts": {r.page: sorted({c.layout for c in r.calls}) for r in results},
        "sources": sources,
    } | out
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")

    c = out["counts"]
    print(f"parsed {record['pages_parsed']}/{record['pages']} pages ({record['pages_complete']} complete), "
          f"{c['documented']} documented calls ({record['interfaces']} interface lines, "
          f"{record['signatures']} signatures, unpaired {sum(len(v) for v in record['unpaired'].values())})")
    print(f"vs {prof.measured_as} ({c['live_calls']} live calls): absent {c['absent']} "
          f"(call {c['call-absent']}, pallet {c['pallet-absent']}), arg-mismatch {c['arg-mismatch']} "
          f"({c['arg-mismatch-underscore-only']} underscore-only), args-unknown {c['args-unknown']}, "
          f"match {c['match']}, undocumented live {c['undocumented']}")
    print(f"  {prof.label}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
