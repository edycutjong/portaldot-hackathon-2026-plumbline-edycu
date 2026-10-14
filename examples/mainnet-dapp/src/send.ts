// Send: pay a user, keeping the sender's account alive.
import type { ApiPromise } from "@polkadot/api";

// Written from the mainnet docs: balances.transferKeepAlive(dest, value) -- https://portaldot-dev.readthedocs.io/en/latest/module-interface/extrinsics/balances.html (interface 3)
export function send(api: ApiPromise, dest: string, value: bigint) {
  return api.tx.balances.transferKeepAlive(dest, value);
}
