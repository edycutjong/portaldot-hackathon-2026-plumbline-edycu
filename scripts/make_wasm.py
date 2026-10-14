"""Write the tiny hand-assembled wasm modules `plumbline check` is tested on (deterministic bytes).

    python -I scripts/make_wasm.py            # (re)write them
    python -I scripts/make_wasm.py --verify   # exit 1 if a committed file differs from what this writes

Each module is the smallest valid wasm a contracts pallet would look at: a memory import from `env`, a few
function imports, and two empty exported functions `deploy` / `call`. They are import-section fixtures,
not working contracts: nothing here has been deployed anywhere.

  fixtures/wasm/seal0_only.wasm        seal0 imports only (the rent-era ABI)
  fixtures/wasm/seal_newer.wasm        seal0 + seal1 + seal2 imports (the newer ABI ink! 5 builds use)
  fixtures/wasm/no_seal.wasm           no seal* import: not a contracts module
  fixtures/wasm/seal_newer.contract    a `.contract` bundle around seal_newer.wasm
  examples/mainnet-dapp/contract/minimal_seal0.wasm   = seal0_only.wasm, the example dApp's artifact
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def vec(items: list[bytes]) -> bytes:
    return uleb(len(items)) + b"".join(items)


def name(text: str) -> bytes:
    data = text.encode()
    return uleb(len(data)) + data


def section(sid: int, body: bytes) -> bytes:
    return bytes([sid]) + uleb(len(body)) + body


I32 = 0x7F
VOID = bytes([0x60, 0x00, 0x00])                    # () -> ()
TWO_I32 = bytes([0x60, 0x02, I32, I32, 0x00])        # (i32, i32) -> ()


def module(funcs: list[tuple[str, str]]) -> bytes:
    """`funcs` = (module, field) function imports, each typed (i32, i32) -> ()."""
    imports = [name("env") + name("memory") + bytes([0x02, 0x00]) + uleb(1)]  # memory, min 1 page
    imports += [name(m) + name(f) + bytes([0x00]) + uleb(1) for m, f in funcs]
    first_local = len(funcs)  # defined functions come after the imported ones
    return (b"\0asm" + bytes([1, 0, 0, 0])
            + section(1, vec([VOID, TWO_I32]))
            + section(2, vec(imports))
            + section(3, vec([uleb(0), uleb(0)]))
            + section(7, vec([name("deploy") + bytes([0x00]) + uleb(first_local),
                              name("call") + bytes([0x00]) + uleb(first_local + 1)]))
            + section(10, vec([b"\x02\x00\x0b", b"\x02\x00\x0b"])))


SEAL0_ONLY = module([("seal0", "seal_caller"), ("seal0", "seal_value_transferred"), ("seal0", "seal_return")])
SEAL_NEWER = module([("seal0", "caller"), ("seal1", "get_storage"), ("seal2", "set_storage")])
NO_SEAL = module([("env", "host_log")])

FILES = {
    "fixtures/wasm/seal0_only.wasm": SEAL0_ONLY,
    "fixtures/wasm/seal_newer.wasm": SEAL_NEWER,
    "fixtures/wasm/no_seal.wasm": NO_SEAL,
    "fixtures/wasm/seal_newer.contract": (json.dumps(
        {"source": {"hash": None, "language": "hand-assembled test fixture", "wasm": "0x" + SEAL_NEWER.hex()},
         "contract": {"name": "seal_newer_fixture", "version": "0.0.0"}}, indent=2, sort_keys=True) + "\n").encode(),
    "examples/mainnet-dapp/contract/minimal_seal0.wasm": SEAL0_ONLY,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    bad = 0
    for rel, data in FILES.items():
        path = ROOT / rel
        if args.verify:
            same = path.exists() and path.read_bytes() == data
            bad += not same
            print(f"{'ok  ' if same else 'DIFF'} {rel} ({len(data)} bytes)")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f"wrote {rel} ({len(data)} bytes)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
