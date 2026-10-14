"""Hand-written test fixture: SDK methods reached through strings or rebound names."""
import operator

chain = object()
name = pick()
by_name = getattr(chain, name)("Tokens", "issue", {})
caller = operator.methodcaller("compose_call", "Tokens", "issue", {})
table = chain.__dict__["runtime_call"]
FN = "burn"


def rebind():
    global FN
    FN = pick()


rebound = chain.compose_call("Tokens", FN, {})
