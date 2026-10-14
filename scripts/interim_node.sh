#!/usr/bin/env bash
# Start / stop the interim-sdk chain: an upstream polkadot-sdk `substrate-node --dev`, archive mode.
# A stand-in while the Portaldot V3 testnet is unreachable — NOT V3. Nothing measured on it is a V3 finding.
#
#   scripts/interim_node.sh start | stop | status
#
# Ports (this project only): WS+HTTP RPC 39944, p2p 30339. Only the PID this script started is ever stopped.
# Env: PLUMBLINE_INTERIM_BIN (node binary), PLUMBLINE_INTERIM_DIR (state dir, default ~/.cache/plumbline-interim).
set -euo pipefail

STATE_DIR="${PLUMBLINE_INTERIM_DIR:-$HOME/.cache/plumbline-interim}"
BIN="${PLUMBLINE_INTERIM_BIN:-$STATE_DIR/substrate-node-aarch64-apple-darwin}"
# polkadot-sdk release polkadot-stable2606-3, asset substrate-node-aarch64-apple-darwin
BIN_SHA256="e687db4993973bd83922cfa9f01e29bb549af16560511b1fd51ff883b996207f"
RPC_PORT=39944
P2P_PORT=30339
PID_FILE="$STATE_DIR/node.pid"
LOG_FILE="$STATE_DIR/node.log"
DATA_DIR="$STATE_DIR/chain-sdk"

listener() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1 || true; }

start() {
  mkdir -p "$STATE_DIR"
  [[ -x "$BIN" ]] || { echo "node binary not found: $BIN (set PLUMBLINE_INTERIM_BIN)" >&2; exit 1; }
  local got; got="$(shasum -a 256 "$BIN" | cut -d' ' -f1)"
  [[ "$got" == "$BIN_SHA256" ]] || { echo "sha256 mismatch for $BIN: $got" >&2; exit 1; }
  for port in "$RPC_PORT" "$P2P_PORT"; do
    local holder; holder="$(listener "$port")"
    if [[ -n "$holder" ]]; then echo "port $port already held by PID $holder — not starting, not killing it" >&2; exit 1; fi
  done
  nohup "$BIN" --dev --base-path "$DATA_DIR" --state-pruning archive --blocks-pruning archive \
    --rpc-port "$RPC_PORT" --port "$P2P_PORT" --no-prometheus --no-telemetry >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  for _ in $(seq 1 60); do
    if [[ "$(listener "$RPC_PORT")" == "$(cat "$PID_FILE")" ]]; then
      echo "interim-sdk up: PID $(cat "$PID_FILE") ws://127.0.0.1:$RPC_PORT (stand-in, not V3)"; return 0
    fi
    sleep 1
  done
  echo "node did not open port $RPC_PORT within 60 s; see $LOG_FILE" >&2; exit 1
}

stop() {
  [[ -f "$PID_FILE" ]] || { echo "no PID file; nothing to stop"; return 0; }
  local pid; pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  fi
  rm -f "$PID_FILE"
  echo "interim-sdk stopped (PID $pid); archive data kept in $DATA_DIR"
}

status() {
  local pid=""; [[ -f "$PID_FILE" ]] && pid="$(cat "$PID_FILE")"
  echo "pid file: ${pid:-none}  rpc $RPC_PORT: $(listener "$RPC_PORT")  p2p $P2P_PORT: $(listener "$P2P_PORT")"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
