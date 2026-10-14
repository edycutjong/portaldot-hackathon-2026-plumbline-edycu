"""NFT: mint a ticket into an existing collection."""

# Copied from the V3 guide sample: Nfts.mint(collection, item, mint_to, witness_data) -- demo/03_nfts_demo.py:154
# (ItsCogumellum/portaldot-v3-testnet-guide@d671a32)


def mint_ticket(substrate, collection: int, item: int, owner: str):
    return substrate.compose_call(
        call_module="Nfts",
        call_function="mint",
        call_params={"collection": collection, "item": item, "mint_to": owner, "witness_data": None},
    )
