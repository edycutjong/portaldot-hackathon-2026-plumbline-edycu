# examples/mainnet-dapp

An **engineered** fixture for `plumbline check`: a small dApp written the way an S1-era entrant would have
written it for Portaldot mainnet, from the official mainnet docs, plus one call copied from each of two V3
guide samples. Every call site cites the docs page or guide `file:line` it was copied from.

It is never run. It does not import an SDK, holds no keys and sends nothing; it exists so `check` has a
known input whose findings can be reviewed by hand.

```bash
plumbline check examples/mainnet-dapp --target mainnet-1002 --offline
```

The output for the `mainnet-1002` profile is stored in `../../fixtures/expected_check_1002.txt` and is
reproduced byte for byte by `tests/test_check_cli.py`. A golden output for Portaldot V3 is written only
after a real run against V3 metadata.

| File | Call | Copied from |
|---|---|---|
| `contract/minimal_seal0.wasm` | contract artifact, seal0 imports only | hand-assembled by `scripts/make_wasm.py`; stands in for a rent-era build (not a real contract) |
| `src/escrow.py` | `Contracts.instantiate_with_code` | mainnet docs, contracts page, interface 5 |
| `src/token.py` | `Assets.transfer` | mainnet docs, assets page, interface 29 |
| `src/notes.py` | `System.remark` | mainnet docs, system page, interface 6 |
| `src/nft.py` | `Nfts.mint` | V3 guide `demo/03_nfts_demo.py:154` |
| `src/evm.py` | `Revive.call` | V3 guide `demo/04_solidity_demo.py:421` |
| `src/send.ts` | `balances.transferKeepAlive` | mainnet docs, balances page, interface 3 |
| `src/vault.ts` | `proxy.createPure` | mainnet docs, proxy page, interface 3 |
| `src/router.ts` | `api.tx[pallet][call]` | a dynamic call: names chosen at run time |
