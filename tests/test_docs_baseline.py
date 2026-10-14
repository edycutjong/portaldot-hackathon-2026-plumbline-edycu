"""The mainnet-1002 docs baseline. The parse itself needs the fetched pages (scripts/fetch_docs.py);
the comparison is re-derived offline from the committed call table and the committed 1002 blob."""

import json
from pathlib import Path

import pytest

from plumbline import docs, metadata as md

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "docs"
EVIDENCE = ROOT / "evidence" / "docs_1002.json"
BLOB_1002 = ROOT / "fixtures" / "metadata_1002.scale"


@pytest.fixture(scope="module")
def record():
    return json.loads(EVIDENCE.read_text())


def test_committed_evidence_is_for_mainnet_1002_blob(record):
    assert record["profile"] == "mainnet-1002"
    assert record["metadata_sha256"] == md.sha256_hex(BLOB_1002.read_bytes())
    assert (record["pages_parsed"], record["pages_complete"], record["pages"]) == (22, 22, 22)


def test_compare_rederived_from_committed_call_table_and_1002_blob(record):
    documented = {}
    for row in record["rows"]:
        pallet, call = row["doc_call"].split(".")
        args = tuple(tuple(a) for a in row["doc_args"]) if row["doc_args"] is not None else None
        documented[row["doc_call"]] = docs.DocCall(pallet=pallet, call=call, args=args, page=row["page"],
                                                   url="", layout="plain", position=0)
    decoded = md.decode_metadata(BLOB_1002.read_bytes(), "legacy")
    out = docs.compare(documented, md.call_surface(decoded), md.pallet_names(decoded))
    c = out["counts"]
    assert (c["documented"], c["absent"], c["arg-mismatch"], c["match"]) == (196, 64, 15, 117)
    assert c == record["counts"]
    assert [(r["doc_call"], r["class"]) for r in out["rows"]] == [(r["doc_call"], r["class"]) for r in record["rows"]]


@pytest.mark.skipif(not (CACHE / "index.html").exists(),
                    reason="needs the fetched docs pages: run scripts/fetch_docs.py first")
def test_cached_pages_parse_complete_and_match_committed_table(record):
    results = docs.parse_dir(CACHE)
    assert sum(r.complete for r in results) == len(results) == 22
    assert all(r.interfaces == r.signatures and not r.unpaired for r in results)
    assert {r.page: r.interfaces for r in results} == record["per_page"]
    parsed = {k: [list(a) for a in v.args] if v.args is not None else None
              for k, v in docs.documented_calls(results).items()}
    assert parsed == {r["doc_call"]: r["doc_args"] for r in record["rows"]}
