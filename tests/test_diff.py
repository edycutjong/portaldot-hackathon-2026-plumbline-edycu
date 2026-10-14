"""The runtime diff: one normal form for V13 and V14+, the six diff kinds, explicit renames only.

Every test that diffs against fixtures/metadata_standin_sdk.scale says "stand-in, not V3" in its name or id:
that blob is an upstream polkadot-sdk dev node, used here only as V14 decoder input. No number asserted
below is a Portaldot V3 finding.
"""

import json
import sqlite3

import pytest

from plumbline import diff, metadata as md, plumb, verdicts as v
from plumbline.renames import Rename

from .test_plumb import BLOB_1002, STANDIN

STANDIN_ID = "stand-in, not V3"


@pytest.fixture(scope="module")
def nf_1002():
    return diff.normalise(md.decode_metadata(BLOB_1002.read_bytes(), "legacy"), 13)


@pytest.fixture(scope="module")
def nf_standin():
    return diff.normalise(md.decode_metadata(STANDIN.read_bytes(), None), 14)


def test_type_name_drops_generic_prefixes_and_spaces():
    assert diff.type_name("T::Balance") == "Balance"
    assert diff.type_name("<T as frame_system::Config>::AccountId") == "AccountId"
    assert diff.type_name("Compact<BalanceOf<T>>") == "Compact<BalanceOf<T>>"
    assert diff.type_name("Vec<(T::AccountId, u32)>") == "Vec<(AccountId,u32)>"
    assert diff.type_name(None) == ""


def test_normalise_v13_matches_the_recorded_1002_surface(nf_1002):
    # 31 pallets / 155 calls: the same counts evidence/metadata_1002.json's producer printed for this blob
    assert nf_1002.metadata_version == 13
    assert len(nf_1002.pallets) == 31 and nf_1002.call_count() == 155
    assert nf_1002.pallets["Balances"].calls["transfer_keep_alive"] == (("dest", "LookupSource"),
                                                                      ("value", "Compact<Balance>"))
    assert nf_1002.pallets["Balances"].events["Transfer"] == ("AccountId", "AccountId", "Balance")
    ed_type, ed_value = nf_1002.pallets["Balances"].constants["ExistentialDeposit"]
    assert ed_type == "Balance" and ed_value.startswith("40")  # Vec<u8> length prefix 16 bytes
    assert "InsufficientBalance" in nf_1002.pallets["Balances"].errors


@pytest.mark.parametrize("label", [STANDIN_ID])
def test_normalise_v14_makes_compact_explicit_stand_in_not_v3(nf_standin, label):
    assert nf_standin.metadata_version == 14
    call = dict(nf_standin.pallets["Balances"].calls["transfer_keep_alive"])
    assert call["value"] == "Compact<Balance>"  # typeName T::Balance on a compact registry type
    assert nf_standin.pallets["Balances"].events["Transfer"] == ("AccountId", "AccountId", "Balance")
    assert nf_standin.pallets["Balances"].constants["ExistentialDeposit"][0] == "u128"  # no typeName on constants


@pytest.mark.parametrize("which", ["1002", STANDIN_ID])
def test_a_runtime_diffed_with_itself_is_empty(which, nf_1002, nf_standin):
    nf = nf_1002 if which == "1002" else nf_standin
    assert diff.diff(nf, nf) == []


def test_diff_1002_vs_standin_not_v3_uses_only_explicit_renames(nf_1002, nf_standin):
    rows = diff.diff(nf_1002, nf_standin)
    renamed = [r for r in rows if r.kind == diff.RENAMED]
    assert [(r.pallet, r.before["call"], r.after["call"]) for r in renamed] == [
        ("Proxy", "anonymous", "create_pure"), ("Proxy", "kill_anonymous", "kill_pure")]
    assert all("explicit rename row" in r.note for r in renamed)
    items = {(r.pallet, r.item, r.kind) for r in rows}
    # the two sides of a rename are not also reported as removed / added
    assert ("Proxy", "call:anonymous", diff.REMOVED) not in items
    assert ("Proxy", "call:create_pure", diff.ADDED) not in items
    # a call the stand-in has under another name with no rename row stays plain removed / added: never guessed
    assert ("Balances", "call:transfer", diff.REMOVED) in items
    assert ("Balances", "call:transfer_allow_death", diff.ADDED) in items


def test_diff_1002_vs_standin_not_v3_covers_every_kind_and_is_deterministic(nf_1002, nf_standin):
    rows = diff.diff(nf_1002, nf_standin)
    assert {r.kind for r in rows} == set(diff.DIFF_KINDS)
    assert rows == diff.diff(nf_1002, nf_standin)
    added_pallets = {r.pallet for r in rows if r.kind == diff.ADDED and r.item == ""}
    assert {"Revive", "Nfts"} <= added_pallets
    tka = next(r for r in rows if r.pallet == "Balances" and r.item == "call:transfer_keep_alive")
    assert tka.kind == diff.ARG_CHANGED and tka.note == "types_only"  # LookupSource vs AccountIdLookupOf<T>
    counts = diff.summary(rows)
    assert counts["renamed"] == 2
    assert sum(n for k, n in counts.items() if not k.endswith("only")) == len(rows)


def test_diff_standin_not_v3_vs_1002_reverses_the_rename(nf_1002, nf_standin):
    renamed = [r for r in diff.diff(nf_standin, nf_1002) if r.kind == diff.RENAMED]
    assert [(r.before["call"], r.after["call"]) for r in renamed] == [("create_pure", "anonymous"),
                                                                      ("kill_pure", "kill_anonymous")]


def nf(**pallets):
    return diff.NormalForm(14, {name: p for name, p in sorted(pallets.items())})


def test_rename_rows_apply_only_when_each_side_exists_on_one_runtime_only():
    rows = (Rename("x", "P", "new", "old", v.RENAME, "evidence"),)
    a = nf(P=diff.Pallet(calls={"old": (("a", "u8"),)}))
    b = nf(P=diff.Pallet(calls={"new": (("a", "u8"),)}))
    assert [r.kind for r in diff.diff(a, b, rows)] == [diff.RENAMED]
    both = nf(P=diff.Pallet(calls={"old": (("a", "u8"),), "new": (("a", "u8"),)}))
    assert sorted(r.kind for r in diff.diff(a, both, rows)) == [diff.ADDED]  # old still exists: not a rename
    assert sorted(r.kind for r in diff.diff(a, b, ())) == [diff.ADDED, diff.REMOVED]  # no row: no rename
    none_row = (Rename("x", "P", "new", "", v.NONE, "nothing replaces it"),)
    assert sorted(r.kind for r in diff.diff(a, b, none_row)) == [diff.ADDED, diff.REMOVED]


def test_rename_with_changed_argument_names_says_so():
    rows = (Rename("x", "P", "new", "old", v.RENAME, "evidence"),)
    a = nf(P=diff.Pallet(calls={"old": (("a", "u8"),)}))
    b = nf(P=diff.Pallet(calls={"new": (("b", "u8"),)}))
    (row,) = diff.diff(a, b, rows)
    assert row.note.endswith("argument names differ too")


def test_const_and_event_changes():
    a = nf(P=diff.Pallet(constants={"K": ("u32", "01000000"), "Gone": ("u8", "00")}, events={"E": ("u8",)}))
    b = nf(P=diff.Pallet(constants={"K": ("u32", "02000000"), "New": ("u8", "01")},
                         events={"E": ("u16",), "F": ()}))
    rows = {(r.item, r.kind): r for r in diff.diff(a, b)}
    assert rows[("const:K", diff.CONST_CHANGED)].note == "value changed"
    assert rows[("const:Gone", diff.CONST_CHANGED)].after is None
    assert rows[("const:New", diff.CONST_CHANGED)].before is None
    assert rows[("event:E", diff.EVENT_CHANGED)].note == "field types changed"
    assert rows[("event:F", diff.EVENT_CHANGED)].note == "added"


def test_arg_change_with_new_names_is_not_types_only():
    a = nf(P=diff.Pallet(calls={"c": (("endowment", "u128"),)}))
    b = nf(P=diff.Pallet(calls={"c": (("value", "u128"),)}))
    (row,) = diff.diff(a, b)
    assert row.kind == diff.ARG_CHANGED and row.note == ""


def test_diff_kinds_are_the_ledger_schema_kinds_and_rows_store():
    conn = plumb.open_ledger()
    for rid, profile in ((1, "a"), (2, "b")):
        conn.execute("INSERT INTO runtime (id, profile, label, spec_name, spec_version, metadata_version, "
                     "metadata_hash, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
                     (rid, profile, "synthetic", "t", 1, 14, "ab" * 32, "2026-10-16T00:00:00+00:00"))
    a = nf(P=diff.Pallet(calls={"old": ()}))
    b = nf(Q=diff.Pallet())
    rows = diff.diff(a, b)
    assert diff.store(conn, 1, 2, rows) == 2
    stored = conn.execute("SELECT pallet, item, kind, before_json, after_json FROM diff_row ORDER BY pallet").fetchall()
    assert stored == [("P", "", "removed", json.dumps({"calls": 1}), "null"),
                      ("Q", "", "added", "null", json.dumps({"calls": 0}))]
    with pytest.raises(sqlite3.IntegrityError):  # a kind outside DIFF_KINDS is refused by the schema
        conn.execute("INSERT INTO diff_row (from_runtime, to_runtime, pallet, item, kind) VALUES (1,2,'P','x','moved')")


def test_cli_diff_1002_vs_standin_not_v3_is_bannered_text_and_json():
    from typer.testing import CliRunner

    from plumbline.cli import STANDIN_BANNER, app

    args = ["diff", f"stored:{BLOB_1002}", f"stored:{STANDIN}"]
    text = CliRunner().invoke(app, args + ["--kind", "renamed"])
    assert text.exit_code == 0, text.output
    lines = text.output.splitlines()
    assert lines[0] == STANDIN_BANNER and lines[-1] == STANDIN_BANNER
    assert "  renamed       call   Proxy.anonymous -> create_pure" in text.output
    assert "2 shown" in lines[-2]
    as_json = CliRunner().invoke(app, args + ["--json"])
    data = json.loads(as_json.output)
    assert data["warning"] == STANDIN_BANNER and data["to"]["metadata_version"] == 14
    assert len(data["rows"]) == sum(n for k, n in data["summary"].items() if not k.endswith("only"))
    bad = CliRunner().invoke(app, args + ["--kind", "moved"])
    assert bad.exit_code == 2


def test_cli_diff_without_a_standin_has_no_banner():
    from typer.testing import CliRunner

    from plumbline.cli import STANDIN_BANNER, app

    result = CliRunner().invoke(app, ["diff", f"stored:{BLOB_1002}", f"stored:{BLOB_1002}"])
    assert result.exit_code == 0 and STANDIN_BANNER not in result.output
    assert result.output.rstrip().endswith("(0 rows)")
