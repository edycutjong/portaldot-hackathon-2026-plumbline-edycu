"""Canary transactions: sign a documented call, submit it, keep the receipt, and read it back (I1).

v0 runs only on the local `interim-sdk` chain with the well-known dev keys (`//Alice`), so it proves the
runner end to end before any V3 funds exist. It is a stand-in, not V3: every receipt it writes carries the
label `interim-sdk — not V3` and the profile and spec version it was sent to; `plumb` refuses a receipt whose
profile or spec version is not its runtime's, and refuses the stand-in runtime itself, so none of these
receipts can become a V3 (or mainnet) verdict.

One run:
  1. `//Alice` sends `Balances.transfer_keep_alive` to a fresh per-run account `//Plumbline//<run id>`.
  2. That account sends `System.remark_with_event` with the run id as the remark.
  3. That account sends `Revive.map_account` (fresh account, so the mapping is new).
Each receipt holds the tx hash, block hash and number, extrinsic id `<block>-<index>`, success or the decoded
dispatch error, the events of that extrinsic, weight and fee. Read-back fetches the block by hash, takes the
extrinsic at the receipt's index, and checks that its hash is the tx hash and that it decodes to the same
`Pallet.call`; only then is `readback_ok` 1. A failed call is still a receipt.

Dev keys are refused on every profile other than `interim-sdk` (`dev_keypair`), and a dev profile's endpoint
must be on this machine (loopback). Mainnet is never written to.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import metadata as md

DEV_PROFILES = ("interim-sdk",)
LABEL = "interim-sdk — not V3"
FORMAT = "plumbline-canary/1"
TRANSFER_AMOUNT = 10**18  # dev-chain units; ED on this runtime is 10**14 (Balances.ExistentialDeposit)


class CanaryError(RuntimeError):
    """A canary the runner refuses to send, or a run that stopped part way (`partial` holds what it sent)."""

    def __init__(self, msg: str, partial: dict | None = None):
        super().__init__(msg)
        self.partial = partial


LOOPBACK = ("127.0.0.1", "localhost", "::1")


def dev_keypair(profile: str, uri: str):
    """A keypair from a dev URI (`//Alice`, `//Plumbline//run`), only on a dev profile."""
    if profile not in DEV_PROFILES:
        raise CanaryError(f"dev keys are refused on {profile}: only {', '.join(DEV_PROFILES)} accepts them")
    if not uri.startswith("//"):
        raise CanaryError("dev_keypair takes a dev derivation URI starting with //")
    from substrateinterface import Keypair

    return Keypair.create_from_uri(uri)


def _events(receipt) -> list[dict]:
    out = []
    for event in receipt.triggered_events:
        value = event.value
        ev = value.get("event", value)
        out.append({"pallet": ev.get("module_id"), "event": ev.get("event_id"),
                    "attributes": json.loads(json.dumps(ev.get("attributes"), default=str))})
    return out


def _dispatch_error(receipt) -> str | None:
    if receipt.is_success:
        return None
    err = receipt.error_message or {}
    if isinstance(err, dict):
        return f"{err.get('type', '?')}.{err.get('name', '?')}"
    return str(err)


def readback(substrate, block_hash: str, index: int, tx_hash: str) -> tuple[bool, str | None]:
    """(ok, decoded Pallet.call) for the extrinsic at `index` of the block `block_hash`."""
    block = substrate.get_block(block_hash=block_hash)
    extrinsics = block["extrinsics"]
    if index >= len(extrinsics):
        return False, None
    xt = extrinsics[index].value
    call = xt.get("call", {})
    decoded = f"{call.get('call_module')}.{call.get('call_function')}"
    xt_hash = xt.get("extrinsic_hash")
    same_hash = xt_hash is not None and xt_hash.lower().removeprefix("0x") == tx_hash.lower().removeprefix("0x")
    return same_hash, decoded


def dispatch_outcome(substrate, block_hash: str, index: int) -> bool | None:
    """True / False from the block's System.ExtrinsicSuccess / ExtrinsicFailed event for extrinsic `index`,
    read from chain state at that block; None if neither is there."""
    for event in substrate.get_events(block_hash=block_hash):
        value = event.value
        phase = value.get("phase")
        # substrate-interface 1.8 flattens the phase to 'ApplyExtrinsic' + 'extrinsic_idx'; a raw decode
        # keeps the enum form {'ApplyExtrinsic': n}
        applied = value.get("extrinsic_idx") if phase == "ApplyExtrinsic" else \
            phase.get("ApplyExtrinsic") if isinstance(phase, dict) else None
        if applied != index:
            continue
        ev = value.get("event", value)
        if ev.get("module_id") == "System" and ev.get("event_id") in ("ExtrinsicSuccess", "ExtrinsicFailed"):
            return ev["event_id"] == "ExtrinsicSuccess"
    return None


def send(substrate, keypair, pallet: str, call: str, params: dict, stamp: dict) -> dict:
    """Sign, submit, wait for inclusion, read back. Returns one receipt row."""
    composed = substrate.compose_call(call_module=pallet, call_function=call, call_params=params)
    xt = substrate.create_signed_extrinsic(call=composed, keypair=keypair)
    receipt = substrate.submit_extrinsic(xt, wait_for_inclusion=True)
    block_hash = receipt.block_hash
    tx_hash = receipt.extrinsic_hash if receipt.extrinsic_hash.startswith("0x") else "0x" + receipt.extrinsic_hash
    try:
        block_number = substrate.get_block_number(block_hash)
        index = receipt.extrinsic_idx
        same_hash, decoded = readback(substrate, block_hash, index, tx_hash)
        success = bool(receipt.is_success)
    except Exception as exc:  # included on chain, but reading it back failed: keep what is known
        return {"side": "substrate", "pallet": pallet, "call": call, "signer": keypair.ss58_address,
                "tx_hash": tx_hash, "block_hash": block_hash, "block_number": None, "extrinsic_id": None,
                "success": None, "dispatch_error": None, "events_json": "[]", "weight_json": "null", "fee": None,
                "readback_ok": 0, "readback_call": None, "readback_error": f"{type(exc).__name__}: {exc}",
                "label": LABEL, **stamp}
    return {
        "side": "substrate", "pallet": pallet, "call": call, "signer": keypair.ss58_address,
        "tx_hash": tx_hash, "block_hash": block_hash, "block_number": block_number,
        "extrinsic_id": f"{block_number}-{index}", "success": int(success),
        "dispatch_error": _dispatch_error(receipt), "events_json": json.dumps(_events(receipt), sort_keys=True),
        "weight_json": json.dumps(receipt.weight, sort_keys=True, default=str),
        "fee": str(receipt.total_fee_amount), "readback_ok": int(same_hash and decoded == f"{pallet}.{call}"),
        "readback_call": decoded, "label": LABEL, **stamp,
    }


def run(profile_name: str, url: str | None = None, run_id: str | None = None) -> dict:
    """One canary run on a dev profile. Returns the evidence record (runtime stamp + receipts)."""
    profile = md.get_profile(profile_name)
    if not profile.writes_txs:
        raise CanaryError(f"{profile_name} is read-only: no transaction is ever sent to it")
    if profile_name not in DEV_PROFILES:
        raise CanaryError(f"canary v0 runs only on {', '.join(DEV_PROFILES)} (dev keys); {profile_name} needs a "
                          f"funded per-run seed, which is not implemented yet")
    endpoint = url or profile.url
    if urlparse(endpoint).hostname not in LOOPBACK:
        raise CanaryError(f"dev keys go only to a chain on this machine; {endpoint} is not a loopback address")
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    alice = dev_keypair(profile_name, "//Alice")
    runner = dev_keypair(profile_name, f"//Plumbline//{run_id}")
    substrate = md.connect(profile, url)  # fails loud on another chain (genesis, spec name, spec version)
    try:
        head = md.pinned_head(substrate)
        version = substrate.rpc_request("state_getRuntimeVersion", [head])["result"]
        blob = md.fetch_metadata_blob(substrate, head)
        record = {
            "format": FORMAT, "label": LABEL, "profile": profile_name, "endpoint": endpoint,
            "spec_name": version["specName"], "spec_version": version["specVersion"],
            "metadata_sha256": md.sha256_hex(blob), "run_id": run_id, "started_at_block": head, "receipts": [],
            "note": "local stand-in chain (upstream polkadot-sdk substrate-node --dev); not Portaldot V3; dev keys; "
                    "no number here is a V3 finding. metadata_sha256 is read at started_at_block; it differs from "
                    "the genesis-pinned fixtures/metadata_standin_sdk.scale because one Revive storage default on "
                    "this runtime changes from block to block",
        }
        stamp = {"profile": profile_name, "spec_version": version["specVersion"]}
        plan = [(alice, "Balances", "transfer_keep_alive", {"dest": runner.ss58_address, "value": TRANSFER_AMOUNT}),
                (runner, "System", "remark_with_event", {"remark": f"plumbline canary {run_id}"}),
                (runner, "Revive", "map_account", {})]
        for keypair, pallet, call, params in plan:
            try:
                record["receipts"].append(send(substrate, keypair, pallet, call, params, stamp))
            except Exception as exc:  # keep what was already sent: those receipts are real
                raise CanaryError(f"{pallet}.{call} failed before inclusion was confirmed: {exc}",
                                  partial=record) from exc
    finally:
        substrate.close()
    return record


def verify(record: dict, url: str | None = None) -> list[dict]:
    """Re-read every receipt of a record from the chain (a new connection): one result row per receipt."""
    profile = md.get_profile(record["profile"])
    substrate = md.connect(profile, url or record.get("endpoint"))
    rows = []
    try:
        for r in record["receipts"]:
            if not r.get("extrinsic_id"):  # included but never read back: nothing to compare against
                rows.append({"extrinsic_id": None, "tx_hash": r["tx_hash"], "call": f"{r['pallet']}.{r['call']}",
                             "decoded": None, "block_number": None, "success_on_chain": None, "match": False})
                continue
            index = int(r["extrinsic_id"].split("-")[1])
            ok, decoded = readback(substrate, r["block_hash"], index, r["tx_hash"])
            number = substrate.get_block_number(r["block_hash"])
            outcome = dispatch_outcome(substrate, r["block_hash"], index)
            match = (ok and decoded == f"{r['pallet']}.{r['call']}" and number == r["block_number"]
                     and outcome == bool(r["success"]))
            rows.append({"extrinsic_id": r["extrinsic_id"], "tx_hash": r["tx_hash"], "call": f"{r['pallet']}.{r['call']}",
                         "decoded": decoded, "block_number": number, "success_on_chain": outcome, "match": match})
    finally:
        substrate.close()
    return rows


def proof_markdown(record: dict) -> str:
    """docs/PROOF.md from a canary record: one row per receipt, labelled on every row."""
    lines = [
        "# PROOF",
        "",
        f"Canary receipts written by `plumbline canary`. **Every row below is {LABEL}**: a local upstream",
        "polkadot-sdk `substrate-node --dev` chain, used because the Portaldot V3 testnet endpoint did not resolve",
        "when this run was made; signed with dev keys. It proves the runner (sign → submit → receipt →",
        "read-back), not anything about V3.",
        "",
        f"- Run `{record['run_id']}` on `{record['profile']}` ({record['spec_name']}/{record['spec_version']}, "
        f"metadata sha256 `{record['metadata_sha256'][:16]}…`), endpoint `{record['endpoint']}`",
        "- Source: `evidence/canary_interim.json` (full receipts: events, weight, fee)",
        "- Metadata hash: read at the run's first block; it differs from the genesis-pinned stand-in fixture",
        "  because one Revive storage default on this runtime changes from block to block (see the record's note).",
        "- Read-back: the block is fetched by hash, the extrinsic at the receipt's index must have the tx hash",
        "  and decode to the same `Pallet.call` (`plumbline readback evidence/canary_interim.json` repeats it).",
        "",
        "| Chain | Call | Extrinsic | Tx hash | Block hash | Result | Events | Read-back |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in record["receipts"]:
        events = ", ".join(f"{e['pallet']}.{e['event']}" for e in json.loads(r["events_json"]))
        result = ("success" if r["success"] else "outcome not read back" if r["success"] is None
                  else f"failed: {r['dispatch_error']}")
        lines.append(f"| {LABEL} | `{r['pallet']}.{r['call']}` | `{r['extrinsic_id'] or '-'}` | `{r['tx_hash']}` | "
                     f"`{r['block_hash']}` | {result} | {events} | "
                     f"{'ok' if r['readback_ok'] else 'FAILED'} (`{r['readback_call']}`) |")
    lines += ["", "No row here is a Portaldot V3 transaction. V3 canaries need V3 back online and tPOTv3 funds.", ""]
    return "\n".join(lines)
