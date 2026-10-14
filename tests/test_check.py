"""`plumbline check`: wasm import scan, JS/TS and Python call sites, verdicts against a profile, exit codes."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from plumbline import check, plumb, verdicts as v
from plumbline.renames import Rename

from .test_plumb import BLOB_1002, ROOT

WASM = ROOT / "fixtures" / "wasm"

RT = plumb.Runtime(profile="target-x", label="synthetic target", endpoint=None, spec_name="t", spec_version=7,
                   tx_version=1, metadata_version=14, metadata_hash="cd" * 32, code_hash=None, block_hash=None,
                   fetched_at="2026-10-16T00:00:00+00:00")
SURFACE = plumb.Surface(
    calls={"Balances.transfer_keep_alive": [("dest", "Address"), ("value", "Compact<u128>")],
           "Proxy.anonymous": [("proxy_type", "u8"), ("delay", "u32"), ("index", "u16")],
           "System.remark": [("_remark", "Bytes")]},
    pallets=["Balances", "Proxy", "System"], metadata_hash=RT.metadata_hash)
RENAMES = (Rename("target-x", "Proxy", "create_pure", "anonymous", v.RENAME, "test evidence"),)


def test_make_wasm_fixtures_are_committed_as_generated():
    out = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "make_wasm.py"), "--verify"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_wasm_imports_are_read_from_the_bytes():
    assert check.wasm_imports((WASM / "seal0_only.wasm").read_bytes()) == [
        ("env", "memory", "memory"), ("seal0", "seal_caller", "func"),
        ("seal0", "seal_value_transferred", "func"), ("seal0", "seal_return", "func")]
    assert [m for m, _, _ in check.wasm_imports(check.artifact_wasm(WASM / "seal_newer.contract"))] == [
        "env", "seal0", "seal1", "seal2"]


@pytest.mark.parametrize("data, why", [
    (b"not wasm", "magic"),
    (b"\0asm\x01\0\0\0\x02\x05\x01", "truncated"),          # import section shorter than it says
    (b"\0asm\x01\0\0\0\x02\x04\x01\x01a\x01", "truncated"),  # an import cut off inside its field name
])
def test_malformed_wasm_is_refused(data, why):
    with pytest.raises(check.WasmError, match=why):
        check.wasm_imports(data)


def test_abi_family():
    assert check.abi_family([("env", "memory", "memory"), ("seal0", "x", "func")]) == check.SEAL0
    assert check.abi_family([("seal0", "x", "func"), ("seal2", "y", "func")]) == check.SEAL_NEWER
    assert check.abi_family([("__unstable__", "y", "func")]) == check.SEAL_NEWER
    assert check.abi_family([("env", "memory", "memory")]) is None


@pytest.mark.parametrize("name, target, status", [  # seal_newer imports seal1.get_storage + seal2.set_storage
    ("seal0_only.wasm", "mainnet-1002", v.UNPROVEN),
    ("seal_newer.wasm", "mainnet-1002", v.EXPECTED_REJECT),
    ("seal_newer.contract", "mainnet-1002", v.EXPECTED_REJECT),
    ("seal0_only.wasm", "v3", v.COMPAT_UNMEASURED),
    ("seal_newer.wasm", "v3", v.COMPAT_UNMEASURED),
    ("no_seal.wasm", "mainnet-1002", v.UNSCANNED),
])
def test_artifact_cells(name, target, status):
    finding = check.check_artifact(WASM / name, name, target)
    assert finding.status == status
    if status == v.COMPAT_UNMEASURED:
        assert f"on {target} yet" in finding.detail


def test_newer_abi_imports_no_measurement_covers_are_unmeasured():
    imports = [("env", "memory", "memory"), ("seal1", "seal_call", "func")]
    status, evidence, fix = check.artifact_cell("mainnet-1002", imports)
    assert status == v.COMPAT_UNMEASURED and fix == ""
    one = check.artifact_cell("mainnet-1002", imports + [("seal2", "set_storage", "func")])
    assert one[0] == v.COMPAT_UNMEASURED  # one of the two measured functions is not the measured set
    hit = check.artifact_cell("mainnet-1002", imports + [("seal2", "set_storage", "func"), ("seal1", "get_storage", "func")])
    assert hit[0] == v.EXPECTED_REJECT and "seal2.set_storage" in hit[1] and "inferred" in hit[1]
    assert "or target V3" not in hit[2]


def test_ink_target_dir_is_scanned_other_build_output_is_not(tmp_path):
    for rel in ("target/ink/flip.contract", "target/debug/x.wasm", "node_modules/y.wasm", "contract/z.WASM"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((WASM / "seal_newer.contract").read_bytes() if rel.endswith(".contract")
                         else (WASM / "seal0_only.wasm").read_bytes())
    stats = {}
    found = check.check(tmp_path, RT, SURFACE, RENAMES, stats=stats)
    assert [f.ref for f in found] == ["contract/z.WASM", "target/ink/flip.contract"] and stats["files"] == 2


def test_empty_dir_says_nothing_to_check(tmp_path):
    stats = {}
    text = check.render(check.check(tmp_path, RT, SURFACE, RENAMES, stats=stats), RT, "offline", stats["files"])
    assert "nothing to check in 0 files scanned" in text and text.endswith("exit 0\n")


def test_unreadable_files_and_unparsed_frameworks_are_unscanned(tmp_path):
    write(tmp_path, "a.vue", "<script>api.tx.nfts.mint()</script>")
    write(tmp_path, "b.ts", "api.tx.nfts.mint()")
    (tmp_path / "b.ts").chmod(0)
    try:
        found = check.check(tmp_path, RT, SURFACE, RENAMES)
    finally:
        (tmp_path / "b.ts").chmod(0o644)
    assert [(f.ref, f.status) for f in found] == [("a.vue", v.UNSCANNED), ("b.ts", v.UNSCANNED)]


def test_unreadable_and_polkavm_artifacts_are_unscanned(tmp_path):
    (tmp_path / "bad.wasm").write_bytes(b"\0asm\x01\0\0\0\x02\x09")
    (tmp_path / "x.polkavm").write_bytes(b"PVM\0")
    (tmp_path / "y.contract").write_text("{}")
    for name in ("bad.wasm", "x.polkavm", "y.contract"):
        assert check.check_artifact(tmp_path / name, name, "mainnet-1002").status == v.UNSCANNED


def test_mask_js_hides_comments_and_strings_and_keeps_lines():
    src = ('api.tx.x.y(1) // api.tx.no.no(1)\n/* api.tx.no.no(\n) */ s = "api.tx.no.no(" + `t api.tx.no.no(1) '
           '${api.tx.yes.call(1)} ${`${api.tx.deep.call()}`}`\n')
    masked = check.mask_js(src)
    assert masked.count("\n") == src.count("\n") and len(masked) == len(src)
    # template text is masked; code inside ${...} (also nested) stays visible
    assert [s[1:3] for s in check.js_sites(src)] == [("x", "y"), ("deep", "call"), ("yes", "call")]


def test_mask_js_reads_regex_literals_as_literals():
    src = ("const q = /'/g; api.tx.balances.transfer(a, b)\n"
           "const r = s.replace(/\\/\\/[/]x/, ''); api.tx.system.remark(c)\n"
           "const half = total / 2; api.tx.assets.mint(a, b, c) / 1\n")
    assert [s[1:4] for s in check.js_sites(src)] == [("balances", "transfer", 2), ("system", "remark", 1),
                                                     ("assets", "mint", 3)]


def test_js_sites_arity_and_dynamic():
    src = ("api.tx.balances.transferKeepAlive(dest, f(a, b), [1, 2])\n"
           "this.api.tx.proxy.createPure()\n"
           "api.tx.utility.batch(...calls)\n"
           "api.tx[pallet][call](x)\n"
           "api.tx.assets[method](x)\n"
           "api.tx.system.remark('a, b')\n")
    assert [s[:4] for s in check.js_sites(src)] == [
        (1, "balances", "transferKeepAlive", 3), (2, "proxy", "createPure", 0), (3, "utility", "batch", None),
        (4, None, None, None), (5, None, None, None), (6, "system", "remark", 1)]


def test_js_sites_optional_chaining_references_destructuring_and_type_arguments():
    src = ("api.tx?.balances?.transferKeepAlive?.(a, b)\n"
           "api.tx.balances?.transfer(a)\n"
           "const f = api.tx.balances.transfer\n"
           "const pal = api.tx.proxy;\n"
           "const { tx } = api\n"
           "api.tx.balances.transfer(dest as Foo<A, B>, 1)\n"
           "const t = receipt.tx; t.wait()\n")
    got = [s[:4] + (s[4],) for s in check.js_sites(src)]
    assert got == [(1, "balances", "transferKeepAlive", 2, ""), (2, "balances", "transfer", 1, ""),
                   (3, None, None, None, "api.tx reference, not a call"),
                   (4, None, None, None, "api.tx reference, not a call"),
                   (5, None, None, None, "api.tx destructured"),
                   (6, "balances", "transfer", None, "")]  # a `<` makes the count unknown, never a false drift


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_check_verdicts_per_site(tmp_path):
    write(tmp_path, "a/send.ts", "api.tx.balances.transferKeepAlive(dest, value)\n"
                                 "api.tx.balances.transferKeepAlive(dest)\n"
                                 "api.tx.proxy.createPure(t, d, i)\n"
                                 "api.tx.nfts.mint(c, i, w)\n")
    write(tmp_path, "b/app.py", "def f(s, name):\n"
                                "    s.compose_call('System', 'remark', {'remark': b'x'})\n"
                                "    s.compose_call('balances', 'transfer_keep_alive', {'dest': 1, 'value': 2})\n"
                                "    s.compose_call('Balances', name, {})\n")
    write(tmp_path, "node_modules/x/ignored.ts", "api.tx.nfts.mint()\n")
    findings = check.check(tmp_path, RT, SURFACE, RENAMES)
    got = [(f.ref, f.subject, f.status) for f in findings]
    assert got == [
        ("a/send.ts:1", "balances.transferKeepAlive", v.ARGS_UNCHECKED),
        ("a/send.ts:2", "balances.transferKeepAlive", v.ARG_DRIFT),
        ("a/send.ts:3", "proxy.createPure", v.DRIFTED),
        ("a/send.ts:4", "nfts.mint", v.ABSENT),
        ("b/app.py:2", "System.remark", v.ARG_DRIFT),
        ("b/app.py:3", "balances.transfer_keep_alive", v.ABSENT),  # Python: the SDK matches the pallet exactly
        ("b/app.py:4", "dynamic call", v.UNSCANNED),
    ]
    by_ref = {f.ref: f for f in findings}
    assert by_ref["a/send.ts:3"].fix == "-> Proxy.anonymous(proxy_type, delay, index)"
    assert by_ref["a/send.ts:2"].detail == "1 positional argument passed, Balances.transfer_keep_alive takes 2"
    assert by_ref["b/app.py:2"].fix == "-> live shape System.remark(_remark)"
    assert check.exit_code(findings) == 1


def test_proven_needs_a_receipt_key_and_unblocked_runs_exit_0(tmp_path):
    write(tmp_path, "send.ts", "api.tx.balances.transferKeepAlive(dest, value)\n")
    (finding,) = check.check(tmp_path, RT, SURFACE, RENAMES, proven={"Balances.transfer_keep_alive"})
    assert finding.status == v.PROVEN
    assert check.exit_code([finding]) == 0
    assert check.render([finding], RT, "offline").endswith("blocking 0 · exit 0\n")


def test_render_is_deterministic_and_has_no_clock_time(tmp_path):
    write(tmp_path, "send.ts", "api.tx.proxy.createPure(t, d, i)\n")
    one = check.render(check.check(tmp_path, RT, SURFACE, RENAMES), RT, "offline")
    two = check.render(check.check(tmp_path, RT, SURFACE, RENAMES), RT, "offline")
    assert one == two and "2026" not in one
    assert one.splitlines()[-1] == "summary: 1 finding · DRIFTED 1 · blocking 1 · exit 1"
    assert "not checked yet: TYPE-TRAP" in one


def test_rename_rows_are_checked_against_the_target(tmp_path):
    bad = (Rename("target-x", "Proxy", "anonymous", "create_pure", v.RENAME, "wrong way round"),)
    with pytest.raises(plumb.LedgerError):
        check.check(tmp_path, RT, SURFACE, bad)


def test_unparsable_python_is_unscanned(tmp_path):
    write(tmp_path, "broken.py", "def (:\n")
    (finding,) = check.check(tmp_path, RT, SURFACE, RENAMES)
    assert (finding.status, finding.subject) == (v.UNSCANNED, "unparsed file")


def test_blocking_set_is_the_spec_list():
    assert set(check.BLOCKING) == {v.EXPECTED_REJECT, v.TYPE_TRAP, v.DRIFTED, v.ABSENT, v.ARG_DRIFT}


def test_measured_cells_cite_the_baseline_and_say_no_receipt_here():
    from plumbline import metadata as md
    assert set(check.REJECTED_IMPORTS) <= set(md.PROFILES)
    for imports in ([("seal0", "a", "func")], [("seal1", "get_storage", "func"), ("seal2", "set_storage", "func")]):
        status, evidence, _ = check.artifact_cell("mainnet-1002", imports)
        assert status in (v.UNPROVEN, v.EXPECTED_REJECT)
        assert "baseline measurement 2026-10-11" in evidence and "no receipt in this repository" in evidence
    assert json.loads(json.dumps(check.Finding("r", "s", v.ABSENT).to_dict()))["status"] == v.ABSENT
    assert BLOB_1002.exists()


def test_jsx_markup_slashes_are_not_regex_literals():
    src = ("const A = () => <p>{label}</p>; <button onClick={() => api.tx.balances.transfer(a, 1)}>Send</button>\n"
           "<Input value={v} /> <Button onClick={() => api.tx.system.remark(r)} />\n"
           "const half = n++ / 2; api.tx.assets.burn(a, b, c)\n")
    assert [s[1:4] for s in check.js_sites(src)] == [("balances", "transfer", 2), ("system", "remark", 1),
                                                     ("assets", "burn", 3)]


def test_only_an_api_like_receiver_is_read_as_a_runtime_call():
    src = ("receipt.tx.hash.toString()\n"
           "const t = api.tx;\n"
           "const { balances } = api.tx;\n"
           "const { tx, hash } = receipt;\n"
           "const { tx } = this.api;\n"
           "polkadotApi.tx.nfts.mint(c, i, w)\n"
           "(await getApi()).tx.nfts.mint(c, i, w)\n")
    got = [(s[0], s[1], s[4]) for s in check.js_sites(src)]
    assert got == [(1, None, "receipt.tx.hash.toString(): receiver not an api"), (2, None, "api.tx aliased"),
                   (3, None, "api.tx aliased"), (5, None, "api.tx destructured"), (6, "nfts", ""),
                   (7, None, "(expression).tx.nfts.mint(): receiver not an api")]


def test_receipt_tx_is_never_a_blocking_verdict(tmp_path):
    write(tmp_path, "a.ts", "receipt.tx.hash.toString()\n")
    found = check.check(tmp_path, RT, SURFACE, RENAMES)
    assert [f.status for f in found] == [v.UNSCANNED] and check.exit_code(found) == 0


def test_unreadable_directories_are_reported_and_skips_are_named(tmp_path):
    write(tmp_path, "locked/x.ts", "api.tx.nfts.mint()\n")
    write(tmp_path, "node_modules/y/z.ts", "api.tx.nfts.mint()\n")
    write(tmp_path, "src/ok.ts", "api.tx.balances.transferKeepAlive(a, b)\n")
    (tmp_path / "locked").chmod(0)
    try:
        stats = {}
        found = check.check(tmp_path, RT, SURFACE, RENAMES, stats=stats)
    finally:
        (tmp_path / "locked").chmod(0o755)
    assert [(f.ref, f.status) for f in found] == [("locked/", v.UNSCANNED), ("src/ok.ts:1", v.ARGS_UNCHECKED)]
    assert stats["skipped"] == ["node_modules/"]
    text = check.render(found, RT, "offline", stats["files"], stats["skipped"])
    assert "not scanned (generated or vendored): node_modules/" in text
