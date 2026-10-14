"""Explicit rename / substitute / none rows: a documented call (or module) absent from a runtime, and what
replaces it there. Never inferred: each row names its evidence, and `plumb.check_renames` refuses a row
whose documented side exists, or (rename / substitute) whose live side does not, in the runtime it is
applied to (I3). A `none` row records that nothing replaces it; its evidence travels with the ABSENT verdict.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import verdicts as v


@dataclass(frozen=True)
class Rename:
    profile: str      # the runtime profile this row applies to
    pallet: str       # live pallet name
    doc_call: str     # the documented call (snake_case), or for a module row the documented module name
    live_call: str    # the live call that replaces it ('' for a module row or a `none` row)
    kind: str         # verdicts.RENAME | SUBSTITUTE | NONE
    evidence: str
    module: bool = False  # the row renames a whole module: doc_call is the documented module, pallet the live one


RENAMES: tuple[Rename, ...] = (
    Rename("mainnet-1002", "Proxy", "create_pure", "anonymous", v.RENAME,
           "docs proxy.createPure(proxy_type, delay, index) is absent on spec 1002; Proxy.anonymous takes the same "
           "arguments by name and order (evidence/docs_1002.json vs fixtures/metadata_1002.scale; checked by "
           "tests/test_plumb.py)"),
    Rename("mainnet-1002", "Proxy", "kill_pure", "kill_anonymous", v.RENAME,
           "docs proxy.killPure(spawner, proxy_type, index, height, ext_index) is absent on spec 1002; "
           "Proxy.kill_anonymous takes the same arguments by name and order (same evidence and test)"),
)
