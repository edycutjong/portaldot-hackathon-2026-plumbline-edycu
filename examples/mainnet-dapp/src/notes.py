"""Notes: store a short note on chain."""

# Written from the mainnet docs: system.remark(remark) -- https://portaldot-dev.readthedocs.io/en/latest/module-interface/extrinsics/system.html (interface 6)


def note(substrate, text: str):
    return substrate.compose_call(
        call_module="System",
        call_function="remark",
        call_params={"remark": text.encode()},
    )
