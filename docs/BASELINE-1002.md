# BASELINE-1002

What Plumbline measured on the **Portaldot mainnet runtime, spec 1002**, read-only, on 2026-10-14. Every figure names the
file that holds it and the command that regenerates it. Nothing here was measured on Portaldot V3 (the V3 testnet
endpoint did not resolve on 2026-10-14), and no transaction was sent to mainnet.

**What "baseline" means.** The mainnet docs are compared with the runtime they are published for. The V3 guide's entries
(`v3-guide`, `v3-demo`, `v3-faq`) appear in the ledger below only to show what those calls look like on mainnet spec 1002;
their verdicts there are **not** V3 findings.

## The runtime

| Fact | Value | Producer · file | Date |
|---|---|---|---|
| Chain | `portaldot` spec 1002, tx version 2, `substrate-node` | `scripts/record_metadata.py` · `evidence/metadata_1002.json` | 2026-10-14 |
| Endpoint | `wss://mainnet.portaldot.io` (read-only) | same | 2026-10-14 |
| Genesis hash | `0x83b5212c69f85f3996f8696bb35bf913af51b176a5f53d0a74fd3a83dc0b8a54` | same | 2026-10-14 |
| Metadata | V13, 153205 bytes, sha256 `b87080f9349f936e859748df90ca7837d1e2d15cb02abba347499b8639869053` | same · `fixtures/metadata_1002.scale` | 2026-10-14 09:32 UTC |
| Runtime code hash (`:code`) | `0xc8e7e77fe84f1231787e75a751af48c52e68675ad55de062c81cc8df24066e55` | same | 2026-10-14 |
| Size | 31 pallets, 155 calls | `plumbline runtime -p stored:fixtures/metadata_1002.scale` | 2026-10-14 |

## The mainnet docs against that runtime

Command: `python scripts/fetch_docs.py && python scripts/docs_baseline.py --profile stored:fixtures/metadata_1002.scale --out evidence/docs_1002.json`
(re-run 2026-10-14 14:46 UTC, from freshly fetched pages). File: `evidence/docs_1002.json`.

| Measure | Count | Notes |
|---|---|---|
| Extrinsic docs pages parsed | 22 / 22 | 22 complete; three page layouts (plain, `api.tx`, dash) |
| Documented calls | 196 | 196 interface lines = 196 signatures, 0 unpaired |
| Match (name and arguments) | 117 | |
| Documented but absent from the runtime | 64 | all 64 are call-absent (the pallet exists, the call does not); 0 are pallet-absent |
| Documented, present, arguments differ | 15 | 4 of the 15 differ only by a leading underscore on the runtime's parameter name |
| Live calls whose names no page lists | 23 | 14 of them are in pallets that have a docs page; pallets with no docs page: Authorship, Gilt, Sudo; 2 are the live sides of the renamed proxy calls |

196 = 117 + 64 + 15. Every `treasury` call the docs list (6) is absent from the runtime.

Per-page counts are in the same file (`per_page`, and `rows` for each call with its documented and live arguments).

## The drift ledger

Command: `python scripts/build_ledger.py --profile stored:fixtures/metadata_1002.scale --runtime-record evidence/metadata_1002.json --out evidence/ledger_1002.json --docs-cache .cache/docs --guide-dir ~/.cache/plumbline-sources/guide`
(re-run 2026-10-14, byte-identical to the committed file). File: `evidence/ledger_1002.json`, blake2-256
`d067b31bb5721e2b9d99ac6d3ba5c94e3d31b66f9676bdc65ba7a3a82de21ee7` (`evidence/ledger_1002.json.blake2-256`).
320 verdict rows, each stamped with profile, spec version and metadata hash.

| Source | Verdicts on mainnet-1002 |
|---|---|
| `mainnet-docs` (196 calls) | ABSENT 62 · DRIFTED 2 · ARG_DRIFT 15 · UNPROVEN 117 |
| `runtime` (live calls no source names) | UNDOCUMENTED 21 · UNPROVEN 2 |
| `v3-demo` (guide sample code) | ABSENT 33 · UNKNOWN-DOC 1 · UNPROVEN 13 |
| `v3-guide` (guide README) | ABSENT 31 · ARGS-UNCHECKED 9 · UNKNOWN-DOC 2 |
| `v3-faq` (FAQ module table) | ABSENT 10 · UNPROVEN 2 |

- The docs' 64 absent = 62 ABSENT + 2 DRIFTED. The two DRIFTED rows are `proxy.createPure` and `proxy.killPure`, whose
  runtime equivalents `Proxy.anonymous` / `Proxy.kill_anonymous` take the same arguments by name and order (explicit rows
  in `plumbline/renames.py`, checked against the runtime).
- The runtime's 23 undocumented live calls = 21 UNDOCUMENTED + 2 UNPROVEN (the live sides of those two renames).
- **PROVEN: 0.** No row is PROVEN on mainnet-1002 because Plumbline sends no transaction to mainnet; UNPROVEN means
  "present, arguments match, no receipt".
- The V3-source rows are a baseline, not V3 findings (see the top of this file).

## The V3 guide, as parsed (pinned commit `d671a32`)

Command: `python scripts/guide_calls.py --guide-dir ~/.cache/plumbline-sources/guide --out evidence/guide_calls.json`
(re-run 2026-10-14, byte-identical). File: `evidence/guide_calls.json` (names and `file:line` references only, no guide text).

| Measure | Count |
|---|---|
| `compose_call` sites in the guide's 5 demo scripts, by AST | 46 (a text grep finds 47 lines; the extra one is a comment, `demo/04_solidity_demo.py:411`) |
| Other demo call sites | 1 `runtime_call` and 1 raw `state_call` (`ReviveApi.call`) |
| Guide README call-table names | 39, plus 3 explorer paths |
| FAQ modules | 12 (10 listed, 2 documented); 10 FAQ lines carry a bold "awaiting …" status |
| Unresolved / unaccounted | 0 / 0 |

## The dApp check on the 1002 profile

Command: `plumbline check examples/mainnet-dapp --target mainnet-1002 --offline` (re-run 2026-10-14; exit code 1; output
byte-identical to `fixtures/expected_check_1002.txt`). `examples/mainnet-dapp/` is a small engineered fixture, not a field
sample: 8 source files with one call site each and 1 contract artifact; each static call site cites its docs page or guide `file:line` (`src/router.ts` is a deliberate dynamic call).

Summary line: `9 findings in 9 files scanned · DRIFTED 1, ARG_DRIFT 2, ARGS-UNCHECKED 1, ABSENT 2, UNPROVEN 2, UNSCANNED 1 · blocking 5 · exit 1`.

`plumbline check` has two wasm cells, and both rest on measurements made on 2026-10-11, before this repository existed,
on a local chain running spec 1002; they are cited, not re-run here, and no receipt or other artifact for them is in this
repository.

- **seal0-only → `UNPROVEN`** (the cell this example's artifact hits). The measured module was a different, hand-written
  157-byte seal0-only module with two imports that instantiated on spec 1002; `examples/mainnet-dapp/contract/minimal_seal0.wasm`
  (140 bytes) was never instantiated, so the cell generalises one measurement to any seal0-only module.
- **`seal1.get_storage` + `seal2.set_storage` → `EXPECTED-REJECT`** (not exercised by the golden output; covered by
  `tests/test_check.py`). An ink! 5.1.1 artifact importing exactly that set was rejected by spec 1002 with `System.Other`;
  which import caused the rejection is inferred, not measured.

## Tests

`pytest -q` on HEAD (2026-10-14): **255 passed, 3 deselected** (the 3 live tests are opt-in, `pytest -m live`).

## Not in this file

- Anything on Portaldot V3: **pending** (V3 testnet unreachable on 2026-10-14).
- Canary receipts: all on a stand-in chain, not V3 — see `PROOF.md`. `plumbline diff` against the stand-in prints a
  "STAND-IN, NOT V3" banner and no number from it is used anywhere.
