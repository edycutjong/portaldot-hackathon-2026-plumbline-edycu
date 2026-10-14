"""The committed V3 guide call table (evidence/guide_calls.json), re-derived from a fresh clone when one
is present (scripts/fetch_guide.py clones it outside the tree)."""

import json
import os
from pathlib import Path

import pytest

from plumbline import guide

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "guide_calls.json"
CLONE = Path(os.environ.get("PLUMBLINE_GUIDE_DIR", Path.home() / ".cache" / "plumbline-sources" / "guide"))


@pytest.fixture(scope="module")
def table():
    return json.loads(EVIDENCE.read_text())


def test_committed_call_table_counts(table):
    assert table["commit"] == guide.GUIDE_COMMIT
    demo, readme, faq = table["counts"]["v3-demo"], table["counts"]["v3-guide"], table["counts"]["v3-faq"]
    assert (demo["compose_call_sites"], demo["compose_call_unresolved"], demo["distinct_pallet_calls"]) == (46, 0, 46)
    # 46 distinct pallet.call names: Balances.transfer_keep_alive has two sites, and one Assets site names
    # two candidates (transfer_keep_alive / transfer), so the two 46s coincide
    assert demo["candidate_sites"] == ["demo/01_assets_demo.py:147"]
    assert table["unaccounted"] == []
    # grep counts 47 lines; the extra one is a comment, not a call site
    assert demo["text_lines_mentioning_compose_call"] == 47
    assert demo["text_lines_not_a_call_site"] == ["demo/04_solidity_demo.py:411"]
    assert (demo["runtime_call_sites"], demo["state_call_sites"]) == (1, 1)
    assert (faq["modules"], faq["unresolved"]) == (12, 0)
    assert (readme["call_table_names"], readme["explorer_paths"], readme["unresolved"]) == (39, 3, 0)


def test_committed_call_table_holds_no_guide_text(table):
    # only names, refs and the FAQ status reduced to its closed set; no free-text field at all
    for site in table["sites"]:
        assert set(site) == {"source", "kind", "pallet", "calls", "ref", "how", "args", "status"}
        assert site["status"] in guide.FAQ_STATUSES + ("",)
    statuses = [s["status"] for s in table["sites"] if s["source"] == "v3-faq"]
    assert (statuses.count(guide.FAQ_LISTED), statuses.count(guide.FAQ_DOCUMENTED)) == (10, 2)


def test_committed_files_are_the_parsed_inputs(table):
    assert sorted(table["files"]) == ["FAQ.md", "README.md"] + [
        f"demo/0{i}_{n}_demo.py" for i, n in enumerate(["base", "assets", "uniques", "nfts", "solidity"])]


@pytest.mark.skipif(not (CLONE / "README.md").exists(), reason="needs scripts/fetch_guide.py (clone outside the tree)")
def test_fresh_clone_reproduces_the_committed_call_table(table):
    head, dirty = guide.clone_state(CLONE)
    assert (head, dirty) == (guide.GUIDE_COMMIT, False)
    res = guide.parse_guide(CLONE)
    assert [guide.site_to_dict(s) for s in res.sites] == table["sites"]
    assert res.files == table["files"] and res.awaiting_lines == table["faq_awaiting_lines"]
    assert res.unaccounted == table["unaccounted"] == []
