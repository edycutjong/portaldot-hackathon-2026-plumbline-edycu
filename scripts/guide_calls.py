"""The V3 guide's documented call table -> evidence JSON (names + file:line refs only, no guide text).

    python -I scripts/fetch_guide.py
    python -I scripts/guide_calls.py --out evidence/guide_calls.json

Refuses (exit 1, writes nothing) unless the clone is a clean checkout of the pinned commit and every
code token naming compose_call / runtime_call / rpc_request, and every README line that looks like a
Developer path or call table, is covered by a site. Also
reports the plain-text cross-check: demo lines that mention `compose_call` but are not an AST call
site (a comment, a string), so the AST count and a grep count can be compared line by line.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline import guide, verdicts as v  # noqa: E402

DEFAULT_DIR = Path.home() / ".cache" / "plumbline-sources" / "guide"


def counts(res: guide.GuideResult, guide_dir: Path) -> dict:
    demo = [s for s in res.sites if s.source == v.V3_DEMO]
    compose = [s for s in demo if s.kind == v.CALL]
    text_lines = [f"{rel}:{n}" for rel in sorted(res.files) if rel.startswith("demo/")
                  for n, line in enumerate((guide_dir / rel).read_text(encoding="utf-8").splitlines(), start=1)
                  if "compose_call" in line]
    ast_lines = {s.ref for s in compose}
    readme = [s for s in res.sites if s.source == v.V3_GUIDE]
    faq = [s for s in res.sites if s.source == v.V3_FAQ]
    return {
        v.V3_DEMO: {
            "compose_call_sites": len(compose),
            "compose_call_unresolved": sum(not s.resolved for s in compose),
            "compose_call_by_how": dict(sorted(Counter(s.how for s in compose).items())),
            "distinct_pallet_calls": len({(s.pallet, c) for s in compose if s.resolved for c in s.calls}),
            "candidate_sites": [s.ref for s in compose if len(s.calls) > 1],
            "runtime_call_sites": sum(s.how == guide.RUNTIME_CALL for s in demo),
            "state_call_sites": sum(s.how == guide.STATE_CALL for s in demo),
            "text_lines_mentioning_compose_call": len(text_lines),
            "text_lines_not_a_call_site": [ref for ref in text_lines if ref not in ast_lines],
        },
        v.V3_GUIDE: {
            "call_table_names": sum(s.how == guide.TABLE for s in readme),
            "explorer_paths": sum(s.how == guide.EXPLORER for s in readme),
            "unresolved": sum(not s.resolved for s in readme),
        },
        v.V3_FAQ: {
            "modules": sum(s.resolved for s in faq),
            "unresolved": sum(not s.resolved for s in faq),
            "awaiting_lines": len(res.awaiting_lines),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--guide-dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    guide_dir = args.guide_dir.expanduser()
    head, dirty = guide.clone_state(guide_dir)
    if head != guide.GUIDE_COMMIT or dirty:
        print(f"refusing to write evidence: {guide_dir} is at {head} (dirty: {dirty}), not a clean "
              f"checkout of {guide.GUIDE_COMMIT}; run scripts/fetch_guide.py", file=sys.stderr)
        return 1
    res = guide.parse_guide(guide_dir)
    if res.unaccounted:
        print(f"refusing to write evidence: references no site accounts for: {', '.join(res.unaccounted)}",
              file=sys.stderr)
        return 1
    record = {
        "repo": guide.GUIDE_REPO,
        "commit": guide.GUIDE_COMMIT,
        "files": res.files,
        "counts": counts(res, guide_dir),
        "faq_awaiting_lines": res.awaiting_lines,
        "unaccounted": res.unaccounted,
        "sites": [guide.site_to_dict(s) for s in res.sites],
        "note": "Names, file:line references and the FAQ status key (a closed set) extracted from the guide; "
                "no guide text is reproduced.",
    }
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    c = record["counts"]
    d, g, f = c[v.V3_DEMO], c[v.V3_GUIDE], c[v.V3_FAQ]
    print(f"guide {guide.GUIDE_COMMIT[:7]}: v3-demo {d['compose_call_sites']} compose_call sites "
          f"({d['compose_call_unresolved']} unresolved; {d['distinct_pallet_calls']} distinct pallet.call names, "
          f"{len(d['candidate_sites'])} site with candidates), "
          f"{d['runtime_call_sites']} runtime_call + {d['state_call_sites']} raw state_call; "
          f"text lines mentioning compose_call {d['text_lines_mentioning_compose_call']} "
          f"(not a call site: {', '.join(d['text_lines_not_a_call_site']) or 'none'})")
    print(f"  v3-guide {g['call_table_names']} call-table names + {g['explorer_paths']} explorer paths "
          f"({g['unresolved']} unresolved); v3-faq {f['modules']} modules ({f['unresolved']} unresolved), "
          f"{f['awaiting_lines']} lines with a bold 'awaiting' status")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
