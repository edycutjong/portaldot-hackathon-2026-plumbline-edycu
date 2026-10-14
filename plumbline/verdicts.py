"""The ledger's vocabulary: the ONE place verdict, source and kind strings are defined.

The SQLite schema's CHECK constraints, the ledger export and every module that assigns a verdict
import these names; no other module spells a verdict string. Definitions:

  PROVEN             documented, present in the target runtime, and a canary receipt exists whose
                     extrinsic, read back by block hash, decodes to the same pallet.call (I1).
  DRIFTED            documented, absent from the target's metadata, with an explicit rename/substitute
                     row naming the live equivalent (I3; never inferred).
  ARG_DRIFT          documented and present, but the documented arguments differ from the live ones.
  ARGS-UNCHECKED     documented and present; the source gives no arguments to compare; no receipt yet.
  ABSENT             documented (call or listed module) and absent from the target's metadata, with no
                     known equivalent.
  UNDOCUMENTED       a live call that no parsed source names (directly or as the live side of a rename).
  UNPROVEN           documented and present with matching arguments (or a listed module that is present),
                     but no receipt yet. Also the live side of a rename that no receipt proves.
  EXPECTED-REJECT    a contract artifact whose imports the target is known to reject (from `check`).
  COMPAT-UNMEASURED  an artifact/target cell with no measurement behind it (from `check`).
  TYPE-TRAP          a call site that passes a value of the wrong account type (`check` does not detect it yet).
  UNSCANNED          a dApp call site the scanner could not resolve statically (from `check`).
  UNKNOWN-DOC        a documented entry the ledger cannot check against this runtime: the parser could
                     not resolve it, or it is a runtime API (V13/V14 metadata lists none; reading the V15
                     API list is not implemented yet).

Per report (stamped on the runtime): CACHED (measured from a stored metadata blob, not read live),
STALE (the live runtime has changed since).
"""

PROVEN = "PROVEN"
DRIFTED = "DRIFTED"
ARG_DRIFT = "ARG_DRIFT"
ARGS_UNCHECKED = "ARGS-UNCHECKED"
ABSENT = "ABSENT"
UNDOCUMENTED = "UNDOCUMENTED"
UNPROVEN = "UNPROVEN"
EXPECTED_REJECT = "EXPECTED-REJECT"
COMPAT_UNMEASURED = "COMPAT-UNMEASURED"
TYPE_TRAP = "TYPE-TRAP"
UNSCANNED = "UNSCANNED"
UNKNOWN_DOC = "UNKNOWN-DOC"

VERDICTS = (PROVEN, DRIFTED, ARG_DRIFT, ARGS_UNCHECKED, ABSENT, UNDOCUMENTED, UNPROVEN, EXPECTED_REJECT,
            COMPAT_UNMEASURED, TYPE_TRAP, UNSCANNED, UNKNOWN_DOC)

CACHED = "CACHED"
STALE = "STALE"
REPORT_STATES = (CACHED, STALE)

# Where a documented entry came from. RUNTIME marks UNDOCUMENTED rows: the live call itself is the source.
V3_GUIDE = "v3-guide"          # the V3 guide README
V3_DEMO = "v3-demo"            # the guide's demo/*.py sample code
V3_FAQ = "v3-faq"              # the V3 FAQ module table
MAINNET_DOCS = "mainnet-docs"  # portaldot-dev.readthedocs.io extrinsic pages
RUNTIME = "runtime"
SOURCES = (V3_GUIDE, V3_DEMO, V3_FAQ, MAINNET_DOCS)

# What a documented entry names.
CALL = "call"                  # a dispatchable pallet call
RUNTIME_API = "runtime-api"    # a runtime API method (state_call / runtime_call)
MODULE = "module"              # a whole pallet (FAQ rows)
DOC_KINDS = (CALL, RUNTIME_API, MODULE)

# Rename rows (explicit, with evidence; never fuzzy).
RENAME = "rename"
SUBSTITUTE = "substitute"
NONE = "none"
RENAME_KINDS = (RENAME, SUBSTITUTE, NONE)
