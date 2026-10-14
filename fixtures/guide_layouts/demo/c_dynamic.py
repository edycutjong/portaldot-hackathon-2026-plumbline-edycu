"""Hand-written test fixture: references the AST reader cannot resolve, and must report."""
from somewhere import compose_call as cc_imported

chain = object()
alias = chain.compose_call
dynamic = getattr(chain, "compose_call")("Tokens", "issue", {})
bare = compose_call("Tokens", "burn")
no_params = chain.compose_call("Tokens", "reset", None)


def raw(method):
    return chain.rpc_request(method, ["GadgetApi_lookup", "0x00"])


def compose_call(*args):
    return args


def shadow(names):
    fn = "burn"
    return [chain.compose_call("Tokens", fn, {}) for fn in names]


plain = chain.rpc_request("chain_getHeader", [])
