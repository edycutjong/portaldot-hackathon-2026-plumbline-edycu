# PROOF

## Portaldot V3 testnet — profile v3

Canary receipts written by `plumbline canary -p v3`. **Every row below is a transaction on the Portaldot V3
testnet, profile v3** (`portaldot-v3`/3002, genesis pinned in the profile), signed
by one funded canary account (`5EhP3KWtUkaRuFUoejCfmYHfi54d2fomNFoyVjNFFsRUzDsb`), the cheapest documented calls, each sent once.

- Run `20261019T182014Z`, endpoint `wss://test-node-v3.portaldot.dev`, metadata sha256 `381014358cc17ba6…` read at block `0x3c8df58010b6ae144b4157fd5c81a64d78735fb3c113b0759225cf8d2fee9868`
- The metadata sha256 above is the read at the run's start block; the V3 metadata hash varies between blocks, see `BASELINE-V3.md` ("The metadata hash moves between blocks"). Call verdicts rest on the read-back receipts below, not on that hash.
- Explorer: the Portaldot-hosted V3 console, https://node-console.portaldot.dev/ , takes a block number or block hash (no tx-hash search); use the block number from the Extrinsic column or the block hash from the table.
- Free balance before the run 3000000000000000 base units, after 2995558364584806 (14 decimals; the self-transfer returns to the same account, the difference is fees)
- Source: `evidence/canary_v3.json` (full receipts: events, weight, fee)
- Read-back: the block is fetched by hash, the extrinsic at the receipt's index must have the tx hash and
  decode to the same `Pallet.call` (`plumbline readback evidence/canary_v3.json` repeats it from a new connection).

| Chain | Call | Extrinsic | Tx hash | Block hash | Result | Events | Read-back |
|---|---|---|---|---|---|---|---|
| profile v3 — Portaldot V3 testnet | `System.remark_with_event` | `106424-1` | `0xf54430571069e41bfe629c6c77609a279d0ad82fa2a83e45b0c3575bc941e6f5` | `0xac1f0030783d7470925fb39b691046d58418ac138c4d603916506cb345353ec2` | success | Balances.Withdraw, System.Remarked, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`System.remark_with_event`) |
| profile v3 — Portaldot V3 testnet | `Balances.transfer_keep_alive` | `106425-1` | `0xa7effe5d5ab7d7c512690d285065fd83919f8b83dc04de1630e1e4ddff91d90b` | `0xf71728583059750fbdb8095730835eae53955148675b11cae98c90d3374b4c1d` | success | Balances.Withdraw, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Balances.transfer_keep_alive`) |
| profile v3 — Portaldot V3 testnet | `Utility.batch_all` | `106426-1` | `0x25e0352284f3a2455e03c7c02c625b8bd1ff692e244bafbc51d1adf20f5dfaa8` | `0x06a069139c6457bdcd841dd7cb274d986b621538befea8ecb0471988764319ab` | success | Balances.Withdraw, System.Remarked, Utility.ItemCompleted, Utility.BatchCompleted, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Utility.batch_all`) |

## Interim stand-in — not V3

Canary receipts written by `plumbline canary`. **Every row below is interim-sdk — not V3**: a local upstream
polkadot-sdk `substrate-node --dev` chain, used because the Portaldot V3 testnet endpoint did not resolve
when this run was made; signed with dev keys. It proves the runner (sign → submit → receipt →
read-back), not anything about V3.

- Run `20261014T144705Z` on `interim-sdk` (node/268, metadata sha256 `08667bc21e9eecbc…`), endpoint `ws://127.0.0.1:39944`
- Source: `evidence/canary_interim.json` (full receipts: events, weight, fee)
- Metadata hash: read at the run's first block; it differs from the genesis-pinned stand-in fixture
  because one Revive storage default on this runtime changes from block to block (see the record's note).
- Read-back: the block is fetched by hash, the extrinsic at the receipt's index must have the tx hash
  and decode to the same `Pallet.call` (`plumbline readback evidence/canary_interim.json` repeats it).

| Chain | Call | Extrinsic | Tx hash | Block hash | Result | Events | Read-back |
|---|---|---|---|---|---|---|---|
| interim-sdk — not V3 | `Balances.transfer_keep_alive` | `3040-2` | `0xa2aaeac54450ff5517aba1f9fd465a61d4bea2667206d5d0aad6cdf7cfc1ff21` | `0xd55c2e5fb66d2806c9c8b25464a27746128a7323b2bd581f29f55c756de37619` | success | Balances.Withdraw, System.NewAccount, Balances.Endowed, Balances.Transfer, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Balances.transfer_keep_alive`) |
| interim-sdk — not V3 | `System.remark_with_event` | `3041-2` | `0x7b99fb5baa547019245b442be12b0c85fa2fe35963f6e7ce66c476e889ec18df` | `0xd014d8849b019ad32a6c96ced0cefbacbbc42f7ba56d47b797fc47f707cfbb37` | success | Balances.Withdraw, System.Remarked, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`System.remark_with_event`) |
| interim-sdk — not V3 | `Revive.map_account` | `3042-2` | `0x64ac83906e686086f09eae41fb8c11cc9a1c78a8ec12e850a03e4dd012a83201` | `0xe03504b1f8437e6bd7ac782d9891d1885e00c266c4a6d4cebfe931d8f5cf0615` | success | Balances.Withdraw, Balances.Held, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Revive.map_account`) |

No row in this section is a Portaldot V3 transaction.
