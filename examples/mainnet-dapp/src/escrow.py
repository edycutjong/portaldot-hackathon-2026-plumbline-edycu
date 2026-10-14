"""Escrow: deploy the escrow contract with its code in one call."""

# Written from the mainnet docs: contracts.instantiateWithCode(value, gas_limit, storage_deposit_limit,
# code, data, salt) -- https://portaldot-dev.readthedocs.io/en/latest/module-interface/extrinsics/contracts.html (interface 5)


def deploy_escrow(substrate, code: bytes, constructor: bytes):
    return substrate.compose_call(
        call_module="Contracts",
        call_function="instantiate_with_code",
        call_params={
            "value": 0,
            "gas_limit": {"ref_time": 5_000_000_000, "proof_size": 1_000_000},
            "storage_deposit_limit": None,
            "code": code,
            "data": constructor,
            "salt": b"escrow-1",
        },
    )
