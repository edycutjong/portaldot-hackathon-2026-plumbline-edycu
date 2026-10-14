"""The verdict vocabulary has one source: plumbline/verdicts.py. No other module spells a verdict."""

import re
from pathlib import Path

from plumbline import verdicts as v

PACKAGE = Path(__file__).resolve().parents[1] / "plumbline"

# The product spec's list, in its order ("Verdict vocabulary"): the oracle this test pins.
SPEC_VERDICTS = ["PROVEN", "DRIFTED", "ARG_DRIFT", "ARGS-UNCHECKED", "ABSENT", "UNDOCUMENTED", "UNPROVEN",
                 "EXPECTED-REJECT", "COMPAT-UNMEASURED", "TYPE-TRAP", "UNSCANNED", "UNKNOWN-DOC"]


def test_verdicts_equal_the_spec_list_exactly():
    assert list(v.VERDICTS) == SPEC_VERDICTS
    assert v.REPORT_STATES == ("CACHED", "STALE")
    assert v.RENAME_KINDS == ("rename", "substitute", "none")
    assert v.SOURCES == ("v3-guide", "v3-demo", "v3-faq", "mainnet-docs")


def test_no_other_module_spells_a_verdict_string():
    pattern = re.compile(r"""["'](%s)["']""" % "|".join(re.escape(x) for x in v.VERDICTS + v.REPORT_STATES))
    offenders = []
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == "verdicts.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == []
