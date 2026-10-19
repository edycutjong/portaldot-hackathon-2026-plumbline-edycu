"""The CLI faces wired in this row: `plumbline check` (golden output on the 1002 profile) and `plumbline plumb`."""

import json
import subprocess
import sys

from typer.testing import CliRunner

from plumbline.cli import STANDIN_BANNER, app

from .test_plumb import LEDGER, ROOT

EXAMPLE = ROOT / "examples" / "mainnet-dapp"
GOLDEN = ROOT / "fixtures" / "expected_check_1002.txt"
GOLDEN_V3 = ROOT / "fixtures" / "expected_check_v3.txt"


def fresh(args, cwd):
    """`plumbline …` in a new interpreter (isolated mode: nothing from cwd on sys.path)."""
    code = "import sys; sys.argv[0] = 'plumbline'; from plumbline.cli import app; app()"
    return subprocess.run([sys.executable, "-I", "-c", code, *args], capture_output=True, text=True, cwd=cwd)


def test_check_reproduces_the_1002_golden_byte_for_byte_from_a_fresh_process(tmp_path):
    for cwd, path in ((ROOT, "examples/mainnet-dapp"), (tmp_path, str(EXAMPLE))):
        out = fresh(["check", path, "--target", "mainnet-1002", "--offline"], cwd)
        assert out.returncode == 1, out.stderr  # the example has blocking findings by design
        assert out.stdout == GOLDEN.read_text(), f"from {cwd}"


def test_golden_rows_are_the_reviewed_ones():
    rows = [line.split()[:3] for line in GOLDEN.read_text().splitlines()[2:-1] if not line.startswith(" ")]
    assert [r[0] for r in rows] == ["contract/minimal_seal0.wasm", "src/escrow.py:8", "src/evm.py:8", "src/nft.py:8",
                                    "src/notes.py:7", "src/router.ts:5", "src/send.ts:6", "src/token.py:7",
                                    "src/vault.ts:6"]
    statuses = {r[0]: line for r, line in zip(rows, [x for x in GOLDEN.read_text().splitlines()[2:-1]
                                                       if not x.startswith(" ")])}
    assert " DRIFTED " in statuses["src/vault.ts:6"] and "-> Proxy.anonymous(" in statuses["src/vault.ts:6"]
    assert " ARG_DRIFT " in statuses["src/escrow.py:8"] and " ABSENT " in statuses["src/nft.py:8"]
    assert GOLDEN.read_text().splitlines()[-1].endswith("blocking 5 · exit 1")


def test_example_call_sites_cite_their_source():
    for path in sorted((EXAMPLE / "src").iterdir()):
        text = path.read_text()
        if path.name == "router.ts":
            continue  # the dynamic site has no source to cite
        assert "readthedocs.io" in text or "demo/0" in text, path.name


def test_check_json_and_exit_codes():
    result = CliRunner().invoke(app, ["check", str(EXAMPLE), "--target", "mainnet-1002", "--offline", "--json"])
    data = json.loads(result.output)
    assert result.exit_code == data["exit"] == 1
    assert len(data["findings"]) == 9 and data["target"] == "mainnet-1002"
    assert CliRunner().invoke(app, ["check", str(EXAMPLE / "README.md"), "--target", "mainnet-1002"]).exit_code == 2
    v3 = CliRunner().invoke(app, ["check", str(EXAMPLE), "--target", "v3", "--offline"])
    assert v3.exit_code == 0 and v3.output == GOLDEN_V3.read_text()  # V3 has no blocking finding for this example
    stored = CliRunner().invoke(app, ["check", str(EXAMPLE), "--target", f"stored:{GOLDEN}", "--offline"])
    assert stored.exit_code == 2


def test_check_on_the_standin_not_v3_is_bannered():
    result = CliRunner().invoke(app, ["check", str(EXAMPLE), "--target", "interim-sdk", "--offline"])
    lines = result.output.splitlines()
    assert lines[0] == STANDIN_BANNER and lines[-1] == STANDIN_BANNER
    assert "(stand-in, not V3)" in lines[2]


def test_plumb_cli_rebuilds_the_committed_ledger(tmp_path):
    out = tmp_path / "ledger.json"
    result = fresh(["plumb", "--profile", "stored:fixtures/metadata_1002.scale",
                    "--runtime-record", "evidence/metadata_1002.json", "--out", str(out),
                    "--db", str(tmp_path / "ledger.db")], ROOT)
    assert result.returncode == 0, result.stderr
    assert out.read_bytes() == LEDGER.read_bytes()
    refused = fresh(["plumb", "--profile", "stored:fixtures/metadata_standin_sdk.scale",
                     "--runtime-record", "evidence/metadata_standin_sdk.json", "--out", str(tmp_path / "x.json"),
                     "--db", str(tmp_path / "x.db")], ROOT)
    assert refused.returncode == 1 and "stand-in, not V3" in refused.stderr
    assert not (tmp_path / "x.json").exists()


def test_a_stored_profile_that_is_not_metadata_is_a_usage_error():
    for args in (["runtime", "--profile", f"stored:{GOLDEN}"], ["diff", f"stored:{GOLDEN}", f"stored:{GOLDEN}"]):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 2 and "missing 'meta' magic" in result.output, args
