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


def test_interface_and_signature_outside_article_body_are_ignored(pages):
    # alpha.html has a well-formed signature + interface pair after the articleBody div closes
    assert "ignored" not in {c.call for c in pages["alpha"].calls}
    assert pages["alpha"].raw_interface_labels == 4


def test_every_fixture_page_is_complete(pages):
    assert all(res.complete for res in pages.values())


def test_rejected_interface_line_and_label_outside_paragraph_are_reported():
    page = ('<div itemprop="articleBody">'
            '<ul><li><strong>interface</strong>: <code>x.tight</code></li></ul>'   # label not inside a <p>
            '<p><strong>interface</strong>: <code>x.foo</code> (deprecated)</p>'     # line the pattern rejects
            '<p>good(a: <code>u8</code>)</p><p><strong>interface</strong>: <code>x.good</code></p>'
            '</div>')
    res = docs.parse_page(page, "x")
    assert res.interfaces == 1 and res.raw_interface_labels == 3
    assert res.unmatched_lines == ["interface: x.foo (deprecated)"]
    assert not res.complete


def test_duplicate_documented_calls_are_reported():
    page = '<div itemprop="articleBody"><p>interface: x.dup</p><p>interface: x.dup</p></div>'
    res = docs.parse_page(page, "x")
    assert docs.duplicate_calls([res]) == {"x.dup": [docs.DOCS_BASE + "x.html (interface 1)",
                                                     docs.DOCS_BASE + "x.html (interface 2)"]}
    assert len(docs.documented_calls([res])) == 1


@pytest.mark.parametrize("text, expected", [
    ("a: u8,, b: u16", (("a", "u8"), ("b", "u16"))),
    ("m: BTreeMap{K, V}, n: u8", (("m", "BTreeMap{K, V}"), ("n", "u8"))),
    ("", ()),
])
def test_split_args_edge_cases(text, expected):
    assert docs.split_args(text) == expected


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


def test_unpaired_doc_call_is_args_unknown_not_compared(pages):
    documented = docs.documented_calls(pages.values())
    live = {"Alpha.orphan_call": [], "Gamma.right_name": [("calls", "Vec<Call>")]}
    cls = {r["doc_call"]: r["class"] for r in docs.compare(documented, live)["rows"]}
    assert cls["alpha.orphanCall"] == cls["gamma.rightName"] == docs.ARGS_UNKNOWN


def test_pallet_present_without_calls_is_call_absent_not_pallet_absent(pages):
    documented = docs.documented_calls(pages.values())
    out = docs.compare(documented, {"Alpha.move_funds": []}, live_pallets=["Alpha", "Gamma"])
    cls = {r["doc_call"]: r["class"] for r in out["rows"]}
    assert cls["gamma.batchThings"] == docs.CALL_ABSENT
    assert cls["beta.approveThing"] == docs.PALLET_ABSENT


def test_underscore_only_arg_mismatch_is_flagged():
    page = ('<div itemprop="articleBody"><p>remark(remark: <code>Bytes</code>)</p><p>interface: system.remark</p>'
            '<p>bond(value: <code>u128</code>)</p><p>interface: system.bond</p></div>')
    documented = docs.documented_calls([docs.parse_page(page, "system")])
    out = docs.compare(documented, {"System.remark": [("_remark", "Bytes")],
                                    "System.bond": [("controller", "AccountId"), ("value", "u128")]})
    flags = {r["doc_call"]: r.get("underscore_only") for r in out["rows"]}
    assert flags == {"system.remark": True, "system.bond": False}
    assert out["counts"]["arg-mismatch-underscore-only"] == 1


def test_signature_without_interface_makes_page_incomplete():
    page = ('<div itemprop="articleBody"><p>first(a: <code>u8</code>)</p><p>second(b: <code>u8</code>)</p>'
            '<p>interface: x.second</p><p>last(c: <code>u8</code>)</p></div>')
    res = docs.parse_page(page, "x")
    assert res.unpaired_signatures == ["first", "last"]
    assert res.calls[0].args == (("b", "u8"),)
    assert not res.complete


def test_text_after_the_closing_bracket_is_not_a_signature():
    res = docs.parse_page('<div itemprop="articleBody"><p>foo(a: <code>u8</code>) -&gt; ()</p>'
                          '<p>interface: x.foo</p></div>', "x")
    assert res.signatures == 0 and res.unpaired == ["foo"]
