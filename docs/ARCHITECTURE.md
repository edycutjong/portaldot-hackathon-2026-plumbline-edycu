# Architecture

Drawn from the code in `plumbline/` as of 2026-10-20 (not from a plan). Solid boxes exist in this repository and have
tests; dashed boxes and dashed arrows are **planned** and are not built. Chain measurements are `v3` (the Portaldot V3
testnet, spec 3002, 2026-10-19), `mainnet-1002` (read-only baseline, 2026-10-14) or the `interim-sdk` stand-in (**not V3**);
the one cited exception is the wasm cells inside `check` (see BASELINE-1002.md).

```mermaid
flowchart LR
    subgraph IN["Inputs (untrusted data)"]
        RTD["Portaldot mainnet docs<br/>readthedocs, 22 extrinsic pages"]
        GD["V3 testnet guide @ d671a32<br/>README, 5 demo scripts, FAQ<br/>(cloned outside the tree, read by AST only)"]
        MAIN["mainnet RPC<br/>wss://mainnet.portaldot.io<br/>read-only"]
        V3N["Portaldot V3 testnet<br/>wss://test-node-v3.portaldot.dev<br/>spec 3002: read + 3 canary txs"]
        INT["interim-sdk node<br/>local polkadot-sdk --dev<br/>stand-in, not V3"]
        APP["a dApp directory<br/>.py .ts .js + wasm artifacts"]
    end

    subgraph ENG["Engine: plumbline/ (each box backs a plumbline subcommand; verdicts.py and renames.py are shared tables)"]
        MD["metadata.py<br/>chain profiles, identity check,<br/>pinned-block metadata read"]
        DOCS["docs.py<br/>3 page layouts to documented calls"]
        GUIDE["guide.py<br/>README table + AST call sites + FAQ modules"]
        PLUMB["plumb.py + produce.py + verdicts.py + renames.py<br/>SQLite drift ledger, one verdict per entry,<br/>explicit rename rows, canonical export"]
        DIFF["diff.py<br/>normalise V13 / V14+, six diff kinds"]
        CHECK["check.py<br/>wasm import scan, call-site scan,<br/>exit codes"]
        CANARY["canary.py<br/>sign, submit, receipt, read-back"]
    end

    subgraph OUT["Outputs"]
        FIX["fixtures/*.scale<br/>stored metadata blobs"]
        TBL["evidence/docs_1002.json, guide_calls.json<br/>(scripts/docs_baseline.py, guide_calls.py)"]
        EVD["evidence/ledger_1002.json, ledger_v3.json<br/>+ blake2-256"]
        GOLD["fixtures/expected_check_1002.txt, expected_check_v3.txt<br/>golden dApp-check output"]
        PROOF["evidence/canary_v3.json<br/>docs/PROOF.md"]
        PROOFI["evidence/canary_interim.json<br/>interim-sdk, not V3"]
    end

    MAIN --> MD
    V3N --> MD
    INT --> MD
    MD --> FIX
    RTD -->|scripts/fetch_docs.py| DOCS
    GD -->|scripts/fetch_guide.py| GUIDE
    DOCS --> TBL
    GUIDE --> TBL
    FIX -->|docs_1002.json only| TBL
    TBL -->|default inputs| PLUMB
    DOCS -->|--docs-cache| PLUMB
    GUIDE -->|--guide-dir| PLUMB
    FIX --> PLUMB
    PLUMB --> EVD
    FIX --> DIFF
    MD -->|via cli.py| DIFF
    FIX --> CHECK
    MD -->|via cli.py, live profile| CHECK
    APP --> CHECK
    DOCS --> CHECK
    GUIDE --> CHECK
    PLUMB --> CHECK
    CHECK --> GOLD
    V3N --> CANARY
    INT --> CANARY
    MD --> CANARY
    CANARY -->|receipts + read-back| PROOF
    CANARY -->|receipts + read-back| PROOFI
    PROOF -->|--receipts: PROVEN needs a receipt| PLUMB

    LEDGER["Plumbline Ledger contract (Revive)<br/>PLANNED: attests report hashes on V3"]
    LEDGER -.-> V3N

    classDef planned stroke-dasharray: 5 5,fill:#fff,stroke:#999,color:#555
    classDef standin fill:#fff3cd,stroke:#b8860b
    class LEDGER planned
    class INT,PROOFI standin
```

## Flow, in words

1. **Read the runtime.** `metadata.py` connects through `substrate-interface`, checks the chain's identity (spec name,
   genesis hash, spec version) against the profile and fails loudly on a mismatch, then reads the metadata blob at one
   pinned block (a finalized head or genesis) and stores it with its sha256. Profiles: `mainnet-1002` (legacy type
   registry preset plus an Assets `u64` override), `v3` (no preset; identity pinned to the live testnet: `portaldot-v3`, genesis `0xd4f9acbc…`, spec 3002), `interim-sdk`
   (no preset), `stored:<file>` (offline).
2. **Read the documentation.** `docs.py` parses the mainnet extrinsic pages (three layouts) into documented calls with
   their arguments; `guide.py` reads the V3 guide (README call table, `compose_call` / `runtime_call` sites by AST, the
   FAQ module table) into the same shape, each entry carrying its `file:line` or page reference.
3. **Decide a verdict.** `plumb.py` joins the documented entries to one runtime's call surface and writes exactly one
   verdict per entry into a SQLite ledger, then exports it as canonical JSON (sorted keys, no whitespace, UTF-8) with its
   blake2-256. The verdict names live in `verdicts.py` only. A renamed call is DRIFTED only through an explicit row in
   `renames.py`, checked against the runtime; nothing is matched fuzzily. PROVEN needs a receipt whose profile and spec
   version equal the runtime's and whose read-back decodes to the same `Pallet.call`; the V3 ledger is built
   with `--receipts evidence/canary_v3.json`, so it has 3 PROVEN calls; `mainnet-1002` has none.
4. **Prove.** `canary.py` signs three calls and keeps each
   receipt (tx hash, block hash, extrinsic id, events), and `readback` re-fetches the block by hash and checks that the
   extrinsic at the receipt's index has the tx hash and decodes to the same call. On `v3` the signer is one funded account whose seed
   is read from a mode-600 file outside the repository and never printed or recorded; on the local `interim-sdk` chain it uses
   dev keys, which are refused on every other profile and on any non-loopback endpoint. `plumb` refuses a receipt whose profile or spec version differs from its runtime's (it trusts the record and does not
   re-read the chain) and refuses the stand-in itself, so an unedited stand-in receipt cannot become a mainnet or V3 verdict.
5. **Diff and check.** `diff.py` normalises V13 and V14+ metadata to one form and lists pallets, calls, arguments,
   constants and events that change between two runtimes (it prints a STAND-IN, NOT V3 banner at the top and bottom when a side is the
   stand-in; no number from that output is used in any document). `check.py` scans a dApp directory (Python and JS/TS call sites, wasm import sections) against one profile and
   exits 0, 1 (blocking findings) or 2 (usage or chain error).

## Invariants the code enforces

| Invariant | Where |
|---|---|
| Every verdict row carries profile, spec version and metadata hash | `plumb.py` schema and export; test in `tests/test_plumb.py` |
| Verdict strings are defined once | `verdicts.py`; a test bans the literals elsewhere |
| A recorded blob is labelled from its sha256; an unrecorded one is labelled "origin not recorded" and `plumb` refuses it, so a stand-in cannot be relabelled as V3 | `metadata.KNOWN_BLOBS`, `produce.py` |
| The stand-in chain cannot enter the ledger (`check` and `diff` can run on it and print a STAND-IN, NOT V3 banner) | `produce.py`, `plumb.py` refuse the profile and the blob; `allow_standin` is test-only |
| Dev keys never leave the stand-in chain | `canary.dev_keypair` (profile) and `canary.run` (loopback endpoint) |
| The V3 signing seed is never in the tree, never world-readable, never echoed | `canary.seed_keypair` (path outside the repo, mode 600, errors carry no seed); `tests/test_canary.py` |
| Third-party guide text is not in the tree at HEAD | `evidence/guide_calls.json` holds names and `file:line` only |
