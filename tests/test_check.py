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


@pytest.mark.parametrize("name, target, status", [
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


def test_unreadable_and_polkavm_artifacts_are_unscanned(tmp_path):
    (tmp_path / "bad.wasm").write_bytes(b"\0asm\x01\0\0\0\x02\x09")
    (tmp_path / "x.polkavm").write_bytes(b"PVM\0")
    (tmp_path / "y.contract").write_text("{}")
    for name in ("bad.wasm", "x.polkavm", "y.contract"):
        assert check.check_artifact(tmp_path / name, name, "mainnet-1002").status == v.UNSCANNED


def test_mask_js_hides_comments_and_strings_and_keeps_lines():
    src = 'a.tx.x.y(1) // api.tx.no.no(1)\n/* api.tx.no.no(\n) */ s = "api.tx.no.no(" + `t ${api.tx.no.no(1)}`\n'
    masked = check.mask_js(src)
    assert masked.count("\n") == src.count("\n") and len(masked) == len(src)
    assert [s[1:3] for s in check.js_sites(src)] == [("x", "y")]


def test_js_sites_arity_and_dynamic():
    src = ("api.tx.balances.transferKeepAlive(dest, f(a, b), [1, 2])\n"
           "this.api.tx.proxy.createPure()\n"
           "api.tx.utility.batch(...calls)\n"
           "api.tx[pallet][call](x)\n"
           "api.tx.assets[method](x)\n"
           "api.tx.system.remark('a, b')\n")
    assert check.js_sites(src) == [(1, "balances", "transferKeepAlive", 3), (2, "proxy", "createPure", 0),
                                   (3, "utility", "batch", None), (4, None, None, None), (5, None, None, None),
                                   (6, "system", "remark", 1)]


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
    assert by_ref["a/send.ts:2"].detail == "1 positional arguments passed, Balances.transfer_keep_alive takes 2"
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
    assert one.splitlines()[-1] == "summary: 1 findings · DRIFTED 1 · blocking 1 · exit 1"


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


def test_compat_cells_use_known_profiles_and_verdicts():
    from plumbline import metadata as md
    for (profile, family), (status, evidence) in check.COMPAT.items():
        assert profile in md.PROFILES and family in (check.SEAL0, check.SEAL_NEWER)
        assert status in v.VERDICTS and "baseline measurement" in evidence
    assert json.dumps(check.Finding("r", "s", v.ABSENT).to_dict())
    assert BLOB_1002.exists()
