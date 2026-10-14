# Architecture

Drawn from the code in `plumbline/` as of 2026-10-14 (not from a plan). Solid boxes exist in this repository and have
tests; dashed boxes and dashed arrows are **planned** and are not built. The Portaldot V3 testnet did not resolve on
2026-10-14, so the V3 path is planned and every chain measurement below is `mainnet-1002` (read-only) or the
`interim-sdk` stand-in (**not V3**).

```mermaid
flowchart LR
    subgraph IN["Inputs (untrusted data)"]
        RTD["Portaldot mainnet docs<br/>readthedocs, 22 extrinsic pages"]
        GD["V3 testnet guide @ d671a32<br/>README, 5 demo scripts, FAQ<br/>(cloned outside the tree, read by AST only)"]
        MAIN["mainnet RPC<br/>wss://mainnet.portaldot.io<br/>read-only"]
        INT["interim-sdk node<br/>local polkadot-sdk --dev<br/>stand-in, not V3"]
        APP["a dApp directory<br/>.py .ts .js + wasm artifacts"]
    end

    subgraph ENG["Engine: plumbline/ (each box backs a plumbline subcommand; verdicts.py and renames.py are shared tables)"]
        MD["metadata.py<br/>chain profiles, identity check,<br/>pinned-block metadata read"]
        DOCS["docs.py<br/>3 page layouts to documented calls"]
        GUIDE["guide.py<br/>README table + AST call sites + FAQ modules"]
        PLUMB["plumb.py + verdicts.py + renames.py<br/>SQLite drift ledger, one verdict per entry,<br/>explicit rename rows, canonical export"]
        DIFF["diff.py<br/>normalise V13 / V14+, six diff kinds"]
        CHECK["check.py<br/>wasm import scan, call-site scan,<br/>exit codes"]
        CANARY["canary.py<br/>sign, submit, receipt, read-back"]
    end

    subgraph OUT["Outputs"]
        FIX["fixtures/*.scale<br/>stored metadata blobs"]
        TBL["evidence/docs_1002.json, guide_calls.json<br/>(scripts/docs_baseline.py, guide_calls.py)"]
        EVD["evidence/ledger_1002.json<br/>+ blake2-256"]
        GOLD["fixtures/expected_check_1002.txt<br/>golden dApp-check output"]
        PROOF["evidence/canary_interim.json<br/>docs/PROOF.md<br/>interim-sdk, not V3"]
    end

    MAIN --> MD
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
    MD --> DIFF
    FIX --> CHECK
    MD --> CHECK
    APP --> CHECK
    PLUMB --> CHECK
    CHECK --> GOLD
    INT --> CANARY
    MD --> CANARY
    CANARY -->|receipts + read-back| PROOF

    V3["Portaldot V3 testnet<br/>PLANNED: endpoint did not resolve"]
    LEDGER["Plumbline Ledger contract (Revive)<br/>PLANNED: attests report hashes on V3"]
    V3CAN["V3 canaries, tx per verdict<br/>PLANNED: needs V3 + tPOTv3 funds"]
    V3 -.-> MD
    CANARY -.-> V3CAN
    V3CAN -.-> V3
    V3CAN -.->|PROVEN needs a receipt| PLUMB
    LEDGER -.-> V3

    classDef planned stroke-dasharray: 5 5,fill:#fff,stroke:#999,color:#555
    classDef standin fill:#fff3cd,stroke:#b8860b
    class V3,LEDGER,V3CAN planned
    class INT,PROOF standin
```

## Flow, in words

1. **Read the runtime.** `metadata.py` connects through `substrate-interface`, checks the chain's identity (spec name,
   genesis hash, spec version) against the profile and fails loudly on a mismatch, then reads the metadata blob at one
   pinned block (a finalized head or genesis) and stores it with its sha256. Profiles: `mainnet-1002` (legacy type
   registry preset plus an Assets `u64` override), `v3` (no preset, a stub until the testnet resolves), `interim-sdk`
   (no preset), `stored:<file>` (offline).
2. **Read the documentation.** `docs.py` parses the mainnet extrinsic pages (three layouts) into documented calls with
   their arguments; `guide.py` reads the V3 guide (README call table, `compose_call` / `runtime_call` sites by AST, the
   FAQ module table) into the same shape, each entry carrying its `file:line` or page reference.
3. **Decide a verdict.** `plumb.py` joins the documented entries to one runtime's call surface and writes exactly one
   verdict per entry into a SQLite ledger, then exports it as canonical JSON (sorted keys, no whitespace, UTF-8) with its
   blake2-256. The verdict names live in `verdicts.py` only. A renamed call is DRIFTED only through an explicit row in
   `renames.py`, checked against the runtime; nothing is matched fuzzily. PROVEN needs a receipt whose profile and spec
   version equal the runtime's and whose read-back decodes to the same `Pallet.call`; there is none for any real
   Portaldot chain yet, so the shipped ledger has no PROVEN row.
4. **Prove (runner only).** `canary.py` signs three calls with dev keys on the local `interim-sdk` chain, keeps each
   receipt (tx hash, block hash, extrinsic id, events), and `readback` re-fetches the block by hash and checks that the
   extrinsic at the receipt's index has the tx hash and decodes to the same call. Dev keys are refused on every other
   profile and on any non-loopback endpoint. `plumb` refuses these receipts, so they can never become a mainnet or V3
   verdict.
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
| The stand-in chain cannot produce a verdict | `plumb.py` refuses the profile and the blob |
| Dev keys never leave the stand-in chain | `canary.dev_keypair` |
| Third-party guide text is not copied into the repo | `evidence/guide_calls.json` holds names and `file:line` only |
