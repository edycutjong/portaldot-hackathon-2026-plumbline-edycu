# fixtures

Test inputs. Every file here is either hand-written for a test or a chain metadata blob
whose origin, sha256 and fetch time are recorded in `../evidence/`.

| File | What | Origin |
|---|---|---|
| `metadata_1002.scale` | Portaldot mainnet runtime metadata, spec 1002, V13 | `wss://mainnet.portaldot.io`, read-only; record in `../evidence/metadata_1002.json` |
| `metadata_standin_sdk.scale` | **stand-in, not V3 — test input only.** Metadata V14 of an upstream polkadot-sdk `substrate-node --dev` (node/268) | local interim chain (`../scripts/interim_node.sh`); record in `../evidence/metadata_standin_sdk.json` |
