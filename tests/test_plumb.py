"""The drift ledger: schema, one verdict per entry by fixed rules, explicit renames, canonical export."""

import json
import random
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from plumbline import guide, metadata as md, plumb, verdicts as v
from plumbline.renames import Rename

ROOT = Path(__file__).resolve().parents[1]
BLOB_1002 = ROOT / "fixtures" / "metadata_1002.scale"
STANDIN = ROOT / "fixtures" / "metadata_standin_sdk.scale"
LEDGER = ROOT / "evidence" / "ledger_1002.json"

RT = plumb.Runtime(profile="test", label="synthetic", endpoint=None, spec_name="t", spec_version=1, tx_version=1,
                   metadata_version=14, metadata_hash="ab" * 32, code_hash=None, block_hash=None,
                   fetched_at="2026-10-14T00:00:00+00:00")
SURFACE = plumb.Surface(
    calls={"Tokens.issue": [("to", "AccountId"), ("amount", "u128")],
           "Tokens.lock": [("who", "AccountId")],
           "Tokens.old_name": [("x", "u8")],
           "Tokens.spare": []},
    pallets=["Tokens", "ConvictionVoting", "Empty"])


def entry(call, args=None, ordered=False, kind=v.CALL, pallet="tokens", source=v.V3_DEMO):
    return plumb.DocEntry(source, kind, pallet, call, args, ordered, ("x:1",))


# ---------------------------------------------------------------- schema

def test_schema_has_every_table_and_its_checks_come_from_the_vocabulary():
    conn = plumb.open_ledger()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == set(plumb.TABLES)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='verdict'").fetchone()[0]
    assert all(f"'{x}'" in sql for x in v.VERDICTS)


@pytest.mark.parametrize("table, row", [
    ("verdict", (1, v.V3_DEMO, v.CALL, "P", "c", "MAYBE", None, None, None)),        # unknown verdict
    ("verdict", (1, "blog-post", v.CALL, "P", "c", v.ABSENT, None, None, None)),     # unknown source
    ("verdict", (1, v.V3_DEMO, "event", "P", "c", v.ABSENT, None, None, None)),      # unknown kind
    ("rename", ("P", "a", "b", "test", "e", "fuzzy")),                               # unknown rename kind
    ("doc_call", ("guide", v.CALL, "P", "c", None, None, 0, "r", "[]", "")),         # unknown source
])
def test_schema_rejects_values_outside_the_vocabulary(table, row):
    conn = plumb.open_ledger()
    conn.execute("INSERT INTO runtime (id, profile, label, spec_name, spec_version, metadata_version, metadata_hash, "
                 "fetched_at) VALUES (1, 'test', 'l', 't', 1, 14, 'h', 'now')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(row))})", row)


def test_a_verdict_must_name_a_stored_runtime():
    conn = plumb.open_ledger()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO verdict VALUES (99, ?, ?, 'P', 'c', ?, NULL, NULL, NULL)", (v.V3_DEMO, v.CALL, v.ABSENT))


# ---------------------------------------------------------------- verdict rules

@pytest.mark.parametrize("e, expected", [
    (entry("issue", ("amount", "to")), (v.UNPROVEN, "Tokens.issue")),            # keyword args: set compare
    (entry("issue", ("amount", "to"), ordered=True), (v.ARG_DRIFT, "Tokens.issue")),  # positional: order matters
    (entry("issue", ("to",)), (v.ARG_DRIFT, "Tokens.issue")),
    (entry("issue"), (v.ARGS_UNCHECKED, "Tokens.issue")),
    (entry("spare", ()), (v.UNPROVEN, "Tokens.spare")),                          # zero args documented = checked
    (entry("transfer"), (v.ABSENT, None)),
    (entry("iss_ue"), (v.ABSENT, None)),                                         # a near name is not matched
    (entry("issue", pallet="Gadgets"), (v.ABSENT, None)),
    (entry("", kind=v.MODULE, pallet="conviction_voting", source=v.V3_FAQ), (v.UNPROVEN, "ConvictionVoting")),
    (entry("", kind=v.MODULE, pallet="broker", source=v.V3_FAQ), (v.ABSENT, None)),
    (entry("lookup", kind=v.RUNTIME_API, pallet="TokensApi"), (v.UNKNOWN_DOC, None)),
    (entry("?demo/x.py:3", pallet=""), (v.UNKNOWN_DOC, None)),
])
def test_classify_rules(e, expected):
    status, live, _ = plumb.classify(e, RT, SURFACE, [])
    assert (status, live) == expected


def test_arg_drift_detail_carries_both_shapes_and_flags_underscore_only():
    surface = plumb.Surface(calls={"System.remark": [("_remark", "Bytes")]}, pallets=["System"])
    status, _, detail = plumb.classify(entry("remark", ("remark",), True, pallet="system"), RT, surface, [])
    assert status == v.ARG_DRIFT
    assert json.loads(detail) == {"doc_args": ["remark"], "live_args": ["_remark"], "underscore_only": True}


def test_runtime_api_on_v13_metadata_says_why_it_is_unknown():
    rt13 = plumb.Runtime(**{**RT.__dict__, "metadata_version": 13})
    assert plumb.classify(entry("lookup", kind=v.RUNTIME_API), rt13, SURFACE, [])[2] == "metadata V13 lists no runtime APIs"


def test_an_absent_call_is_drifted_only_through_an_explicit_rename_row():
    row = Rename("test", "Tokens", "new_name", "old_name", v.RENAME, "measured")
    assert plumb.classify(entry("new_name"), RT, SURFACE, [row])[:2] == (v.DRIFTED, "Tokens.old_name")
    assert plumb.classify(entry("new_nam"), RT, SURFACE, [row])[:2] == (v.ABSENT, None)
    none_row = Rename("test", "Tokens", "new_name", "old_name", v.NONE, "no equivalent")
    assert plumb.classify(entry("new_name"), RT, SURFACE, [none_row])[0] == v.ABSENT


@pytest.mark.parametrize("row, message", [
    (Rename("test", "Tokens", "issue", "lock", v.RENAME, "e"), "documented call exists"),
    (Rename("test", "Tokens", "gone", "missing", v.RENAME, "e"), "no live missing"),
])
def test_rename_rows_the_runtime_contradicts_are_refused(row, message):
    with pytest.raises(plumb.LedgerError, match=message):
        plumb.check_renames((row,), "test", SURFACE)


def test_rename_rows_apply_only_to_their_profile():
    row = Rename("other", "Tokens", "issue", "lock", v.RENAME, "e")  # would be contradicted on "test"
    assert plumb.check_renames((row,), "test", SURFACE) == []


def test_committed_renames_hold_on_the_1002_blob():
    decoded = md.decode_metadata(BLOB_1002.read_bytes(), "legacy")
    surface = plumb.Surface(md.call_surface(decoded), md.pallet_names(decoded))
    rows = plumb.check_renames(plumb.RENAMES, "mainnet-1002", surface)
    assert {(r.doc_call, r.live_call) for r in rows} == {("create_pure", "anonymous"), ("kill_pure", "kill_anonymous")}


# ---------------------------------------------------------------- I1: PROVEN needs a read-back receipt

def ledger_with(receipts):
    conn = plumb.open_ledger()
    plumb.add_entries(conn, [entry("issue", ("to", "amount")), entry("lock")])
    plumb.plumb(conn, RT, SURFACE, renames=(), receipts=receipts)
    return {(c, s): r for c, s, r in conn.execute("SELECT call, status, receipt_id FROM verdict WHERE source != ?",
                                                  (v.RUNTIME,))}


def receipt(call, success=1, readback_ok=1):
    return {"side": "substrate", "pallet": "Tokens", "call": call, "tx_hash": "0x01", "success": success,
            "readback_ok": readback_ok}


def test_no_receipt_no_proven():
    assert ledger_with([]) == {("issue", v.UNPROVEN): None, ("lock", v.ARGS_UNCHECKED): None}


def test_a_read_back_receipt_proves_the_call_and_is_linked():
    rows = ledger_with([receipt("issue"), receipt("lock")])
    assert rows == {("issue", v.PROVEN): 1, ("lock", v.PROVEN): 2}


@pytest.mark.parametrize("bad", [receipt("issue", readback_ok=0), receipt("issue", success=0)])
def test_a_failed_or_unverified_receipt_proves_nothing(bad):
    assert ("issue", v.UNPROVEN) in ledger_with([bad])


def test_undocumented_live_calls_get_their_own_rows():
    conn = plumb.open_ledger()
    plumb.add_entries(conn, [entry("issue", ("to", "amount"))])
    plumb.plumb(conn, RT, SURFACE, renames=())
    rows = conn.execute("SELECT pallet, call FROM verdict WHERE status = ?", (v.UNDOCUMENTED,)).fetchall()
    assert sorted(rows) == [("Tokens", "lock"), ("Tokens", "old_name"), ("Tokens", "spare")]


# ---------------------------------------------------------------- guide entries

def test_guide_sites_group_per_call_and_expand_candidates():
    sites = [guide.Site(v.V3_DEMO, v.CALL, "Assets", ("transfer_keep_alive", "transfer"), "d.py:1", guide.LOCAL, ("a",)),
             guide.Site(v.V3_DEMO, v.CALL, "Assets", ("transfer",), "d.py:9", guide.LITERAL, ("a",)),
             guide.Site(v.V3_DEMO, v.CALL, "Assets", ("mint",), "d.py:5", guide.LITERAL, ("a",)),
             guide.Site(v.V3_DEMO, v.CALL, "Assets", ("mint",), "d.py:7", guide.LITERAL, ("b",)),
             guide.Site(v.V3_DEMO, v.CALL, None, (), "d.py:11", guide.UNRESOLVED)]
    got = {e.call: e for e in plumb.entries_from_guide(sites)}
    assert got["transfer"].refs == ("d.py:1", "d.py:9") and got["transfer_keep_alive"].refs == ("d.py:1",)
    assert got["mint"].args is None and "disagree" in got["mint"].note
    assert got["?d.py:11"].resolved is False


# ---------------------------------------------------------------- export

def build_1002(shuffle_seed=None):
    record = json.loads((ROOT / "evidence" / "metadata_1002.json").read_text())
    blob = BLOB_1002.read_bytes()
    rt = plumb.runtime_from_record(record, blob, md.PROFILES["mainnet-1002"].label)
    decoded = md.decode_metadata(blob, "legacy")
    table = json.loads((ROOT / "evidence" / "guide_calls.json").read_text())
    entries = plumb.entries_from_guide([guide.site_from_dict(s) for s in table["sites"]]) + \
        plumb.entries_from_mainnet_docs(json.loads((ROOT / "evidence" / "docs_1002.json").read_text())["rows"])
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(entries)
    data, _ = plumb.build(entries, [(rt, plumb.Surface(md.call_surface(decoded), md.pallet_names(decoded)))])
    return data


def test_export_is_canonical_utf8_json_with_sorted_keys_and_no_whitespace():
    data = build_1002()
    obj = json.loads(data.decode("utf-8"))
    assert data == json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert not data.endswith(b"\n") and b'": ' not in data


def test_export_is_deterministic_whatever_the_insertion_order():
    assert build_1002(shuffle_seed=1) == build_1002(shuffle_seed=2) == build_1002()


def test_every_verdict_row_names_its_runtime():
    for row in json.loads(build_1002())["verdicts"]:
        assert row["profile"] == "mainnet-1002" and row["spec_version"] == 1002
        assert row["metadata_hash"] == md.sha256_hex(BLOB_1002.read_bytes())
        assert row["status"] in v.VERDICTS


def test_mainnet_docs_verdicts_reproduce_the_docs_baseline_counts():
    counts = json.loads(build_1002())["counts"]["mainnet-1002"]
    docs = json.loads((ROOT / "evidence" / "docs_1002.json").read_text())["counts"]
    md_counts = counts[v.MAINNET_DOCS]
    assert md_counts[v.ABSENT] + md_counts[v.DRIFTED] == docs["absent"] == 64
    assert md_counts[v.DRIFTED] == 2 and md_counts[v.ARG_DRIFT] == docs["arg-mismatch"] == 15
    assert md_counts[v.UNPROVEN] == docs["match"] == 117
    assert counts[v.RUNTIME][v.UNDOCUMENTED] == docs["undocumented"] - 2  # the renamed live calls are covered


def test_committed_ledger_is_reproduced_byte_for_byte_with_its_hash():
    # scripts/build_ledger.py adds an `inputs` block; rebuild it the same way through the script
    committed = LEDGER.read_bytes()
    digest, name = (ROOT / "evidence" / "ledger_1002.json.blake2-256").read_text().split()
    assert (digest, name) == (plumb.blake2_256(committed), "ledger_1002.json")
    without_inputs = json.loads(committed)
    without_inputs["inputs"] = {}
    assert plumb.canonical(without_inputs) == build_1002()


def test_build_refuses_an_existing_database(tmp_path):
    db = tmp_path / "ledger.db"
    db.write_bytes(b"")
    with pytest.raises(plumb.LedgerError, match="rebuilt from its inputs"):
        plumb.build([], [], db=db)


def test_runtime_record_must_be_for_the_blob():
    record = json.loads((ROOT / "evidence" / "metadata_1002.json").read_text())
    with pytest.raises(plumb.LedgerError):
        plumb.runtime_from_record(record, STANDIN.read_bytes(), "x")


def test_standin_not_v3_v14_metadata_runs_through_plumb_in_memory_only():
    # the stand-in exercises the V14 decode path; no evidence is ever written from it
    decoded = md.decode_metadata(STANDIN.read_bytes())
    surface = plumb.Surface(md.call_surface(decoded), md.pallet_names(decoded))
    rt = plumb.Runtime(**{**RT.__dict__, "profile": "interim-sdk", "label": "stand-in, not V3"})
    conn = plumb.open_ledger()
    plumb.add_entries(conn, [entry("transfer_keep_alive", ("dest", "value"), pallet="Balances"),
                             entry("map_account", (), pallet="Revive")])
    plumb.plumb(conn, rt, surface, renames=())
    got = dict(conn.execute("SELECT call, status FROM verdict WHERE source = ?", (v.V3_DEMO,)).fetchall())
    assert got == {"transfer_keep_alive": v.UNPROVEN, "map_account": v.UNPROVEN}


# ---------------------------------------------------------------- the producer script

def run(*args):
    return subprocess.run([sys.executable, "-I", "scripts/build_ledger.py", *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_build_ledger_script_reproduces_the_committed_ledger(tmp_path):
    out = tmp_path / "ledger_1002.json"
    res = run("--profile", "stored:fixtures/metadata_1002.scale", "--runtime-record", "evidence/metadata_1002.json",
              "--db", str(tmp_path / "l.db"), "--out", str(out))
    assert res.returncode == 0, res.stderr
    assert out.read_bytes() == LEDGER.read_bytes()
    assert (tmp_path / "ledger_1002.json.blake2-256").read_text() == \
        (ROOT / "evidence" / "ledger_1002.json.blake2-256").read_text()


def test_build_ledger_script_refuses_the_standin_not_v3(tmp_path):
    res = run("--profile", "stored:fixtures/metadata_standin_sdk.scale", "--runtime-record",
              "evidence/metadata_standin_sdk.json", "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and "stand-in, not V3" in res.stderr
    assert not (tmp_path / "o.json").exists()


def test_build_ledger_script_refuses_a_record_for_another_runtime(tmp_path):
    res = run("--profile", "stored:fixtures/metadata_1002.scale", "--runtime-record", "evidence/metadata_standin_sdk.json",
              "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and not (tmp_path / "o.json").exists()


def test_build_ledger_script_refuses_a_guide_table_for_another_commit(tmp_path):
    table = json.loads((ROOT / "evidence" / "guide_calls.json").read_text()) | {"commit": "0" * 40}
    (tmp_path / "g.json").write_text(json.dumps(table))
    res = run("--profile", "stored:fixtures/metadata_1002.scale", "--runtime-record", "evidence/metadata_1002.json",
              "--guide-table", str(tmp_path / "g.json"), "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and "guide commit" in res.stderr
