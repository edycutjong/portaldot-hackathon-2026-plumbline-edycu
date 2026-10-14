"""Clone the Portaldot V3 testnet guide at its pinned commit into a cache outside this repository.

    python -I scripts/fetch_guide.py [--dest ~/.cache/plumbline-sources/guide]

The guide is third-party content with no stated licence: it is cloned outside the tree, read by
`plumbline.guide` as text and Python AST only (never imported or executed), and none of its files are
committed here. Only the extracted call table (names + file:line refs) is. Exits 1 if the fetch fails,
or if an existing clone is on another commit or has local changes (it is never repaired in place).
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline.guide import GUIDE_COMMIT, GUIDE_REPO, clone_state  # noqa: E402

DEFAULT_DEST = Path.home() / ".cache" / "plumbline-sources" / "guide"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = ap.parse_args()
    dest = args.dest.expanduser()
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            git("clone", "--quiet", "--no-checkout", GUIDE_REPO, str(dest))
            git("-c", "advice.detachedHead=false", "checkout", "--quiet", GUIDE_COMMIT, cwd=dest)
        except subprocess.CalledProcessError as exc:
            print(f"error: fetching {GUIDE_REPO}@{GUIDE_COMMIT[:7]} failed: {exc.stderr.strip()}", file=sys.stderr)
            return 1
    head, dirty = clone_state(dest)
    if head != GUIDE_COMMIT or dirty:
        print(f"error: {dest} is at {head} (dirty: {dirty}); expected a clean checkout of {GUIDE_COMMIT}",
              file=sys.stderr)
        return 1
    print(f"guide {GUIDE_REPO} at {head} in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
