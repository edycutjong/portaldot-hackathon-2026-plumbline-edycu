"""The V3 guide parser on hand-written layout fixtures (fixtures/guide_layouts)."""

from pathlib import Path

import pytest

from plumbline import guide, verdicts as v

ROOT = Path(__file__).resolve().parents[1]
LAYOUTS = ROOT / "fixtures" / "guide_layouts"


def by_ref(sites):
    return {(s.ref, s.calls): s for s in sites}


@pytest.fixture(scope="module")
def demo_a():
    return guide.parse_demo((LAYOUTS / "demo" / "a_calls.py").read_text(), "demo/a_calls.py")


def test_demo_sites_are_found_by_ast_not_by_text(demo_a):
    # 6 real compose_call sites; the comment and the string literal that mention it are not sites
    assert [s.ref for s in demo_a] == ["demo/a_calls.py:9", "demo/a_calls.py:15", "demo/a_calls.py:20",
                                       "demo/a_calls.py:24", "demo/a_calls.py:32", "demo/a_calls.py:33"]
    assert all(s.source == v.V3_DEMO and s.kind == v.CALL for s in demo_a)


def test_parsing_never_executes_the_demo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    guide.parse_demo((LAYOUTS / "demo" / "a_calls.py").read_text(), "demo/a_calls.py")
    assert not (tmp_path / "EXECUTED_MARKER").exists()


def test_literal_keyword_and_positional_sites_with_param_keys(demo_a):
    issue, lock = demo_a[0], demo_a[1]
    assert (issue.pallet, issue.calls, issue.how, issue.args) == ("Tokens", ("issue",), guide.LITERAL, ("to", "amount"))
    assert (lock.pallet, lock.calls, lock.how, lock.args) == ("Tokens", ("lock",), guide.LITERAL, ("who",))


def test_local_conditional_resolves_to_both_branches_and_dynamic_params_stay_unknown(demo_a):
    send = demo_a[2]
    assert (send.calls, send.how, send.args) == (("transfer_keep_alive", "transfer"), guide.LOCAL, None)


def test_a_function_parameter_is_unresolved_not_guessed(demo_a):
    assert (demo_a[3].calls, demo_a[3].how, demo_a[3].resolved) == ((), guide.UNRESOLVED, False)


def test_assignments_are_read_per_function_scope(demo_a):
    inner, outer = demo_a[4], demo_a[5]
    assert (inner.how, inner.calls) == (guide.UNRESOLVED, ())  # inner `func = compute()`
    assert (outer.how, outer.calls) == (guide.LOCAL, ("burn",))  # outer `func = 'burn'`, inner ignored


def test_runtime_api_sites_sdk_and_raw_state_call():
    sites = guide.parse_demo((LAYOUTS / "demo" / "b_runtime.py").read_text(), "demo/b_runtime.py")
    assert [(s.kind, s.pallet, s.calls, s.how, s.ref) for s in sites] == [
        (v.RUNTIME_API, "GadgetApi", ("lookup",), guide.RUNTIME_CALL, "demo/b_runtime.py:4"),
        (v.RUNTIME_API, "GadgetApi", ("lookup",), guide.STATE_CALL, "demo/b_runtime.py:5"),
    ]


def test_readme_call_tables_take_the_pallet_from_the_section_heading():
    sites = guide.parse_readme((LAYOUTS / "README.md").read_text())
    tables = [(s.pallet, s.calls, s.how) for s in sites if s.how in (guide.TABLE, guide.UNRESOLVED)]
    assert tables == [
        (None, (), guide.UNRESOLVED),  # `orphan` under "1.1 Wallet Setup": not a pallet name
        ("Tokens", ("issue",), guide.TABLE),
        ("Tokens", ("lock",), guide.TABLE),
        ("Tokens", ("unlock",), guide.TABLE),
        ("Gadgets", ("make_gadget",), guide.TABLE),
    ]


def test_readme_explorer_paths_name_calls_and_runtime_apis_only():
    sites = guide.parse_readme((LAYOUTS / "README.md").read_text())
    paths = [(s.kind, s.pallet, s.calls) for s in sites if s.how == guide.EXPLORER]
    assert paths == [(v.RUNTIME_API, "gadgetApi", ("lookup",)), (v.CALL, "Gadgets", ("make_gadget",))]


def test_faq_module_table_rows_and_awaiting_lines():
    sites, awaiting = guide.parse_faq((LAYOUTS / "FAQ.md").read_text())
    assert [(s.pallet, s.how, s.note) for s in sites] == [
        ("tokens", guide.FAQ_TABLE, "Documented on testnet"),
        ("conviction_voting", guide.FAQ_TABLE, "Listed; timing not specified"),
        (None, guide.UNRESOLVED, "two names in one cell"),
        (None, guide.UNRESOLVED, "no module name"),
    ]
    assert all(s.kind == v.MODULE and s.source == v.V3_FAQ for s in sites)
    assert awaiting == [9, 11]  # the status-key definition line is not counted


def test_site_dict_round_trip(demo_a):
    assert [guide.site_from_dict(guide.site_to_dict(s)) for s in demo_a] == demo_a


def test_parse_guide_hashes_every_file_it_read():
    res = guide.parse_guide(LAYOUTS)
    assert sorted(res.files) == ["FAQ.md", "README.md", "demo/a_calls.py", "demo/b_runtime.py"]
    assert res.count(v.V3_DEMO) == 8 and res.count(v.V3_FAQ) == 4


def test_clone_state_refuses_a_folder_inside_another_repository():
    # fixtures/guide_layouts sits inside this repository: it is not a guide checkout of its own
    assert guide.clone_state(LAYOUTS) == (None, True)


def test_clone_state_of_a_plain_folder(tmp_path):
    assert guide.clone_state(tmp_path) == (None, True)
