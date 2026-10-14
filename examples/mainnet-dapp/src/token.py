"""Token: move an asset balance."""

# Written from the mainnet docs: assets.transfer(id, target, amount) -- https://portaldot-dev.readthedocs.io/en/latest/module-interface/extrinsics/assets.html (interface 29)


def pay(substrate, asset_id: int, to: str, amount: int):
    return substrate.compose_call(
        call_module="Assets",
        call_function="transfer",
        call_params={"id": asset_id, "target": to, "amount": amount},
    )
