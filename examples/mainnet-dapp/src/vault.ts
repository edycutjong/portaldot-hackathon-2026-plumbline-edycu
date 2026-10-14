// Vault: a keyless account controlled through a proxy.
import type { ApiPromise } from "@polkadot/api";

// Written from the mainnet docs: proxy.createPure(proxy_type, delay, index) -- https://portaldot-dev.readthedocs.io/en/latest/module-interface/extrinsics/proxy.html (interface 3)
export function openVault(api: ApiPromise) {
  return api.tx.proxy.createPure("Any", 0, 0);
}
