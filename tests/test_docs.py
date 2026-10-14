from pathlib import Path

import pytest

from plumbline import docs

ROOT = Path(__file__).resolve().parents[1]
LAYOUTS = ROOT / "fixtures" / "docs_layouts"


@pytest.fixture(scope="module")
def pages():
    return {r.page: r for r in docs.parse_dir(LAYOUTS)}


def test_index_lists_linked_pages_once_in_order():
    assert docs.index_pages((LAYOUTS / "index.html").read_text()) == ["alpha", "beta", "gamma"]


@pytest.mark.parametrize("page, interfaces, signatures, unpaired, layout", [
    ("alpha", 4, 3, ["orphanCall"], "plain"),
    ("beta", 2, 2, [], "api-tx"),
    ("gamma", 3, 3, ["rightName"], "dash"),
])
def test_per_page_counts_for_each_layout(pages, page, interfaces, signatures, unpaired, layout):
    res = pages[page]
    assert (res.interfaces, res.signatures, res.unpaired) == (interfaces, signatures, unpaired)
    assert {c.layout for c in res.calls} == {layout}


def test_complexity_notes_are_not_signatures(pages):
    # alpha has a bare "O(1)" paragraph and beta an "O(K) where ... (keys.len)" paragraph
    assert pages["alpha"].signatures == 3 and pages["beta"].signatures == 2


def test_signature_pairs_only_with_same_named_interface(pages):
    calls = {c.call: c for c in pages["gamma"].calls}
    assert calls["rightName"].args is None
    assert calls["batchThings"].args == (("calls", "Vec<Call>"),)


def test_args_split_at_top_level_commas_and_unescape(pages):
    calls = {c.call: c for c in pages["alpha"].calls}
    assert calls["setLimits"].args == (("limits", "Vec<(u32, u64)>"), ("flag", "bool"))
    assert calls["moveFunds"].args == (("target", "MultiAddress"), ("amount", "Compact<u128>"))
    assert calls["resetAll"].args == ()


def test_text_outside_article_body_is_ignored(pages):
    assert "ignored" not in {c.call for c in pages["alpha"].calls}


def test_source_ref_names_page_url_and_position(pages):
    call = pages["beta"].calls[1]
    assert call.source_ref == docs.DOCS_BASE + "beta.html (interface 2)"


def test_missing_linked_page_fails_loud(tmp_path):
    (tmp_path / "index.html").write_text((LAYOUTS / "index.html").read_text())
    (tmp_path / "alpha.html").write_text((LAYOUTS / "alpha.html").read_text())
    with pytest.raises(FileNotFoundError):
        docs.parse_dir(tmp_path)


@pytest.mark.parametrize("camel, snake", [
    ("transferKeepAlive", "transfer_keep_alive"), ("Transfer", "transfer"), ("withWeight2", "with_weight_2"),
    ("asMultiThreshold1", "as_multi_threshold_1"), ("batch", "batch"),
])
def test_snake_case(camel, snake):
    assert docs.snake(camel) == snake


def test_compare_classes(pages):
    documented = docs.documented_calls(pages.values())
    live = {
        "Alpha.move_funds": [("target", "LookupSource"), ("amount", "Compact<Balance>")],  # names match, types differ
        "Alpha.reset_all": [("who", "AccountId")],                                       # arity differs
        "Beta.approve_thing": [("threshold", "u16"), ("call_hash", "[u8; 32]")],
        "Alpha.extra": [],                                                                # undocumented
        "Delta.unrelated": [],
    }
    out = docs.compare(documented, live)
    cls = {r["doc_call"]: r["class"] for r in out["rows"]}
    assert cls["alpha.moveFunds"] == docs.MATCH
    assert cls["alpha.resetAll"] == docs.ARG_MISMATCH
    assert cls["alpha.setLimits"] == docs.CALL_ABSENT
    assert cls["gamma.batchThings"] == docs.PALLET_ABSENT
    assert out["counts"]["undocumented"] == 2
    assert out["counts"]["undocumented_in_documented_pallets"] == 1
    assert out["pallets_without_docs_page"] == ["Delta"]
