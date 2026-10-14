# evidence

Measured outputs, each written by a script in `../scripts/` or a `plumbline` command, and named after the chain
profile it was measured on.

| File | Producer | What |
|---|---|---|
| `metadata_1002.json`, `metadata_standin_sdk.json` | `record_metadata.py` | where and when each stored metadata blob was read (the stand-in is not V3) |
| `docs_1002.json` | `docs_baseline.py` | the mainnet docs' documented calls compared with the spec-1002 runtime |
| `guide_calls.json` | `guide_calls.py` | the V3 guide's documented call table at the pinned commit: names, `file:line` refs and the FAQ status reduced to its own closed key (documented / listed / awaiting); no guide text |
| `ledger_1002.json` (+ `.blake2-256`) | `build_ledger.py` | the drift ledger on `mainnet-1002`, stamped CACHED (measured from the stored blob): one verdict per documented entry from all four sources. mainnet-1002 is the read-only baseline, so the V3 guide entries' verdicts there describe mainnet spec 1002, not V3. Canonical JSON (sorted keys, no whitespace, UTF-8); the hash is blake2-256 over the file's bytes |
| `canary_interim.json` | `plumbline canary --profile interim-sdk` | **interim-sdk — not V3.** Canary receipts from the local stand-in chain, signed with dev keys: `Balances.transfer_keep_alive`, `System.remark_with_event`, `Revive.map_account`, each with tx hash, block hash, extrinsic id, events, weight, fee and a read-back of the extrinsic from its block. Proves the runner, not V3; `plumb` refuses these receipts. `docs/PROOF.md` is generated from it; `plumbline readback evidence/canary_interim.json` re-reads every row (needs the interim chain, `scripts/interim_node.sh start`) |
| `readback_interim.txt` | `plumbline readback evidence/canary_interim.json` | **interim-sdk — not V3.** Output of re-reading the three canary receipts from a new process after the interim node was restarted from its archive data dir: 3/3 MATCH |
