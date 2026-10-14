"""Explicit rename / substitute rows: a documented call that is absent from a runtime, and the live call
that replaces it there. Never inferred: each row names its evidence, and `plumb.check_renames` refuses
a row whose documented side exists or whose live side does not, in the runtime it is applied to (I3).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import verdicts as v


@dataclass(frozen=True)
class Rename:
    profile: str      # the runtime profile this row applies to
    pallet: str       # live pallet name
    doc_call: str     # the documented call, snake_case
    live_call: str    # the live call that replaces it
    kind: str         # verdicts.RENAME | SUBSTITUTE | NONE
    evidence: str


RENAMES: tuple[Rename, ...] = (
    Rename("mainnet-1002", "Proxy", "create_pure", "anonymous", v.RENAME,
           "docs proxy.createPure(proxy_type, delay, index) is absent on spec 1002; Proxy.anonymous takes the same "
           "arguments by name and order (evidence/docs_1002.json vs fixtures/metadata_1002.scale). Upstream "
           "pallet-proxy later renamed anonymous proxies to pure proxies"),
    Rename("mainnet-1002", "Proxy", "kill_pure", "kill_anonymous", v.RENAME,
           "docs proxy.killPure(spawner, proxy_type, index, height, ext_index) is absent on spec 1002; "
           "Proxy.kill_anonymous takes the same arguments by name and order (same evidence as create_pure)"),
)
