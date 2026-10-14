import json
from pathlib import Path

import pytest

from plumbline import docs

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "docs"
EVIDENCE = ROOT / "evidence" / "docs_1002.json"


def test_baseline_evidence_reproduces_kitchen_reference_on_1002():
    record = json.loads(EVIDENCE.read_text())
    assert record["profile"] == "mainnet-1002"
    assert (record["pages_parsed"], record["pages"]) == (22, 22)
    c = record["counts"]
    assert (c["documented"], c["absent"], c["arg-mismatch"], c["match"]) == (196, 64, 15, 117)


@pytest.mark.skipif(not (CACHE / "index.html").exists(), reason="run scripts/fetch_docs.py first")
def test_cached_pages_parse_22_of_22_and_match_evidence():
    results = docs.parse_dir(CACHE)
    assert sum(r.parsed for r in results) == len(results) == 22
    assert all(r.interfaces == r.signatures and not r.unpaired for r in results)
    record = json.loads(EVIDENCE.read_text())
    assert {r.page: r.interfaces for r in results} == record["per_page"]
