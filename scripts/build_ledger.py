"""Build the drift ledger for one recorded runtime over every parsed source -> canonical ledger JSON.

    python -I scripts/build_ledger.py --profile stored:fixtures/metadata_1002.scale \\
        --runtime-record evidence/metadata_1002.json --out evidence/ledger_1002.json

Sources (each defaults to its committed call table; pass the fresh-parse option to re-read the inputs):
  v3-guide, v3-demo, v3-faq   --guide-table evidence/guide_calls.json | --guide-dir <clean clone at the pinned commit>
  mainnet-docs                --docs-table evidence/docs_1002.json    | --docs-cache .cache/docs

Writes `<out>` as canonical JSON (sorted keys, no whitespace, UTF-8) and `<out>.blake2-256` with its
blake2-256 hash; the SQLite ledger goes to `--db` (default .cache/ledger.db, rebuilt from scratch and
replaced only on success). The runtime is stamped CACHED: it is measured from a stored blob.
Refuses, writing nothing: an unrecorded blob, a stand-in (interim-sdk) blob, a runtime record that is
not for this blob, a guide table or clone that is not the pinned commit or has unaccounted references,
an incomplete docs parse or a call documented twice.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline import produce  # noqa: E402


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
    return produce.ledger(produce.Args(**vars(ap.parse_args())))


if __name__ == "__main__":
    raise SystemExit(main())
