// Router: replays a call chosen at run time (a pattern check cannot resolve statically).
import type { ApiPromise } from "@polkadot/api";

export function route(api: ApiPromise, pallet: string, call: string, args: unknown[]) {
  return api.tx[pallet][call](...args);
}
