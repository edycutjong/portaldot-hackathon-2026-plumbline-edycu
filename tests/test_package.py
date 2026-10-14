from pathlib import Path

from typer.testing import CliRunner

import plumbline
from plumbline.cli import app

ROOT = Path(__file__).resolve().parents[1]


def test_version_command_prints_package_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == plumbline.__version__


def test_runtime_command_reads_stored_blob_offline():
    result = CliRunner().invoke(app, ["runtime", "--profile", f"stored:{ROOT / 'fixtures' / 'metadata_1002.scale'}"])
    assert result.exit_code == 0, result.output
    assert "metadata V13" in result.stdout and "155 calls" in result.stdout
    assert "Portaldot mainnet, spec 1002" in result.stdout


def test_runtime_command_rejects_unknown_profile_with_exit_2():
    result = CliRunner().invoke(app, ["runtime", "--profile", "devnet"])
    assert result.exit_code == 2


def test_docs_command_parses_layout_fixtures():
    result = CliRunner().invoke(app, ["docs", "--cache", str(ROOT / "fixtures" / "docs_layouts")])
    assert result.exit_code == 0, result.output
    assert "parsed 3/3 pages, 9 documented calls" in result.stdout
    assert "unpaired ['orphanCall']" in result.stdout


def test_docs_command_without_cache_exits_2(tmp_path):
    result = CliRunner().invoke(app, ["docs", "--cache", str(tmp_path)])
    assert result.exit_code == 2
