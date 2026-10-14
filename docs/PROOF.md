# PROOF

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

No row here is a Portaldot V3 transaction. V3 canaries need V3 back online and tPOTv3 funds.
