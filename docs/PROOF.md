# PROOF

Canary receipts written by `plumbline canary`. **Every row below is interim-sdk — not V3**: a local upstream
polkadot-sdk `substrate-node --dev` chain, used because the Portaldot V3 testnet endpoint did not resolve
when this run was made; signed with dev keys. It proves the runner (sign → submit → receipt →
read-back), not anything about V3.

- Run `20261014T124406Z` on `interim-sdk` (node/268, metadata sha256 `aa6ff724170ef209…`), endpoint `ws://127.0.0.1:39944`
- Source: `evidence/canary_interim.json` (full receipts: events, weight, fee)
- Metadata hash: read at the run's first block; it differs from the genesis-pinned stand-in fixture
  because one Revive storage default on this runtime changes from block to block (see the record's note).
- Read-back: the block is fetched by hash, the extrinsic at the receipt's index must have the tx hash
  and decode to the same `Pallet.call` (`plumbline readback evidence/canary_interim.json` repeats it).

| Chain | Call | Extrinsic | Tx hash | Block hash | Result | Events | Read-back |
|---|---|---|---|---|---|---|---|
| interim-sdk — not V3 | `Balances.transfer_keep_alive` | `646-2` | `0x54269528c314c683b874b199989af3bc57b6a5cf2e37aef39d6442ad72944e8f` | `0x1c65c9be10efb76a53d753418f6b2f7bf3a78837dcbabb32395f8fb27d081a0d` | success | Balances.Withdraw, System.NewAccount, Balances.Endowed, Balances.Transfer, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Balances.transfer_keep_alive`) |
| interim-sdk — not V3 | `System.remark_with_event` | `647-2` | `0xef5d91dd57396bf19ffdf4eb8a77bae828a6d7d6fc25ff613326244f799b2747` | `0x48a6876d7beb9c6ebaf78b4e9857a0560435cb1fb894a7972fcfdf2076ab7b42` | success | Balances.Withdraw, System.Remarked, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`System.remark_with_event`) |
| interim-sdk — not V3 | `Revive.map_account` | `648-2` | `0x3e33bf0fb73097a0b6006ac3338deec39cedce6266defd3664e1820932c7ae6d` | `0xeb068ccaca88df131f2087e5b22e51da7f6e094a2b1238148e1d04fc7eadd59c` | success | Balances.Withdraw, Balances.Held, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Revive.map_account`) |

No row here is a Portaldot V3 transaction. V3 canaries need V3 back online and tPOTv3 funds.
