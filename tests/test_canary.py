"""Canary v0: dev keys only on the interim stand-in (not V3), receipts with read-back (I1), PROOF.md labels.

Offline tests use small fakes of the SDK objects. The `live` test needs the interim chain
(`scripts/interim_node.sh start`) and is deselected by default.
"""

import json
from pathlib import Path

import pytest

from plumbline import canary, plumb
from plumbline import metadata as md

ROOT = Path(__file__).resolve().parents[1]
TEST_SEED = "0x" + "07" * 32  # a throwaway test key, never funded
EVIDENCE = ROOT / "evidence" / "canary_interim.json"
V3_EVIDENCE = ROOT / "evidence" / "canary_v3.json"
PROOF = ROOT / "docs" / "PROOF.md"


@pytest.mark.parametrize("profile", ["mainnet-1002", "v3", "stored:fixtures/metadata_1002.scale"])
def test_dev_keys_are_refused_off_the_interim_chain(profile):
    with pytest.raises(canary.CanaryError, match="dev keys are refused"):
        canary.dev_keypair(profile, "//Alice")


def test_dev_keypair_needs_a_dev_uri():
    with pytest.raises(canary.CanaryError, match="starting with //"):
        canary.dev_keypair("interim-sdk", "bottom drive obey lake curtain smoke basket hold race lonely fit walk")
    assert canary.dev_keypair("interim-sdk", "//Alice").ss58_address.startswith("5GrwvaEF")


@pytest.mark.parametrize("profile, why", [("mainnet-1002", "read-only"), ("stored:fixtures/metadata_v3.scale", "read-only")])
def test_run_refuses_every_profile_but_v3_and_the_interim_one(profile, why, monkeypatch):
    monkeypatch.setattr(md, "connect", lambda *a, **k: pytest.fail("must refuse before connecting"))
    with pytest.raises(canary.CanaryError, match=why):
        canary.run(profile)


def test_v3_run_reads_the_seed_before_it_connects(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "connect", lambda *a, **k: pytest.fail("must refuse before connecting"))
    with pytest.raises(canary.CanaryError, match="cannot read the seed file"):
        canary.run_v3(seed_file=tmp_path / "missing.seed")


def test_seed_file_must_be_private_nonempty_valid_and_outside_the_repo(tmp_path):
    seed = tmp_path / "s.seed"
    seed.write_text(TEST_SEED + "\n")
    seed.chmod(0o644)
    with pytest.raises(canary.CanaryError, match="chmod 600"):
        canary.seed_keypair(seed)
    seed.chmod(0o600)
    assert canary.seed_keypair(seed).ss58_address.startswith("5")
    seed.write_text("  \n")
    with pytest.raises(canary.CanaryError, match="is empty"):
        canary.seed_keypair(seed)
    seed.write_text("0xnot-a-seed-SECRETWORD")
    with pytest.raises(canary.CanaryError) as err:
        canary.seed_keypair(seed)
    assert "SECRETWORD" not in str(err.value) and "not hold a valid seed" in str(err.value)  # never echoes the seed
    for uri in ("//Alice", "//alice//stash", "//Alice/stash", "//NotAlice"):  # a bare //URI has no secret entropy
        seed.write_text(uri)
        with pytest.raises(canary.CanaryError, match="holds a dev URI") as err:
            canary.seed_keypair(seed)
        assert uri not in str(err.value)
    from substrateinterface import Keypair

    seed.write_text("0xe5be9a5092b81bca64be81d212e7f2f9eba183bb7a90954f7b76361f6edb5c0a")  # //Alice's published seed
    with pytest.raises(canary.CanaryError, match="well-known dev account"):
        canary.seed_keypair(seed)
    seed.write_text(Keypair.generate_mnemonic())
    assert canary.seed_keypair(seed).ss58_address.startswith("5")
    inside = ROOT / "evidence" / "tmp.seed"
    with pytest.raises(canary.CanaryError, match="outside this repository"):
        canary.seed_keypair(inside)


class _Acct:
    def __init__(self, free):
        self.value = {"data": {"free": free}}


def test_v3_run_refuses_an_unfunded_signer_before_sending(tmp_path, monkeypatch):
    seed = tmp_path / "s.seed"
    seed.write_text(TEST_SEED)
    seed.chmod(0o600)

    class Chain:
        def rpc_request(self, method, params):
            return {"result": {"specName": "portaldot-v3", "specVersion": 3002}}

        def query(self, *a, **k):
            return _Acct(5)

        def close(self):
            pass

        def create_signed_extrinsic(self, **k):
            pytest.fail("must not sign when unfunded")

    monkeypatch.setattr(md, "connect", lambda *a, **k: Chain())
    monkeypatch.setattr(md, "pinned_head", lambda s: "0x" + "11" * 32)
    monkeypatch.setattr(md, "fetch_metadata_blob", lambda s, h: b"meta\x0e")
    with pytest.raises(canary.CanaryError, match="fund it"):
        canary.run_v3(seed_file=seed)


def test_v3_run_reads_the_balance_after_at_the_last_receipts_block(tmp_path, monkeypatch):
    seed = tmp_path / "s.seed"
    seed.write_text(TEST_SEED)
    seed.chmod(0o600)
    last, queried = "0x" + "22" * 32, []

    class Chain:
        def rpc_request(self, method, params):
            return {"result": {"specName": "portaldot-v3", "specVersion": 3002}}

        def query(self, *a, **k):
            queried.append(k.get("block_hash"))
            return _Acct(10**20)

        def compose_call(self, *a, **k):
            return None

        def close(self):
            pass

    monkeypatch.setattr(md, "connect", lambda *a, **k: Chain())
    monkeypatch.setattr(md, "pinned_head", lambda s: "0x" + "11" * 32)
    monkeypatch.setattr(md, "fetch_metadata_blob", lambda s, h: b"meta\x0e")
    monkeypatch.setattr(canary, "send", lambda *a, **k: {"block_hash": last})
    record = canary.run_v3(seed_file=seed)
    assert queried == ["0x" + "11" * 32, last] and record["free_after"] == str(10**20)


def test_cli_canary_matches_the_canonical_record_by_file_not_spelling(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from plumbline.cli import app

    (tmp_path / "docs").mkdir()
    (tmp_path / "evidence").mkdir()
    monkeypatch.chdir(tmp_path)
    record = json.loads((ROOT / "evidence" / "canary_interim.json").read_text())
    monkeypatch.setattr(canary, "run", lambda *a, **k: record)
    absolute = tmp_path / "evidence" / ".." / "evidence" / "canary_interim.json"
    result = CliRunner().invoke(app, ["canary", "--profile", "interim-sdk", "--out", str(absolute)])
    assert (tmp_path / "docs" / "PROOF.md").exists() and not list(tmp_path.glob("evidence/*.PROOF.md")), result.output


def test_cli_canary_with_a_custom_out_never_rewrites_the_canonical_proof(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from plumbline.cli import app

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PROOF.md").write_text("kept")
    monkeypatch.chdir(tmp_path)
    record = json.loads((ROOT / "evidence" / "canary_interim.json").read_text())
    monkeypatch.setattr(canary, "run", lambda *a, **k: record)
    result = CliRunner().invoke(app, ["canary", "--profile", "interim-sdk", "--out", str(tmp_path / "x.json")])
    assert (tmp_path / "docs" / "PROOF.md").read_text() == "kept" and (tmp_path / "x.PROOF.md").exists(), result.output


class FakeScale:
    def __init__(self, value):
        self.value = value


class FakeChain:
    """Just enough of SubstrateInterface for readback / dispatch_outcome."""

    def __init__(self, calls, outcomes):
        self.calls = calls          # [(module, function, hash)]
        self.outcomes = outcomes    # {index: "ExtrinsicSuccess" | "ExtrinsicFailed"}

    def get_block(self, block_hash):
        return {"extrinsics": [FakeScale({"call": {"call_module": m, "call_function": f}, "extrinsic_hash": h})
                               for m, f, h in self.calls]}

    def get_events(self, block_hash):
        flat = [FakeScale({"phase": "ApplyExtrinsic", "extrinsic_idx": i,  # the shape substrate-interface 1.8 returns
                           "event": {"module_id": "System", "event_id": e}}) for i, e in self.outcomes.items()]
        return flat + [FakeScale({"phase": {"ApplyExtrinsic": 9}, "event": {"module_id": "System",
                                                                         "event_id": "ExtrinsicFailed"}}),
                       FakeScale({"phase": "Finalization", "extrinsic_idx": None, "event": {}})]

    def get_block_number(self, block_hash):
        return 7


H = "0x" + "ab" * 32


def test_readback_needs_the_same_hash_and_call_at_the_index():
    chain = FakeChain([("Timestamp", "set", "0x" + "00" * 32), ("Balances", "transfer_keep_alive", H)], {})
    assert canary.readback(chain, "0xb", 1, H) == (True, "Balances.transfer_keep_alive")
    assert canary.readback(chain, "0xb", 1, H.upper().replace("0X", "0x"))[0]  # case-insensitive hex
    assert canary.readback(chain, "0xb", 0, H) == (False, "Timestamp.set")       # another extrinsic
    assert canary.readback(chain, "0xb", 5, H) == (False, None)                  # no such index


def test_dispatch_outcome_reads_the_events_of_that_extrinsic():
    chain = FakeChain([], {0: "ExtrinsicSuccess", 2: "ExtrinsicFailed"})
    assert canary.dispatch_outcome(chain, "0xb", 0) is True
    assert canary.dispatch_outcome(chain, "0xb", 2) is False
    assert canary.dispatch_outcome(chain, "0xb", 1) is None
    assert canary.dispatch_outcome(chain, "0xb", 9) is False  # the enum-shaped phase is read too


def test_committed_record_is_labelled_and_read_back_on_every_row():
    record = json.loads(EVIDENCE.read_text())
    assert record["profile"] == "interim-sdk" and record["label"] == canary.LABEL == "interim-sdk — not V3"
    assert [f"{r['pallet']}.{r['call']}" for r in record["receipts"]] == [
        "Balances.transfer_keep_alive", "System.remark_with_event", "Revive.map_account"]
    for r in record["receipts"]:
        assert r["label"] == canary.LABEL
        assert (r["profile"], r["spec_version"]) == ("interim-sdk", record["spec_version"])
        assert r["readback_ok"] == 1 and r["readback_call"] == f"{r['pallet']}.{r['call']}"
        assert len(r["tx_hash"]) == 66 and len(r["block_hash"]) == 66
        assert r["extrinsic_id"] == f"{r['block_number']}-{r['extrinsic_id'].split('-')[1]}"
    assert len(record["metadata_sha256"]) == 64 and int(record["metadata_sha256"], 16) >= 0
    assert "not Portaldot V3" in record["note"]


def test_proof_md_is_generated_from_the_record_and_labels_every_row():
    record = json.loads(EVIDENCE.read_text())
    v3 = json.loads(V3_EVIDENCE.read_text()) if V3_EVIDENCE.exists() else None
    assert PROOF.read_text() == canary.proof_document(v3, record)
    rows = [line for line in PROOF.read_text().splitlines() if line.startswith("| ") and "`" in line]
    interim_rows = [line for line in rows if line.startswith(f"| {canary.LABEL} |")]
    assert len(interim_rows) == len(record["receipts"])
    v3_rows = [line for line in rows if line.startswith(f"| {canary.LABEL_V3} |")]
    assert len(v3_rows) == (len(v3["receipts"]) if v3 else 0) and len(rows) == len(interim_rows) + len(v3_rows)
    for r in record["receipts"] + (v3["receipts"] if v3 else []):
        assert r["tx_hash"] in PROOF.read_text() and r["block_hash"] in PROOF.read_text()


@pytest.mark.skipif(not V3_EVIDENCE.exists(), reason="no V3 canary run recorded yet")
def test_committed_v3_record_is_labelled_stamped_and_read_back_on_every_row():
    record = json.loads(V3_EVIDENCE.read_text())
    assert record["profile"] == "v3" and record["label"] == canary.LABEL_V3 and record["spec_version"] == 3002
    assert record["signer"] == "5EhP3KWtUkaRuFUoejCfmYHfi54d2fomNFoyVjNFFsRUzDsb"
    assert [f"{r['pallet']}.{r['call']}" for r in record["receipts"]] == [
        "System.remark_with_event", "Balances.transfer_keep_alive", "Utility.batch_all"]
    for r in record["receipts"]:
        assert r["label"] == canary.LABEL_V3 and (r["profile"], r["spec_version"]) == ("v3", 3002)
        assert r["readback_ok"] == 1 and r["success"] == 1 and r["dispatch_error"] is None
        assert len(r["tx_hash"]) == 66 and len(r["block_hash"]) == 66
    assert "seed" not in json.dumps(record).lower().replace("seed file", "")


def test_interim_receipts_never_become_ledger_verdicts():
    record = json.loads(EVIDENCE.read_text())
    # on the baseline runtime: refused, because each receipt names the chain it was sent to
    mainnet = plumb.runtime_from_record(json.loads((ROOT / "evidence" / "metadata_1002.json").read_text()),
                                        (ROOT / "fixtures" / "metadata_1002.scale").read_bytes(), "1002")
    surface_1002 = plumb.surface_from_blob((ROOT / "fixtures" / "metadata_1002.scale").read_bytes(), "legacy")
    with pytest.raises(plumb.LedgerError, match="receipt .* is from interim-sdk"):
        plumb.plumb(plumb.open_ledger(), mainnet, surface_1002, receipts=record["receipts"])
    # on the stand-in itself: refused as a runtime
    standin = plumb.Runtime(profile="interim-sdk", label="stand-in, not V3", endpoint=None, spec_name="node",
                            spec_version=268, tx_version=2, metadata_version=14, metadata_hash="ef" * 32,
                            code_hash=None, block_hash=None, fetched_at="2026-10-16T00:00:00+00:00")
    surface = plumb.Surface({}, [], standin.metadata_hash)
    with pytest.raises(plumb.LedgerError, match="stand-in, not V3"):
        plumb.plumb(plumb.open_ledger(), standin, surface, receipts=record["receipts"])


def test_dev_keys_go_only_to_a_loopback_endpoint(monkeypatch):
    monkeypatch.setattr(md, "connect", lambda *a, **k: pytest.fail("must refuse before connecting"))
    with pytest.raises(canary.CanaryError, match="not a loopback address"):
        canary.run("interim-sdk", url="wss://testnet.example.org:39944")


def test_cli_canary_will_not_overwrite_a_record_without_replace(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from plumbline.cli import app

    out = tmp_path / "c.json"
    out.write_text("{}")
    monkeypatch.setattr(canary, "run", lambda *a, **k: pytest.fail("must refuse before sending"))
    result = CliRunner().invoke(app, ["canary", "--profile", "interim-sdk", "--out", str(out),
                                      "--proof", str(tmp_path / "PROOF.md")])
    assert result.exit_code == 2 and "--replace" in result.output and out.read_text() == "{}"


def test_cli_canary_keeps_the_receipts_of_a_run_that_stopped_part_way(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from plumbline.cli import app

    partial = {"receipts": [{"tx_hash": H}], "label": canary.LABEL, "run_id": "r1"}

    def stop(*a, **k):
        raise canary.CanaryError("System.remark_with_event failed: boom", partial=partial)

    monkeypatch.setattr(canary, "run", stop)
    out = tmp_path / "c.json"
    result = CliRunner().invoke(app, ["canary", "--profile", "interim-sdk", "--out", str(out),
                                      "--proof", str(tmp_path / "PROOF.md")])
    kept = json.loads((tmp_path / "c.r1.partial.json").read_text())  # one file per run: never overwritten
    assert result.exit_code == 1 and not out.exists()
    assert kept["receipts"] == partial["receipts"] and "boom" in kept["incomplete"]


@pytest.mark.live
def test_live_canary_on_the_interim_chain_reads_back(tmp_path):
    record = canary.run("interim-sdk")  # a fresh per-run account each time
    assert all(r["readback_ok"] and r["success"] for r in record["receipts"])
    assert all(row["match"] for row in canary.verify(record))


def test_cli_readback_of_a_bad_file_is_a_usage_error(tmp_path):
    from typer.testing import CliRunner

    from plumbline.cli import app

    (tmp_path / "bad.json").write_text("{not json")
    for target in (tmp_path / "bad.json", tmp_path / "missing.json"):
        result = CliRunner().invoke(app, ["readback", str(target)])
        assert result.exit_code == 2 and result.output.startswith("error:"), result.output


def test_a_receipt_never_read_back_is_rendered_and_verified_without_crashing(monkeypatch):
    record = json.loads(EVIDENCE.read_text())
    lost = dict(record["receipts"][0], extrinsic_id=None, block_number=None, success=None, readback_ok=0,
                readback_call=None, readback_error="TimeoutError: x")
    record = dict(record, receipts=[lost])
    text = canary.proof_markdown(record)
    assert "| `-` |" in text and "outcome not read back" in text and "failed: None" not in text
    monkeypatch.setattr(md, "connect", lambda *a, **k: FakeChain([], {}))
    FakeChain.close = lambda self: None
    (row,) = canary.verify(record)
    assert row["match"] is False and row["extrinsic_id"] is None


def test_cli_canary_will_not_overwrite_the_proof_table_without_replace(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from plumbline.cli import app

    proof = tmp_path / "PROOF.md"
    proof.write_text("kept")
    monkeypatch.setattr(canary, "run", lambda *a, **k: pytest.fail("must refuse before sending"))
    result = CliRunner().invoke(app, ["canary", "--profile", "interim-sdk", "--out", str(tmp_path / "new.json"),
                                      "--proof", str(proof)])
    assert result.exit_code == 2 and "--replace" in result.output and proof.read_text() == "kept"
    assert not (tmp_path / "new.json").exists()


def test_proof_row_of_a_receipt_never_read_back_names_the_error_not_none():
    record = json.loads((Path(__file__).resolve().parents[1] / "evidence" / "canary_interim.json").read_text())
    record["receipts"][0].update(extrinsic_id=None, success=None, readback_ok=0, readback_call=None,
                                 readback_error="ConnectionError: boom")
    row = [line for line in canary.proof_markdown(record).splitlines() if "transfer_keep_alive" in line][0]
    assert "None" not in row and "FAILED (ConnectionError: boom)" in row
