# evidence

Measured outputs, each written by a script in `../scripts/` and named after the chain profile it was measured on.

| File | Producer | What |
|---|---|---|
| `metadata_1002.json`, `metadata_standin_sdk.json` | `record_metadata.py` | where and when each stored metadata blob was read (the stand-in is not V3) |
| `docs_1002.json` | `docs_baseline.py` | the mainnet docs' documented calls compared with the spec-1002 runtime |
| `guide_calls.json` | `guide_calls.py` | the V3 guide's documented call table at the pinned commit: names, `file:line` refs and the FAQ status reduced to its own closed key (documented / listed / awaiting); no guide text |
| `ledger_1002.json` (+ `.blake2-256`) | `build_ledger.py` | the drift ledger on `mainnet-1002`, stamped CACHED (measured from the stored blob): one verdict per documented entry from all four sources. mainnet-1002 is the read-only baseline, so the V3 guide entries' verdicts there describe mainnet spec 1002, not V3. Canonical JSON (sorted keys, no whitespace, UTF-8); the hash is blake2-256 over the file's bytes |
