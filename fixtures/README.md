# fixtures

Test inputs. Every file here is either hand-written for a test or a chain metadata blob
whose origin, sha256 and fetch time are recorded in `../evidence/`.

| File | What | Origin |
|---|---|---|
| `metadata_1002.scale` | Portaldot mainnet runtime metadata, spec 1002, V13 | `wss://mainnet.portaldot.io`, read-only; record in `../evidence/metadata_1002.json` |
| `metadata_standin_sdk.scale` | **stand-in, not V3 — test input only.** Metadata V14 of an upstream polkadot-sdk `substrate-node --dev` (node/268) | local interim chain (`../scripts/interim_node.sh`); record in `../evidence/metadata_standin_sdk.json` |
| `docs_layouts/` | **hand-written** miniatures of the three mainnet readthedocs extrinsic-page layouts for `tests/test_docs.py`. Not copied from the docs | hand-written |
| `guide_layouts/` | **hand-written** miniature of the V3 guide's three layouts (README call tables + Node Explorer paths, `demo/*.py` call sites, FAQ module table) for `tests/test_guide.py`. Not copied from the guide; `demo/a_calls.py` writes a marker file if it is ever executed | hand-written |
| `wasm/*.wasm`, `wasm/seal_newer.contract` | **hand-assembled** import-section fixtures for `plumbline check` (seal0 only / seal0+seal1+seal2 / no seal import, and a `.contract` bundle). Not working contracts; never deployed | `../scripts/make_wasm.py` (`--verify` checks the committed bytes) |
| `expected_check_1002.txt` | golden output of `plumbline check examples/mainnet-dapp --target mainnet-1002 --offline` (the 1002 profile only; a V3 golden is written only after a V3 run) | `tests/test_check_cli.py` reproduces it byte for byte |
