"""EVM: call a Solidity contract from the native side."""

# Copied from the V3 guide sample: Revive.call(dest, value, weight_limit, storage_deposit_limit, data)
# -- demo/04_solidity_demo.py:421 (ItsCogumellum/portaldot-v3-testnet-guide@d671a32)


def call_contract(substrate, dest: str, data: bytes, weight: dict, deposit: int):
    return substrate.compose_call(
        call_module="Revive",
        call_function="call",
        call_params={"dest": dest, "value": 0, "weight_limit": weight,
                     "storage_deposit_limit": deposit, "data": data},
    )
