# PROOF

Canary receipts written by `plumbline canary`. **Every row below is interim-sdk — not V3**: a local upstream
polkadot-sdk `substrate-node --dev` chain run while the Portaldot V3 testnet is unreachable, signed with
dev keys. It proves the runner (sign → submit → receipt → read-back), not anything about V3.

- Run `20261014T122613Z` on `interim-sdk` (node/268, metadata sha256 `e4f00ed9ad6cc82c…`), endpoint `ws://127.0.0.1:39944`
- Source: `evidence/canary_interim.json` (full receipts: events, weight, fee)
- Read-back: the block is fetched by hash, the extrinsic at the receipt's index must have the tx hash
  and decode to the same `Pallet.call` (`plumbline readback evidence/canary_interim.json` repeats it).

| Chain | Call | Extrinsic | Tx hash | Block hash | Result | Events | Read-back |
|---|---|---|---|---|---|---|---|
| interim-sdk — not V3 | `Balances.transfer_keep_alive` | `291-2` | `0x74d80bcc2b72c0c156192b5b885bfa28c15f4032fc3f0e02bd3e57ff1d2e90ac` | `0xd34796053aed856351bac95291b5b12782814a1fc164518bd4a14f1147fdbd75` | success | Balances.Withdraw, System.NewAccount, Balances.Endowed, Balances.Transfer, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Balances.transfer_keep_alive`) |
| interim-sdk — not V3 | `System.remark_with_event` | `292-2` | `0xea732d66084a0ca57c00769807e78ab202d0e16de044255f3c45155421ae7ead` | `0xccf3f8817f2e7375bfd901f94b42b25f14754a166887fa2dda9c4a6db4e430fa` | success | Balances.Withdraw, System.Remarked, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`System.remark_with_event`) |
| interim-sdk — not V3 | `Revive.map_account` | `293-2` | `0xbe445aee72d0c6431d30f97ead51b470734c835d701000f1f53d1b0fbcc7cbb7` | `0x96dc441ebad3030c1f68e59b0d346c42435557efc040b266799052b520189aeb` | success | Balances.Withdraw, Balances.Held, Balances.Deposit, TransactionPayment.TransactionFeePaid, System.ExtrinsicSuccess | ok (`Revive.map_account`) |

No row here is a Portaldot V3 transaction. V3 canaries need V3 back online and tPOTv3 funds.
