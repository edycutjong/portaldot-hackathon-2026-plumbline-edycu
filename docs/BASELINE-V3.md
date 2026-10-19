# BASELINE-V3

What Plumbline measured on the **Portaldot V3 testnet** (profile `v3`, spec `portaldot-v3`/3002), on 2026-10-19 18:15 UTC
(= 2026-10-20 01:15 WIB). Every figure names the file that holds it and the command that regenerates it. All chain access
was read-only except the three canary transactions in [`PROOF.md`](PROOF.md).

## What the docs and the first V3 measurements said, against what the chain reports

| Fact | Before V3 came back | Live V3 | Source of "before" | Source of "live" |
|---|---|---|---|---|
| Endpoint | `wss://testnetv3-node.feso-apps.xyz` (no DNS on 2026-10-14, per `PROOF.md`'s interim section) | `wss://test-node-v3.portaldot.dev` | V3 testnet guide (`ItsCogumellum/portaldot-v3-testnet-guide@d671a32`) §1.1 | `evidence/metadata_v3.json` |
| Spec name | `portaldotV3` | `portaldot-v3` | third-party measurement, `finfreedom/portaldot-ink-toolchain` README (not stored in this repository; date not re-verified) | `evidence/metadata_v3.json` |
| Spec version | 3000 | **3002** | same | same |
| Token symbol | `tPOTv3` | **`tPOT`** | V3 guide §1.1, §3.1 | `system_properties`, below |
| Pallets | 70 | **69** | same third-party measurement | `evidence/baseline_v3.json` |
| EVM chain id, decimals | 420420777, 14 | 420420777 (`Revive.ChainId` constant in `fixtures/metadata_v3.scale`), 14 | V3 testnet guide §1.1, §3.1 | `system_properties`: `tokenDecimals 14` |

`system_properties` on 2026-10-19: `ss58Format 42, tokenDecimals 14, tokenSymbol tPOT`. The three differences (spec name,
spec version, symbol) and the pallet count are what the written sources got wrong or what moved when the testnet was
re-launched; Plumbline does not know which. The V3 profile now pins what the chain reports, and rejects the old values
(`tests/test_metadata.py::test_v3_profile_is_pinned_to_the_live_testnet_identity`).

## The runtime

| Fact | Value | Producer · file |
|---|---|---|
| Chain | `portaldot-v3` spec 3002, tx version 2, impl `portaldot-v3-node` | `scripts/record_metadata.py` · `evidence/metadata_v3.json` |
| Genesis hash | `0xd4f9acbcf7f52388d8b095abe3c198d19fc510e0343e643e87cfbccb155283bd` | same |
| Runtime code hash (`:code`) | `0xedc03c9dc81d39fea5c02893cc34cf2ec898d55d7ee8f8e89eb27597a724c7c2` | same |
| Metadata | V14, 561916 bytes, sha256 `381014358cc17ba6274f4de8e54846534f0986f311e27ac696cbd8af5696394c`, fetched 2026-10-19T18:15:54+00:00 at block `0x80f9dc0b…4892` | same · `fixtures/metadata_v3.scale` |
| Size | 69 pallets, 480 calls, 228 constants, 494 events | `scripts/baseline_counts.py` · `evidence/baseline_v3.json` |

**The metadata hash moves between blocks.** Read at block 106485 (2026-10-19 ~18:26 UTC) the same runtime (same `:code`
hash, same length 561916) hashes to `963152285a7f94083ed20f938126cb65c325a9717156e4d5b6561b9e9f2f3456`: one byte differs, at
offset 549822, inside the default value of the storage item `Revive.EthBlockBuilderIR`. Re-reading the stored block
returns the stored blob exactly. So a stamp on any V3 row means "metadata read at that block"; the stored blob is the one
at `0x80f9dc0b…4892`, and the `:code` hash is the stable identity. A third value (`c64385ba…`) appears at blocks 106422–106473, which include the three canary blocks. The same behaviour was seen on the interim stand-in chain.

The mainnet side of the comparison: spec 1002, metadata V13, 31 pallets, 155 calls, 69 constants, 119 events
(`docs/BASELINE-1002.md`; same `evidence/baseline_v3.json`).

## Diff: `mainnet-1002` → `v3`

Command: `plumbline diff stored:fixtures/metadata_1002.scale stored:fixtures/metadata_v3.scale --json` →
`evidence/diff_1002_v3.json` (516 rows). The two runtimes share 29 pallets; 40 are new in V3 and 2 are gone.

| Row kind | Count | Reading |
|---|---|---|
| Added pallet | 40 | e.g. Revive, Nfts, Uniques, Broker, Beefy, MessageQueue, Preimage, NominationPools, MultiAssetBounties |
| Removed pallet | 2 | `Gilt`, `RandomnessCollectiveFlip` |
| Added call (in a pallet both runtimes have) | 73 | |
| Removed call | 12 | listed below |
| Renamed call | 2 | `Proxy.anonymous` → `Proxy.create_pure`, `Proxy.kill_anonymous` → `Proxy.kill_pure` (explicit rename rows, `plumbline/renames.py`) |
| Argument changes | 99 | 84 of them keep the argument names and change only a type |
| Constant changes | 119 | 14 type-only |
| Event changes | 169 | |

Removed calls: `Assets.destroy`, `Authorship.set_uncles`, `Balances.set_balance`, **`Balances.transfer`**,
`Contracts.claim_surcharge`, `Staking.set_history_depth`, `Staking.update_staking_limits`, `System.fill_block`,
`System.set_changes_trie_config`, `Treasury.approve_proposal`, `Treasury.propose_spend`, `Treasury.reject_proposal`
(`calls_removed` in `evidence/baseline_v3.json`).

### Headline drift

1. **`Balances.transfer` does not exist on V3.** Balances on V3 has `transfer_allow_death` and `transfer_keep_alive`
   (`balances_v3` in `evidence/baseline_v3.json`). The mainnet docs list `Balances.transfer`; the ledger's verdict for it on
   V3 is `ABSENT` (`evidence/ledger_v3.json`, source `mainnet-docs`). Plumbline reads this from metadata names; it does not
   assert that `transfer_allow_death` takes the old call's semantics.
2. **The 2021 proxy names are gone; the modern ones are there.** `Proxy.create_pure` / `kill_pure` exist on V3; mainnet
   spec 1002 has `anonymous` / `kill_anonymous` (the two explicit rename rows). `plumbline check` on the example dapp, which
   calls `proxy.createPure`, reports it present on V3 (`evidence/check_v3.txt`) and DRIFTED on spec 1002.
3. **Five of the twelve modules the V3 guide's FAQ lists for V3.0 are not on the V3 testnet.** By pallet name, absent from the 69:
   `conviction_voting`, `cumulus_parachain_system`, `ibridge`, `paras`, `referenda`. Seven are present
   (`evidence/ledger_v3.json`, source `v3-faq`: ABSENT 5, UNPROVEN 7). A name match, not a feature test: `RegistrarPara`
   and `RegistrarRelay` exist, `Referenda` and `ConvictionVoting` do not.
4. **The mainnet docs match the V3 runtime far better than the mainnet one** (194 of 196 documented calls match by name and arguments on V3, against 117 of 196 on spec 1002). Joining the two ledgers on the 196 documented calls
   (`evidence/ledger_1002.json` vs `evidence/ledger_v3.json`, source `mainnet-docs`): the 79 calls whose docs and runtime
   disagree on spec 1002 (64 absent, 15 with different arguments) become 2 on V3. All 64 absent calls exist on V3 (the six
   `Treasury` calls the docs list, `spend`, `payout`, … among them); 14 of the 15 argument mismatches now match. The two that
   disagree on V3 are `Balances.transfer` (absent) and `ElectionProviderMultiPhase.governance_fallback` (arguments).
   Plumbline cannot say which runtime the docs were written for.
5. **Spec 3002 and `tPOT`, not 3000 and `tPOTv3`** (first table above).
6. **Fee constants changed.** `TransactionPayment.TransactionByteFee` and `WeightToFee` are removed constants; `OperationalFeeMultiplier`
   is added; `Balances.ExistentialDeposit` changed and reads `10000000000000` base units on V3 (0.1 tPOT at 14 decimals;
   `get_constant` on 2026-10-19; `const-changed` row in `evidence/diff_1002_v3.json`).

## The documented calls against V3

Command: `python scripts/build_ledger.py --profile stored:fixtures/metadata_v3.scale --runtime-record evidence/metadata_v3.json --receipts evidence/canary_v3.json --out evidence/ledger_v3.json`
→ `evidence/ledger_v3.json`, blake2-256 `dc808404744a4cf746264b329a302d62e17a9a33f202c147cd24696bc2c21e34`,
549 verdict rows, every row stamped `v3` / 3002 / the metadata hash. `tests/test_check_cli.py` rebuilds it byte for byte.

| Source | Verdicts on `v3` |
|---|---|
| `mainnet-docs` (196 calls) | PROVEN 3 · UNPROVEN 191 · ABSENT 1 · ARG_DRIFT 1 |
| `v3-demo` (46 `compose_call` sites + 1 runtime-API call) | PROVEN 1 · UNPROVEN 45 · UNKNOWN-DOC 1 |
| `v3-guide` (README call tables + Node Explorer paths) | ARGS-UNCHECKED 40 · UNKNOWN-DOC 2 |
| `v3-faq` (12 modules) | UNPROVEN 7 · ABSENT 5 |
| `runtime` (live calls no source names) | UNDOCUMENTED 252 |

- All 46 `compose_call` sites in the V3 guide's demo scripts name a call that exists on V3: 46 distinct calls (one site is
  dynamic and resolves to two candidates), 45 UNPROVEN and 1 PROVEN by the canary below: the guide's `compose_call` sample code does not call anything V3 lacks (its one runtime-API call, `ReviveApi.call`, cannot be checked from V14 metadata).
- 195 of the 196 calls the mainnet docs list exist on V3 (the one absent is `Balances.transfer`); 194 of them also match the
  documented arguments. The one that exists with different arguments is `ElectionProviderMultiPhase.governance_fallback` (documented `maybe_max_voters, maybe_max_targets`; live: none).
- The three `UNKNOWN-DOC` rows are the `ReviveApi` runtime API: V14 metadata as Plumbline reads it lists no runtime
  APIs, so it can say nothing about them. That is a reader limit, not drift.
- PROVEN means a canary receipt exists for that call on this runtime (3 calls, [`PROOF.md`](PROOF.md)). UNPROVEN means
  the call exists by name and no transaction has been sent. No verdict claims a call *works* beyond those three.
- 252 live calls are named by no documentation source.

## `plumbline check` on V3

`plumbline check examples/mainnet-dapp --target v3` (live, 2026-10-19; `evidence/check_v3.txt`): 9 findings in
9 files, **blocking 0**, exit 0. The same example on mainnet spec 1002 blocks on 5 (`fixtures/expected_check_1002.txt`).
V3 rows: UNPROVEN 5, ARGS-UNCHECKED 2, COMPAT-UNMEASURED 1 (a seal0-only contract: the tool has no measurement of
seal0 modules on V3), UNSCANNED 1 (dynamic `api.tx[...]`). An offline golden for this run is
`fixtures/expected_check_v3.txt`.

## What this does not establish

- Behaviour. A name and argument match is not a successful call; the only calls with a transaction are the three in `PROOF.md`.
- Anything about V3 after 2026-10-19 18:15 UTC. The runtime can be upgraded; the profile pins spec 3002 and refuses another
  spec version rather than measure it silently.
- Why the written sources differ from the chain: a re-launch, a corrected guide, or a mistake in the third-party
  measurement are all consistent with these rows.
