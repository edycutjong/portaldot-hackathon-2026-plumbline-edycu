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
    assert [s.ref for s in demo_a] == ["demo/a_calls.py:9", "demo/a_calls.py:15", "demo/a_calls.py:21",
                                       "demo/a_calls.py:25", "demo/a_calls.py:33", "demo/a_calls.py:34"]
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


def readme_sites():
    return guide.parse_readme((LAYOUTS / "README.md").read_text())


def test_readme_call_tables_take_the_pallet_from_the_section_heading():
    tables = [(s.ref, s.pallet, s.calls, s.how) for s in readme_sites() if s.how in (guide.TABLE, guide.UNRESOLVED)]
    assert tables == [
        ("README.md:9", None, ("orphan",), guide.UNRESOLVED),  # under "1.1 Wallet Setup": not a pallet name
        ("README.md:19", "Tokens", ("issue",), guide.TABLE),
        ("README.md:20", "Tokens", ("lock",), guide.TABLE),
        ("README.md:20", "Tokens", ("unlock",), guide.TABLE),
        ("README.md:21", None, (), guide.UNRESOLVED),  # "plain words": a row with no name is reported
        ("README.md:30", "Gadgets", ("make_gadget",), guide.TABLE),
        ("README.md:45", "Widgets", ("spin",), guide.TABLE),  # indented, `Function` header
        ("README.md:46", None, (), guide.UNRESOLVED),  # unticked name
        ("README.md:47", None, ("lock_it", "CollectionConfig"), guide.UNRESOLVED),  # extra ticks: not guessed
        ("README.md:48", "Widgets", ("wobble",), guide.TABLE),
        ("README.md:52", "Widgets", ("quoted",), guide.TABLE),  # a quoted table
        ("README.md:60", None, ("summary",), guide.UNRESOLVED),  # "### Overview" is not a numbered section
    ]


def test_readme_skips_fenced_code():
    # the fenced "### Setup" heading and its table row would otherwise rename the pallet / add a call
    assert all(s.pallet != "Setup" and "not_a_call" not in s.calls for s in readme_sites())


def test_readme_explorer_paths_name_calls_and_runtime_apis_only():
    paths = [(s.ref, s.kind, s.pallet, s.calls) for s in readme_sites() if s.how == guide.EXPLORER]
    assert paths == [
        ("README.md:32", v.RUNTIME_API, "gadgetApi", ("lookup",)),
        ("README.md:33", v.CALL, "Gadgets", ("make_gadget",)),
        ("README.md:48", v.CALL, "Widgets", ("wobble",)),  # bold segments, inside a table row
        ("README.md:54", v.CALL, "Widgets", ("transferKeepAlive",)),  # ASCII arrows; name kept as written
        ("README.md:64", v.CALL, "Paths", ("go_left",)),  # ticked segments, `>` arrows, singular "Extrinsic"
    ]


def test_readme_lines_that_look_like_call_docs_but_yield_no_site_are_unaccounted():
    _, unaccounted = guide.scan_readme((LAYOUTS / "README.md").read_text())
    # 39 call table in a fence · 65 path with no call segment · 68 path in a fence · 71 table with no rows
    assert unaccounted == ["README.md:39", "README.md:65", "README.md:68", "README.md:71"]


def test_faq_module_table_rows_reduce_status_to_the_closed_set_and_count_awaiting_lines():
    sites, awaiting = guide.parse_faq((LAYOUTS / "FAQ.md").read_text())
    assert [(s.pallet, s.calls, s.how, s.status) for s in sites] == [
        ("tokens", ("",), guide.FAQ_TABLE, guide.FAQ_DOCUMENTED),
        ("conviction_voting", ("",), guide.FAQ_TABLE, guide.FAQ_LISTED),
        (None, ("alpha", "beta"), guide.UNRESOLVED, guide.FAQ_UNCLASSIFIED),  # two names: reported, not picked
        (None, (), guide.UNRESOLVED, guide.FAQ_UNCLASSIFIED),
        ("later", ("",), guide.FAQ_TABLE, guide.FAQ_AWAITING),
        ("odd", ("",), guide.FAQ_TABLE, guide.FAQ_UNCLASSIFIED),
    ]
    assert all(s.kind == v.MODULE and s.source == v.V3_FAQ for s in sites)
    assert awaiting == [9, 11]  # the status-key definition line is not counted


def test_dynamic_references_are_reported_unresolved_never_dropped():
    text = (LAYOUTS / "demo" / "c_dynamic.py").read_text()
    sites = guide.parse_demo(text, "demo/c_dynamic.py")
    assert [(int(s.ref.rsplit(":", 1)[1]), s.kind, s.how) for s in sites] == [
        (2, v.CALL, guide.UNRESOLVED),           # imported under another name
        (5, v.CALL, guide.UNRESOLVED),           # alias = chain.compose_call
        (6, v.CALL, guide.UNRESOLVED),           # getattr(chain, "compose_call")(…)
        (7, v.CALL, guide.LITERAL),              # bare compose_call(…)
        (8, v.CALL, guide.LITERAL),              # call_params=None
        (12, v.RUNTIME_API, guide.UNRESOLVED),   # rpc_request(method, …) with a parameter as the method
        (15, v.CALL, guide.UNRESOLVED),          # a local definition shadows the SDK name
        (21, v.CALL, guide.UNRESOLVED),          # comprehension target shadows `fn = "burn"`
    ]
    assert guide.unaccounted_references(text, "demo/c_dynamic.py") == []


@pytest.mark.parametrize("line, unaccounted", [
    ("Open Developer → Chain state to read storage.", []),            # not a call path: no refusal
    ("Developer > Settings, then Developer >= 3 tabs.", []),
    ("Developer → Extrinsics → Gadgets → make_gadget, then Developer → Extrinsics → Gadgets.", ["R.md:1"]),
])
def test_only_unmatched_call_path_starts_are_unaccounted(line, unaccounted):
    assert guide.scan_readme(line + "\n", "R.md")[1] == unaccounted


def test_names_reached_through_strings_or_rebinding_are_unresolved():
    text = (LAYOUTS / "demo" / "d_strings.py").read_text()
    sites = guide.parse_demo(text, "demo/d_strings.py")
    assert [(int(s.ref.rsplit(":", 1)[1]), s.kind, s.how) for s in sites] == [
        (6, v.CALL, guide.UNRESOLVED),          # getattr(chain, name) with a non-literal name
        (7, v.CALL, guide.UNRESOLVED),          # methodcaller("compose_call", …)
        (8, v.RUNTIME_API, guide.UNRESOLVED),   # chain.__dict__["runtime_call"]
        (17, v.CALL, guide.UNRESOLVED),         # FN = "burn" at module level, rebound via `global FN`
    ]
    assert guide.unaccounted_references(text, "demo/d_strings.py") == []


def test_string_tokens_naming_an_sdk_method_must_be_covered():
    assert guide.unaccounted_references('x = f"compose_call"\ny = "compose_call"\n', "x.py", guide.DemoScan([], set())) \
        == ["x.py:2"]  # the plain string is found; an f-string is not a plain literal


def test_omitted_or_none_call_params_are_zero_arguments():
    sites = {s.ref: s for s in guide.parse_demo((LAYOUTS / "demo" / "c_dynamic.py").read_text(), "demo/c_dynamic.py")}
    assert sites["demo/c_dynamic.py:7"].args == () and sites["demo/c_dynamic.py:8"].args == ()


def test_unaccounted_references_flag_a_code_token_no_site_covers():
    # a keyword named like an SDK method is code, not a call site: the reconciliation must flag it
    assert guide.unaccounted_references("f(compose_call=1)\n# compose_call in a comment\n", "x.py") == ["x.py:1"]


@pytest.mark.parametrize("binding", [
    "import fn", "from m import fn", "global fn", "del fn", "fn += 'x'", "for fn in names: pass",
    "with open('x') as fn: pass", "fn, other = 'a', 'b'",
    "try:\n        pass\n    except Exception as fn:\n        pass",
    "def fn(): pass", "class fn: pass",
])
def test_any_other_binding_of_the_name_leaves_it_unresolved(binding):
    text = f"def f(chain, names):\n    fn = 'burn'\n    {binding}\n    return chain.compose_call('T', fn, {{}})\n"
    (site,) = guide.parse_demo(text, "x.py")
    assert site.how == guide.UNRESOLVED, binding


def test_site_dict_round_trip(demo_a):
    assert [guide.site_from_dict(guide.site_to_dict(s)) for s in demo_a] == demo_a


def test_parse_guide_hashes_every_file_it_read():
    res = guide.parse_guide(LAYOUTS)
    assert sorted(res.files) == ["FAQ.md", "README.md", "demo/a_calls.py", "demo/b_runtime.py", "demo/c_dynamic.py",
                                 "demo/d_strings.py"]
    assert res.count(v.V3_DEMO) == 20 and res.count(v.V3_FAQ) == 6
    assert res.unaccounted == ["README.md:39", "README.md:65", "README.md:68", "README.md:71"]


def test_clone_state_refuses_a_folder_inside_another_repository():
    # fixtures/guide_layouts sits inside this repository: it is not a guide checkout of its own
    assert guide.clone_state(LAYOUTS) == (None, True)


def test_clone_state_of_a_plain_folder(tmp_path):
    assert guide.clone_state(tmp_path) == (None, True)
