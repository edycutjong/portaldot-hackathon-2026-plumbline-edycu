from typer.testing import CliRunner

import plumbline
from plumbline.cli import app


def test_version_command_prints_package_version():
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == plumbline.__version__
