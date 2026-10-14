from typer.testing import CliRunner

import plumbline
from plumbline.cli import app


def test_version_command_prints_package_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == plumbline.__version__


def test_runtime_command_reads_stored_blob_offline():
    result = CliRunner().invoke(app, ["runtime", "--profile", "stored:fixtures/metadata_1002.scale"])
    assert result.exit_code == 0, result.output
    assert "metadata V13" in result.stdout and "155 calls" in result.stdout


def test_runtime_command_rejects_unknown_profile_with_exit_2():
    result = CliRunner().invoke(app, ["runtime", "--profile", "devnet"])
    assert result.exit_code == 2
