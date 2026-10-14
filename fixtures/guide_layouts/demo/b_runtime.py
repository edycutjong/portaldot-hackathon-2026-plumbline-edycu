"""Hand-written test fixture: runtime API sites, SDK and raw."""
chain = object()

result = chain.runtime_call("GadgetApi", "lookup", [1, 2])
raw = chain.rpc_request("state_call", ["GadgetApi_lookup", "0x00"])
other = chain.rpc_request("chain_getBlockHash", [0])
