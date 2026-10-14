"""The drift ledger: schema, one verdict per entry by fixed rules, explicit renames, canonical export."""

import json
import random
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from plumbline import guide, metadata as md, plumb, verdicts as v
from plumbline.renames import RENAMES, Rename

ROOT = Path(__file__).resolve().parents[1]
BLOB_1002 = ROOT / "fixtures" / "metadata_1002.scale"
STANDIN = ROOT / "fixtures" / "metadata_standin_sdk.scale"
LEDGER = ROOT / "evidence" / "ledger_1002.json"
DOCS_1002 = ROOT / "evidence" / "docs_1002.json"

RT = plumb.Runtime(profile="test", label="synthetic", endpoint=None, spec_name="t", spec_version=1, tx_version=1,
                   metadata_version=14, metadata_hash="ab" * 32, code_hash=None, block_hash=None,
                   fetched_at="2026-10-14T00:00:00+00:00")
SURFACE = plumb.Surface(
    calls={"Tokens.issue": [("to", "AccountId"), ("amount", "u128")],
           "Tokens.lock": [("who", "AccountId")],
           "Tokens.old_name": [("x", "u8")],
           "Tokens.spare": []},
    pallets=["Tokens", "ConvictionVoting", "Empty"], metadata_hash=RT.metadata_hash)


def entry(call, args=None, ordered=False, kind=v.CALL, pallet="Tokens", source=v.V3_DEMO):
    return plumb.DocEntry(source, kind, pallet, call, args, ordered, ("x:1",))


def ledger(entries, receipts=(), renames=(), rt=RT, surface=SURFACE):
    conn = plumb.open_ledger()
    plumb.add_entries(conn, entries)
    plumb.plumb(conn, rt, surface, renames=renames, receipts=list(receipts))
    return conn


def rows(conn, where="1=1", params=()):
    return conn.execute(f"SELECT source, kind, pallet, call, status, live_equivalent, receipt_id FROM verdict "
                        f"WHERE {where} ORDER BY source, pallet, call", params).fetchall()


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
    ("rename", ("P", "a", "b", "test", "e", "fuzzy", 0)),                            # unknown rename kind
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


def test_one_runtime_per_profile_and_the_surface_must_be_its_blob():
    conn = ledger([])
    with pytest.raises(plumb.LedgerError, match="already holds"):
        plumb.plumb(conn, RT, SURFACE, renames=())
    other = plumb.Surface(SURFACE.calls, SURFACE.pallets, "cd" * 32)
    with pytest.raises(plumb.LedgerError, match="surface is from metadata"):
        plumb.plumb(plumb.open_ledger(), RT, other, renames=())


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
    (entry("issue", ("to", "amount"), pallet="tokens"), (v.ABSENT, None)),        # demo code: exact, like the SDK
    (entry("issue", ("to", "amount"), pallet="tokens", source=v.V3_GUIDE), (v.UNPROVEN, "Tokens.issue")),  # prose
    (entry("", kind=v.MODULE, pallet="conviction_voting", source=v.V3_FAQ), (v.UNPROVEN, "ConvictionVoting")),
    (entry("", kind=v.MODULE, pallet="broker", source=v.V3_FAQ), (v.ABSENT, None)),
    (entry("lookup", kind=v.RUNTIME_API, pallet="TokensApi"), (v.UNKNOWN_DOC, None)),
    (entry("?demo/x.py:3#1", pallet=""), (v.UNKNOWN_DOC, None)),
])
def test_classify_rules(e, expected):
    status, live, _ = plumb.classify(e, RT, SURFACE, [])
    assert (status, live) == expected


def test_arg_drift_detail_carries_both_shapes_and_flags_underscore_only():
    surface = plumb.Surface(calls={"System.remark": [("_remark", "Bytes")]}, pallets=["System"])
    status, _, detail = plumb.classify(entry("remark", ("remark",), True, pallet="system", source=v.MAINNET_DOCS),
                                       RT, surface, [])
    assert status == v.ARG_DRIFT
    assert json.loads(detail) == {"doc_args": ["remark"], "live_args": ["_remark"], "underscore_only": True}


def test_runtime_api_on_v13_metadata_says_why_it_is_unknown():
    rt13 = plumb.Runtime(**{**RT.__dict__, "metadata_version": 13})
    assert plumb.classify(entry("lookup", kind=v.RUNTIME_API), rt13, SURFACE, [])[2] == "metadata V13 lists no runtime APIs"


# ---------------------------------------------------------------- renames (I3)

def test_an_absent_call_is_drifted_only_through_an_explicit_rename_row():
    row = Rename("test", "Tokens", "new_name", "old_name", v.RENAME, "measured")
    assert plumb.classify(entry("new_name"), RT, SURFACE, [row])[:2] == (v.DRIFTED, "Tokens.old_name")
    assert plumb.classify(entry("new_nam"), RT, SURFACE, [row])[:2] == (v.ABSENT, None)


def test_a_drifted_row_shows_the_live_shape_when_the_arguments_differ_too():
    row = Rename("test", "Tokens", "new_name", "old_name", v.RENAME, "measured")
    status, live, detail = plumb.classify(entry("new_name", ("y",)), RT, SURFACE, [row])
    assert (status, live) == (v.DRIFTED, "Tokens.old_name")
    assert detail.endswith("args differ: documented ['y'], live ['x']")
    assert "args differ" not in plumb.classify(entry("new_name", ("x",)), RT, SURFACE, [row])[2]


def test_a_rename_applies_to_demo_code_only_under_its_exact_pallet_name():
    row = Rename("test", "Tokens", "new_name", "old_name", v.RENAME, "measured")
    assert plumb.classify(entry("new_name", pallet="tokens"), RT, SURFACE, [row])[0] == v.ABSENT  # SDK: exact
    assert plumb.classify(entry("new_name", pallet="tokens", source=v.V3_GUIDE), RT, SURFACE, [row])[0] == v.DRIFTED


def test_a_rename_row_without_evidence_is_refused():
    with pytest.raises(plumb.LedgerError, match="needs evidence"):
        plumb.check_renames((Rename("test", "Tokens", "new_name", "old_name", v.RENAME, " "),), "test", SURFACE)


def test_a_none_row_needs_no_live_call_and_its_evidence_travels_with_absent():
    row = Rename("test", "Tokens", "gone", "", v.NONE, "removed upstream, no replacement")
    assert plumb.check_renames((row,), "test", SURFACE) == [row]
    status, live, detail = plumb.classify(entry("gone"), RT, SURFACE, [row])
    assert (status, live) == (v.ABSENT, None) and "removed upstream, no replacement" in detail


@pytest.mark.parametrize("row, message", [
    (Rename("test", "Tokens", "issue", "lock", v.RENAME, "e"), "documented call exists"),
    (Rename("test", "Tokens", "gone", "missing", v.RENAME, "e"), "no live missing"),
    (Rename("test", "ConvictionVoting", "voting", "", v.RENAME, "e", module=True), None),
    (Rename("test", "Tokens", "conviction_voting", "", v.RENAME, "e", module=True), "documented module exists"),
    (Rename("test", "Collators", "cumulus_parachain_system", "", v.RENAME, "e", module=True), "no live Collators"),
])
def test_rename_rows_the_runtime_contradicts_are_refused(row, message):
    if message is None:
        assert plumb.check_renames((row,), "test", SURFACE) == [row]
    else:
        with pytest.raises(plumb.LedgerError, match=message):
            plumb.check_renames((row,), "test", SURFACE)


def test_rename_rows_apply_only_to_their_profile():
    row = Rename("other", "Tokens", "issue", "lock", v.RENAME, "e")  # would be contradicted on "test"
    assert plumb.check_renames((row,), "test", SURFACE) == []


def test_the_live_side_of_a_rename_gets_its_own_verdict():
    row = Rename("test", "Tokens", "new_name", "old_name", v.RENAME, "measured")
    conn = ledger([entry("new_name", ("x",))], renames=(row,))
    got = {(r[0], r[3]): r[4] for r in rows(conn)}
    assert got[(v.V3_DEMO, "new_name")] == v.DRIFTED and got[(v.RUNTIME, "old_name")] == v.UNPROVEN
    proven = ledger([entry("new_name", ("x",))], renames=(row,), receipts=[receipt("old_name")])
    assert {(r[0], r[3]): r[4] for r in rows(proven)}[(v.RUNTIME, "old_name")] == v.PROVEN


def test_a_module_rename_drifts_the_module_and_stamps_its_live_side():
    row = Rename("test", "ConvictionVoting", "voting", "", v.RENAME, "measured", module=True)
    conn = ledger([entry("", kind=v.MODULE, pallet="voting", source=v.V3_FAQ)], renames=(row,))  # noqa: E501
    got = {(r[0], r[1], r[2]): (r[4], r[5]) for r in rows(conn)}
    assert got[(v.V3_FAQ, v.MODULE, "voting")] == (v.DRIFTED, "ConvictionVoting")
    assert got[(v.RUNTIME, v.MODULE, "ConvictionVoting")] == (v.UNPROVEN, "ConvictionVoting")


def test_committed_renames_hold_on_the_1002_blob_and_their_arguments_match_the_docs():
    surface = plumb.surface_from_blob(BLOB_1002.read_bytes(), "legacy")
    applied = plumb.check_renames(RENAMES, "mainnet-1002", surface)
    assert {(r.doc_call, r.live_call) for r in applied} == {("create_pure", "anonymous"), ("kill_pure", "kill_anonymous")}
    documented = {plumb.entries_from_mainnet_docs([r])[0].call: r for r in json.loads(DOCS_1002.read_text())["rows"]
                  if r["doc_call"].startswith("proxy.")}
    for r in applied:  # the evidence string's claim: same argument names, same order
        doc_args = [name for name, _ in documented[r.doc_call]["doc_args"]]
        assert doc_args == [name for name, _ in surface.calls[f"{r.pallet}.{r.live_call}"]], r


# ---------------------------------------------------------------- I1: PROVEN needs a read-back receipt

def receipt(call, **over):
    return {"side": "substrate", "pallet": "Tokens", "call": call, "tx_hash": "0x" + "1" * 64, "block_hash": "0x" + "2" * 64,
            "block_number": 7, "success": 1, "readback_ok": 1, "readback_call": f"Tokens.{call}"} | over


def statuses(receipts):
    conn = ledger([entry("issue", ("to", "amount")), entry("lock")], receipts)
    return {(r[3], r[4]): r[6] for r in rows(conn, "source != ?", (v.RUNTIME,))}


def test_no_receipt_no_proven():
    assert statuses([]) == {("issue", v.UNPROVEN): None, ("lock", v.ARGS_UNCHECKED): None}


def test_a_read_back_receipt_proves_the_call_and_is_linked():
    got = statuses([receipt("issue"), receipt("lock")])
    assert set(got) == {("issue", v.PROVEN), ("lock", v.PROVEN)} and all(got.values())


@pytest.mark.parametrize("bad", [
    {"readback_ok": 0}, {"success": 0}, {"tx_hash": None}, {"block_hash": None}, {"tx_hash": ""},
    {"block_hash": ""}, {"block_number": None}, {"dispatch_error": "Module(Tokens.Frozen)"}, {"side": "eth"},
    {"tx_hash": " " * 66}, {"block_hash": "0x"}, {"tx_hash": "0x" + "g" * 64}, {"block_hash": "0x" + "2" * 63},
    {"readback_call": "Tokens.lock"},  # read back as another call
    {"readback_call": None},
])
def test_a_failed_or_unverified_receipt_proves_nothing(bad):
    assert ("issue", v.UNPROVEN) in statuses([receipt("issue", **bad)])


def test_receipt_ids_do_not_depend_on_the_order_they_were_given():
    a = plumb.canonical(plumb.export(ledger([entry("issue", ("to", "amount"))], [receipt("issue"), receipt("lock")])))
    b = plumb.canonical(plumb.export(ledger([entry("issue", ("to", "amount"))], [receipt("lock"), receipt("issue")])))
    assert a == b


def test_undocumented_live_calls_get_their_own_rows_with_a_caveat_when_entries_are_unresolved():
    conn = ledger([entry("issue", ("to", "amount"))])
    assert [r[3] for r in rows(conn, "status = ?", (v.UNDOCUMENTED,))] == ["lock", "old_name", "spare"]
    conn = ledger([entry("issue", ("to", "amount")), entry("?d.py:1#1", pallet="")])
    details = {d for (d,) in conn.execute("SELECT detail FROM verdict WHERE status = ?", (v.UNDOCUMENTED,))}
    assert details == {"1 documented call entries are unresolved; this call may be one of them"}


# ---------------------------------------------------------------- guide entries

def test_guide_sites_group_per_call_expand_candidates_and_snake_case_names():
    sites = [guide.Site(v.V3_DEMO, v.CALL, "Assets", ("transfer_keep_alive", "transfer"), "d.py:1", guide.LOCAL, ("a",)),
             guide.Site(v.V3_DEMO, v.CALL, "Assets", ("transfer",), "d.py:9", guide.LITERAL, ("a",)),
             guide.Site(v.V3_DEMO, v.CALL, "Assets", ("mint",), "d.py:5", guide.LITERAL, ("a",)),
             guide.Site(v.V3_DEMO, v.CALL, "Assets", ("mint",), "d.py:7", guide.LITERAL, ("b",)),
             guide.Site(v.V3_GUIDE, v.CALL, "Assets", ("transferAll",), "R.md:3", guide.EXPLORER)]
    got = {e.call: e for e in plumb.entries_from_guide(sites)}
    assert got["transfer"].refs == ("d.py:1", "d.py:9") and "one of 2 candidates (local) at d.py:1" in got["transfer"].note
    assert got["mint"].args is None and "disagree" in got["mint"].note
    assert "transfer_all" in got


def test_two_unresolved_sites_on_one_line_are_two_entries_and_keep_their_names():
    sites = [guide.Site(v.V3_GUIDE, v.CALL, None, ("a",), "R.md:5", guide.UNRESOLVED),
             guide.Site(v.V3_GUIDE, v.CALL, None, ("b",), "R.md:5", guide.UNRESOLVED)]
    entries = plumb.entries_from_guide(sites)
    assert [(e.call, e.note) for e in entries] == [("?R.md:5#1", "unresolved unresolved; names a"),
                                                   ("?R.md:5#2", "unresolved unresolved; names b")]
    conn = ledger(entries)  # no primary-key collision
    assert [r[4] for r in rows(conn, "source = ?", (v.V3_GUIDE,))] == [v.UNKNOWN_DOC, v.UNKNOWN_DOC]


# ---------------------------------------------------------------- export

def build_1002(shuffle_seed=None):
    record = json.loads((ROOT / "evidence" / "metadata_1002.json").read_text())
    blob = BLOB_1002.read_bytes()
    rt = plumb.runtime_from_record(record, blob, md.PROFILES["mainnet-1002"].label, report_state=v.CACHED)
    table = json.loads((ROOT / "evidence" / "guide_calls.json").read_text())
    entries = plumb.entries_from_guide([guide.site_from_dict(s) for s in table["sites"]]) + \
        plumb.entries_from_mainnet_docs(json.loads(DOCS_1002.read_text())["rows"])
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(entries)
    data, _ = plumb.build(entries, [(rt, plumb.surface_from_blob(blob, "legacy"))])
    return data


def test_export_is_canonical_utf8_json_with_sorted_keys_and_no_whitespace():
    data = build_1002()
    obj = json.loads(data.decode("utf-8"))
    assert data == json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert not data.endswith(b"\n") and b'": ' not in data


def test_export_is_deterministic_whatever_the_insertion_order():
    assert build_1002(shuffle_seed=1) == build_1002(shuffle_seed=2) == build_1002()


def test_every_verdict_row_names_its_runtime_and_the_runtime_is_stamped_cached():
    ledger_obj = json.loads(build_1002())
    assert [r["report_state"] for r in ledger_obj["runtimes"]] == [v.CACHED]
    for row in ledger_obj["verdicts"]:
        assert row["profile"] == "mainnet-1002" and row["spec_version"] == 1002
        assert row["metadata_hash"] == md.sha256_hex(BLOB_1002.read_bytes())
        assert row["status"] in v.VERDICTS


def test_mainnet_docs_verdicts_reproduce_the_docs_baseline_counts():
    counts = json.loads(build_1002())["counts"]["mainnet-1002"]
    docs = json.loads(DOCS_1002.read_text())["counts"]
    md_counts = counts[v.MAINNET_DOCS]
    assert md_counts[v.ABSENT] + md_counts[v.DRIFTED] == docs["absent"] == 64
    assert md_counts[v.DRIFTED] == 2 and md_counts[v.ARG_DRIFT] == docs["arg-mismatch"] == 15
    assert md_counts[v.UNPROVEN] == docs["match"] == 117
    # the renamed live calls are covered by their DRIFTED rows and get their own UNPROVEN rows
    assert counts[v.RUNTIME] == {v.UNDOCUMENTED: docs["undocumented"] - 2, v.UNPROVEN: 2}


def test_committed_ledger_is_reproduced_byte_for_byte_with_its_hash():
    committed = LEDGER.read_bytes()
    digest, name = (ROOT / "evidence" / "ledger_1002.json.blake2-256").read_text().split()
    assert (digest, name) == (plumb.blake2_256(committed), "ledger_1002.json")
    without_inputs = json.loads(committed)
    without_inputs["inputs"] = {}  # scripts/build_ledger.py adds the inputs block; see the script test below
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


def standin_runtime():
    return plumb.Runtime(**{**RT.__dict__, "profile": "interim-sdk", "label": "stand-in, not V3",
                            "metadata_hash": md.sha256_hex(STANDIN.read_bytes())})


def test_plumb_refuses_the_standin_not_v3_unless_a_test_asks():
    with pytest.raises(plumb.LedgerError, match="stand-in, not V3"):
        plumb.plumb(plumb.open_ledger(), standin_runtime(), plumb.surface_from_blob(STANDIN.read_bytes(), None))
    relabelled = plumb.Runtime(**{**standin_runtime().__dict__, "profile": "v3"})  # same blob, other name
    with pytest.raises(plumb.LedgerError, match="stand-in, not V3"):
        plumb.plumb(plumb.open_ledger(), relabelled, plumb.surface_from_blob(STANDIN.read_bytes(), None))


def test_standin_not_v3_v14_metadata_runs_through_plumb_in_memory_only():
    # the stand-in exercises the V14 decode path; no evidence is ever written from it
    conn = plumb.open_ledger()
    plumb.add_entries(conn, [entry("transfer_keep_alive", ("dest", "value"), pallet="Balances"),
                             entry("map_account", (), pallet="Revive")])
    plumb.plumb(conn, standin_runtime(), plumb.surface_from_blob(STANDIN.read_bytes(), None), renames=(),
                allow_standin=True)
    got = dict(conn.execute("SELECT call, status FROM verdict WHERE source = ?", (v.V3_DEMO,)).fetchall())
    assert got == {"transfer_keep_alive": v.UNPROVEN, "map_account": v.UNPROVEN}


# ---------------------------------------------------------------- the producer script

def run(*args):
    return subprocess.run([sys.executable, "-I", "scripts/build_ledger.py", *args], cwd=ROOT,
                          capture_output=True, text=True)


@pytest.mark.parametrize("blob", ["fixtures/metadata_1002.scale", str(BLOB_1002)])  # relative and absolute
def test_build_ledger_script_reproduces_the_committed_ledger_from_any_invocation_path(tmp_path, blob):
    out = tmp_path / "ledger_1002.json"
    res = run("--profile", f"stored:{blob}", "--runtime-record", "evidence/metadata_1002.json",
              "--db", str(tmp_path / "l.db"), "--out", str(out))
    assert res.returncode == 0, res.stderr
    assert out.read_bytes() == LEDGER.read_bytes()
    assert (tmp_path / "ledger_1002.json.blake2-256").read_text() == \
        (ROOT / "evidence" / "ledger_1002.json.blake2-256").read_text()
    assert (tmp_path / "l.db").exists() and not (tmp_path / "l.db.tmp").exists()


def test_build_ledger_script_refuses_the_standin_not_v3(tmp_path):
    res = run("--profile", "stored:fixtures/metadata_standin_sdk.scale", "--runtime-record",
              "evidence/metadata_standin_sdk.json", "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and "stand-in, not V3" in res.stderr
    assert not (tmp_path / "o.json").exists()


def test_build_ledger_script_refuses_a_record_for_another_runtime(tmp_path):
    res = run("--profile", "stored:fixtures/metadata_1002.scale", "--runtime-record", "evidence/metadata_standin_sdk.json",
              "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and not (tmp_path / "o.json").exists()


def test_build_ledger_script_refuses_a_guide_table_for_another_commit_and_keeps_the_old_db(tmp_path):
    (tmp_path / "l.db").write_bytes(b"previous ledger")
    table = json.loads((ROOT / "evidence" / "guide_calls.json").read_text()) | {"commit": "0" * 40}
    (tmp_path / "g.json").write_text(json.dumps(table))
    res = run("--profile", "stored:fixtures/metadata_1002.scale", "--runtime-record", "evidence/metadata_1002.json",
              "--guide-table", str(tmp_path / "g.json"), "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and "guide commit" in res.stderr
    assert (tmp_path / "l.db").read_bytes() == b"previous ledger"


def test_build_ledger_script_turns_a_malformed_table_into_a_refusal_not_a_traceback(tmp_path):
    record = json.loads(DOCS_1002.read_text())
    twin = next(r for r in record["rows"] if r["doc_call"] == "proxy.createPure") | {"doc_call": "proxy.create_pure"}
    record["rows"].append(twin)  # a different spelling of one call: both snake_case to create_pure
    (tmp_path / "d.json").write_text(json.dumps(record))
    (tmp_path / "l.db").write_bytes(b"previous ledger")
    res = run("--profile", "stored:fixtures/metadata_1002.scale", "--runtime-record", "evidence/metadata_1002.json",
              "--docs-table", str(tmp_path / "d.json"), "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and "IntegrityError" in res.stderr and "Traceback" not in res.stderr
    assert not (tmp_path / "l.db.tmp").exists() and (tmp_path / "l.db").read_bytes() == b"previous ledger"


def test_build_ledger_script_refuses_a_docs_table_with_a_doubled_call(tmp_path):
    record = json.loads(DOCS_1002.read_text())
    record["rows"].append(record["rows"][0])
    (tmp_path / "d.json").write_text(json.dumps(record))
    res = run("--profile", "stored:fixtures/metadata_1002.scale", "--runtime-record", "evidence/metadata_1002.json",
              "--docs-table", str(tmp_path / "d.json"), "--db", str(tmp_path / "l.db"), "--out", str(tmp_path / "o.json"))
    assert res.returncode == 1 and "twice" in res.stderr
