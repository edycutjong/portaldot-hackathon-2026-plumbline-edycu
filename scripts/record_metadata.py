"""Fetch a profile's runtime metadata (read-only) and store it as a fixture with an evidence record.

    python scripts/record_metadata.py --profile mainnet-1002 \
        --out fixtures/metadata_1002.scale --evidence evidence/metadata_1002.json

For a dev chain pass `--at genesis` so the blob can be re-read byte-for-byte from the archive node.

Only RPC reads are made: state_getRuntimeVersion, chain_getBlockHash(0), state_getMetadata,
state_getStorageHash(":code"). No transaction is built or sent.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plumbline import metadata as md  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--url", help="override the profile endpoint")
    ap.add_argument("--at", default="head",
                    help="block to read at: head (= finalized head, resolved once), genesis, or a block hash")
    ap.add_argument("--out", required=True, type=Path, help="where to write the raw SCALE metadata blob")
    ap.add_argument("--evidence", required=True, type=Path, help="where to write the JSON evidence record")
    args = ap.parse_args()

    profile = md.get_profile(args.profile)
    substrate = md.connect(profile, args.url)  # fails loud on an unknown chain
    if args.at == "genesis":
        block = substrate.rpc_request("chain_getBlockHash", [0])["result"]
    elif args.at == "head":
        block = md.pinned_head(substrate)
    else:
        block = args.at
    blob = md.fetch_metadata_blob(substrate, block)
    info = md.runtime_info(profile, substrate, blob, block, endpoint=args.url or profile.url)
    substrate.close()

    decoded = md.decode_metadata(blob, profile.preset)  # the blob must decode offline before it is kept
    calls = md.call_surface(decoded)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)
    record = md.write_evidence(args.evidence, info, args.out)
    record_extra = {"pallets": len(md.pallet_names(decoded)), "calls": len(calls)}
    print(f"profile {info.profile}: {info.spec_name}/{info.spec_version} tx{info.tx_version} "
          f"metadata V{info.metadata_version} {info.metadata_bytes} bytes sha256 {info.metadata_sha256}")
    print(f"  genesis {info.genesis_hash}  :code hash {info.code_hash}  read at {info.block_hash}")
    print(f"  {record_extra['pallets']} pallets, {record_extra['calls']} calls  fetched {record['fetched_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
