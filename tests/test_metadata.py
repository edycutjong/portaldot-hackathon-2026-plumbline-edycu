import json
from pathlib import Path

import pytest

from plumbline import metadata as md

ROOT = Path(__file__).resolve().parents[1]
BLOB_1002 = ROOT / "fixtures" / "metadata_1002.scale"
EVIDENCE_1002 = ROOT / "evidence" / "metadata_1002.json"


def test_known_profiles_and_presets():
    assert md.get_profile("mainnet-1002").preset == "legacy"
    assert md.get_profile("v3").preset is None
    assert md.get_profile("interim-sdk").preset is None
    assert not md.get_profile("mainnet-1002").writes_txs


def test_interim_profile_is_labelled_as_not_v3_and_uses_plumbline_port():
    prof = md.get_profile("interim-sdk")
    assert "not V3" in prof.label
    assert prof.url.endswith(":39944")


def test_unknown_profile_fails_loud():
    with pytest.raises(md.ProfileError):
        md.get_profile("testnet")


def test_stored_profile_needs_existing_file(tmp_path):
    with pytest.raises(md.ProfileError):
        md.get_profile(f"stored:{tmp_path / 'missing.scale'}")


def test_stored_profile_infers_legacy_for_v13_blob():
    prof = md.get_profile(f"stored:{BLOB_1002}")
    assert prof.preset == "legacy" and prof.stored_file == str(BLOB_1002)
    assert md.get_profile(f"stored:{BLOB_1002}@interim-sdk").preset is None


def test_identity_check_rejects_wrong_spec_name_and_genesis():
    prof = md.get_profile("mainnet-1002")
    md.check_identity(prof, "portaldot", prof.genesis_hash)
    with pytest.raises(md.UnknownChainError):
        md.check_identity(prof, "node", prof.genesis_hash)
    with pytest.raises(md.UnknownChainError):
        md.check_identity(prof, "portaldot", "0x" + "00" * 32)


def test_v3_profile_is_not_pinned_to_a_genesis_yet():
    prof = md.get_profile("v3")
    assert prof.genesis_hash is None
    with pytest.raises(md.UnknownChainError):
        md.check_identity(prof, "portaldot", "0x" + "11" * 32)


def test_metadata_version_reads_magic():
    assert md.metadata_version(b"meta\x0e" + b"\x00") == 14
    with pytest.raises(ValueError):
        md.metadata_version(b"nope\x0e")


def test_stored_1002_blob_matches_its_evidence_record():
    blob = BLOB_1002.read_bytes()
    record = json.loads(EVIDENCE_1002.read_text())
    assert md.sha256_hex(blob) == record["metadata_sha256"]
    assert len(blob) == record["metadata_bytes"]
    assert record["spec_name"] == "portaldot" and record["spec_version"] == 1002
    assert md.metadata_version(blob) == record["metadata_version"] == 13


def test_stored_1002_blob_decodes_offline_with_legacy_preset():
    surface = md.call_surface(md.decode_metadata(BLOB_1002.read_bytes(), "legacy"))
    assert len(surface) == 155
    assert surface["Balances.transfer_keep_alive"] == [("dest", "LookupSource"), ("value", "Compact<Balance>")]
    assert "Contracts.instantiate_with_code" in surface


@pytest.mark.live
def test_live_mainnet_runtime_matches_stored_blob():
    prof = md.get_profile("mainnet-1002")
    substrate = md.connect(prof)
    blob = md.fetch_metadata_blob(substrate)
    info = md.runtime_info(prof, substrate, blob)
    substrate.close()
    record = json.loads(EVIDENCE_1002.read_text())
    assert (info.spec_version, info.metadata_sha256) == (record["spec_version"], record["metadata_sha256"])


BLOB_STANDIN = ROOT / "fixtures" / "metadata_standin_sdk.scale"
EVIDENCE_STANDIN = ROOT / "evidence" / "metadata_standin_sdk.json"


def test_standin_not_v3_blob_is_labelled_and_pinned_to_genesis():
    record = json.loads(EVIDENCE_STANDIN.read_text())
    assert "not V3" in record["label"]
    assert record["profile"] == "interim-sdk" and record["spec_name"] == "node"
    assert record["block_hash"] == record["genesis_hash"]
    assert md.sha256_hex(BLOB_STANDIN.read_bytes()) == record["metadata_sha256"]


def test_standin_not_v3_blob_decodes_v14_with_no_preset():
    blob = BLOB_STANDIN.read_bytes()
    assert md.metadata_version(blob) == 14
    assert md.get_profile(f"stored:{BLOB_STANDIN}").preset is None
    decoded = md.decode_metadata(blob, None)
    assert "Revive" in md.pallet_names(decoded)
    surface = md.call_surface(decoded)
    assert [name for name, _ in surface["Balances.transfer_keep_alive"]] == ["dest", "value"]
