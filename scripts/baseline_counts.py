"""Count pallets, calls, constants and events of two stored runtimes and the diff between them -> evidence JSON.

    python -I scripts/baseline_counts.py --out evidence/baseline_v3.json

Offline: reads fixtures/metadata_1002.scale and fixtures/metadata_v3.scale only. Every count quoted in
docs/BASELINE-V3.md comes from the file this writes.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline import diff as diff_mod  # noqa: E402
from plumbline import metadata as md  # noqa: E402

SIDES = {"mainnet-1002": "fixtures/metadata_1002.scale", "v3": "fixtures/metadata_v3.scale"}


def counts(nf: diff_mod.NormalForm) -> dict:
    return {
        "pallets": len(nf.pallets),
        "calls": nf.call_count(),
        "constants": sum(len(p.constants) for p in nf.pallets.values()),
        "events": sum(len(p.events) for p in nf.pallets.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    forms, out = {}, {"sides": {}}
    for name, rel in SIDES.items():
        blob = Path(rel).read_bytes()
        prof = md.get_profile(f"stored:{rel}")
        forms[name] = diff_mod.normalise(md.decode_metadata(blob, prof.preset), md.metadata_version(blob))
        out["sides"][name] = {"fixture": rel, "metadata_sha256": md.sha256_hex(blob),
                              "metadata_version": md.metadata_version(blob), **counts(forms[name])}
    rows = diff_mod.diff(forms["mainnet-1002"], forms["v3"])
    out["diff_summary"] = diff_mod.summary(rows)
    out["diff_rows"] = len(rows)
    a, b = forms["mainnet-1002"], forms["v3"]
    out["pallets_removed"] = sorted(set(a.pallets) - set(b.pallets))
    out["pallets_added"] = sorted(set(b.pallets) - set(a.pallets))
    out["pallets_shared"] = len(set(a.pallets) & set(b.pallets))
    out["calls_removed"] = [f"{r.pallet}.{r.item.split(':', 1)[1]}" for r in rows if r.kind == diff_mod.REMOVED and r.item.startswith("call:")]
    out["calls_renamed"] = [f"{r.pallet}.{r.item.split(':', 1)[1]} -> {r.after['call']}" for r in rows if r.kind == diff_mod.RENAMED]
    out["balances_v3"] = sorted(b.pallets["Balances"].calls)
    out["balances_1002"] = sorted(a.pallets["Balances"].calls)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
