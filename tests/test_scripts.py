"""The evidence producers refuse to write when their inputs can't be trusted."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUTS = ROOT / "fixtures" / "docs_layouts"


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)


def make_cache(tmp_path):
    cache = tmp_path / "docs"
    shutil.copytree(LAYOUTS, cache)
    return cache


def test_docs_baseline_writes_labelled_evidence_for_a_complete_cache(tmp_path):
    out = tmp_path / "e.json"
    res = run("scripts/docs_baseline.py", "--cache", str(make_cache(tmp_path)),
              "--profile", "stored:fixtures/metadata_standin_sdk.scale", "--out", str(out))
    assert res.returncode == 0, res.stderr
    record = json.loads(out.read_text())
    assert record["profile"] == "interim-sdk" and "stand-in, not V3" in record["label"]


def test_docs_baseline_refuses_an_incomplete_page(tmp_path):
    cache = make_cache(tmp_path)
    beta = cache / "beta.html"
    beta.write_text(beta.read_text().replace("</section>", "<p>interface: beta.old (removed)</p></section>"))
    out = tmp_path / "e.json"
    res = run("scripts/docs_baseline.py", "--cache", str(cache),
              "--profile", "stored:fixtures/metadata_1002.scale", "--out", str(out))
    assert res.returncode == 1 and not out.exists()
    assert "incomplete pages ['beta']" in res.stderr


def test_docs_baseline_refuses_a_page_that_differs_from_its_manifest(tmp_path):
    cache = make_cache(tmp_path)
    (cache / "manifest.json").write_text(json.dumps({"files": {"alpha": {"sha256": "0" * 64}}}))
    out = tmp_path / "e.json"
    res = run("scripts/docs_baseline.py", "--cache", str(cache),
              "--profile", "stored:fixtures/metadata_1002.scale", "--out", str(out))
    assert res.returncode == 1 and not out.exists()


def test_docs_baseline_refuses_an_unrecorded_blob_even_with_a_profile_suffix(tmp_path):
    blob = tmp_path / "odd.scale"
    blob.write_bytes((ROOT / "fixtures" / "metadata_1002.scale").read_bytes() + b"\x00")
    out = tmp_path / "e.json"
    res = run("scripts/docs_baseline.py", "--cache", str(make_cache(tmp_path)),
              "--profile", f"stored:{blob}@mainnet-1002", "--out", str(out))
    assert res.returncode != 0 and not out.exists()
    assert "origin not recorded" in res.stderr


def test_record_metadata_exits_2_on_an_unreachable_endpoint(tmp_path):
    res = run("scripts/record_metadata.py", "--profile", "interim-sdk", "--url", "ws://127.0.0.1:9",
              "--out", str(tmp_path / "b.scale"), "--evidence", str(tmp_path / "b.json"))
    assert res.returncode == 2 and "cannot reach" in res.stderr
    assert not (tmp_path / "b.scale").exists()
