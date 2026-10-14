"""Fetch the Portaldot mainnet extrinsic docs pages into a local, gitignored cache.

    python scripts/fetch_docs.py [--cache .cache/docs]

Reads the extrinsics index, then every page it links to. Writes `<page>.html` plus `manifest.json`
(url, HTTP status, bytes, sha256, fetch time per page). The pages are third-party content: they are
parsed from the cache, never committed to this repository.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline.docs import DOCS_BASE, index_pages  # noqa: E402

USER_AGENT = "portaldot-plumbline/0.0.1 (+docs drift check)"


def fetch(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", type=Path, default=Path(".cache/docs"))
    ap.add_argument("--base", default=DOCS_BASE)
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)

    entries = {}

    def get(page: str) -> bytes:
        url = f"{args.base}{page}.html"
        status, body = fetch(url)
        if status != 200:
            raise SystemExit(f"{url}: HTTP {status}")
        (args.cache / f"{page}.html").write_bytes(body)
        entries[page] = {"url": url, "status": status, "bytes": len(body),
                         "sha256": hashlib.sha256(body).hexdigest(),
                         "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        return body

    pages = index_pages(get("index").decode("utf-8"))
    for page in pages:
        get(page)
    manifest = {"base": args.base, "pages": pages, "files": entries}
    (args.cache / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"fetched index + {len(pages)} extrinsic pages into {args.cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
