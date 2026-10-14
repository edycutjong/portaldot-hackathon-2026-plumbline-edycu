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
    assert "parsed 3/3 pages (3 complete), 9 documented calls" in result.stdout
    assert "unpaired ['orphanCall']" in result.stdout


def test_docs_command_labels_standin_comparison_and_exits_1_on_incomplete_page(tmp_path):
    for name in ("index", "alpha", "beta", "gamma"):
        (tmp_path / f"{name}.html").write_text((ROOT / "fixtures" / "docs_layouts" / f"{name}.html").read_text())
    standin = ROOT / "fixtures" / "metadata_standin_sdk.scale"
    ok = CliRunner().invoke(app, ["docs", "--cache", str(tmp_path), "--profile", f"stored:{standin}"])
    assert ok.exit_code == 0, ok.output
    assert "stand-in, not V3" in ok.stdout and "vs interim-sdk" in ok.stdout
    beta = tmp_path / "beta.html"
    beta.write_text(beta.read_text().replace("</section>", "<p>interface: beta.broken (old)</p></section>"))
    bad = CliRunner().invoke(app, ["docs", "--cache", str(tmp_path)])
    assert bad.exit_code == 1
    assert "rejected interface lines" in bad.stdout


def test_docs_command_without_cache_exits_2(tmp_path):
    result = CliRunner().invoke(app, ["docs", "--cache", str(tmp_path)])
    assert result.exit_code == 2


def test_runtime_command_exits_2_when_the_endpoint_is_unreachable():
    for url in ("ws://127.0.0.1:9", "http://127.0.0.1:9"):
        result = CliRunner().invoke(app, ["runtime", "--profile", "interim-sdk", "--url", url])
        assert result.exit_code == 2, (url, result.output)


def test_guide_command_counts_fixture_sites_and_exits_1_off_the_pinned_commit(tmp_path):
    # the layout fixtures are not a git checkout of the pinned commit, and include unresolved sites
    result = CliRunner().invoke(app, ["guide", "--guide-dir", str(ROOT / "fixtures" / "guide_layouts")])
    assert result.exit_code == 1, result.output
    assert "v3-demo    16 sites" in result.stdout and "v3-faq      6 sites" in result.stdout
    assert "unresolved: README.md:9" in result.stdout and "guide at unknown commit (NOT the pinned clean commit" in result.stdout


def test_guide_command_without_checkout_exits_2(tmp_path):
    assert CliRunner().invoke(app, ["guide", "--guide-dir", str(tmp_path)]).exit_code == 2
    (tmp_path / "README.md").write_text("# guide\n")  # README alone (no FAQ.md, no demo/) is not a checkout
    assert CliRunner().invoke(app, ["guide", "--guide-dir", str(tmp_path)]).exit_code == 2
