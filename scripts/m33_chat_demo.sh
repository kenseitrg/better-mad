#!/usr/bin/env bash
# Manual QA launcher for M3.3 — the chat sidecar (not a pytest/vitest).
#
# Boots the real hub exactly like the `better-mad` CLI (worker + sidecar +
# watcher), verifies the chat contract answers, then prints a cheat sheet of
# curl commands to drive manually. Everything under HUB/api/chat/* is proxied
# verbatim to the sidecar; the direct sidecar port is printed too so you can
# compare responses with/without the proxy in between.
#
# Usage:
#   uv run bash scripts/m33_chat_demo.sh [--port 5007] [--workspace DIR]
#                                        [--data FILE] [--model provider/model]
#
# Defaults: small 1000-row fixture (instant load) in /tmp/bm-m33-demo-<pid>;
#           pass --data data/REAL_FILE for the 121 MB sample (~13 s cold).
#
# Cleanup: Ctrl+C (or EXIT) stops hub, worker, and sidecar.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=5007
DATA=tests/fixtures/sample_ws.txt
MODEL_ARGS=()
WORKSPACE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)      PORT="$2"; shift 2 ;;
        --data)      DATA="$2"; shift 2 ;;
        --workspace) WORKSPACE="$2"; shift 2 ;;
        --model)     MODEL_ARGS=(--model "$2"); shift 2 ;;
        *) echo "unknown flag: $1 (usage: m33_chat_demo.sh [--port N] [--data FILE] [--workspace DIR] [--model provider/model])" >&2; exit 2 ;;
    esac
done
if [[ -z "$WORKSPACE" ]]; then
    WORKSPACE="/tmp/bm-m33-demo-$$"
    CLEAN_WS=1
fi

HUB="http://127.0.0.1:$PORT"
SSE_LOG="$(mktemp /tmp/bm-m33-sse-XXXXXX.log)"

cleanup() {
    echo
    echo "== stopping hub + worker + sidecar =="
    # INT the whole stack: every child carries the workspace path in its
    # cmdline (uv wrapper, python hub, worker --data-dir, node sidecar). The
    # hub's own SIGINT handling stops worker + sidecar; this is belt-and-
    # suspenders for anything orphaned.
    pkill -INT -f -- "$WORKSPACE" 2>/dev/null || true
    for _ in $(seq 1 20); do
        pgrep -f -- "$WORKSPACE" >/dev/null 2>&1 || break
        sleep 0.5
    done
    pkill -KILL -f -- "$WORKSPACE" 2>/dev/null || true
    if [[ "${CLEAN_WS:-0}" == 1 ]]; then
        rm -rf "$WORKSPACE"   # .chat session files are throwaway in the demo
    fi
    rm -f "$SSE_LOG"
}
trap cleanup EXIT INT TERM

echo "== workspace: $WORKSPACE  (data: $DATA) =="
mkdir -p "$WORKSPACE"
uv run better-mad "$DATA" --workspace "$WORKSPACE" --no-browser --port "$PORT" \
    "${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}" &
HUB_PID=$!

echo "== waiting for hub + chat sidecar (model boot can take ~10 s) =="
for _ in $(seq 1 45); do
    CHAT_STATE=$(curl -s --max-time 2 "$HUB/api/health" 2>/dev/null | python3 -c \
        'import sys,json; d=json.load(sys.stdin); print(d["chat"]["state"], d["chat"].get("port",""), d["chat"].get("model",""), d["chat"].get("reason",""))' 2>/dev/null || true)
    [[ "$CHAT_STATE" == ready* ]] && break
    kill -0 "$HUB_PID" 2>/dev/null || { echo "hub died during startup (see output above)"; exit 1; }
    sleep 2
done
read -r STATE SPORT SMODEL SREASON <<<"$CHAT_STATE"
if [[ "$STATE" != ready ]]; then
    echo "!! chat sidecar NOT ready: state=$STATE reason=$SREASON"
    echo "!! sidecar log tail: $(tail -2 "$WORKSPACE/.data/sidecar.log" 2>/dev/null || echo '(none)')"
    echo "!! (hub + worker still run — chat features degrade to 503)"
else
    echo "   chat: ready  port=$SPORT  model=$SMODEL"
    # Quick automated sanity: SSE answers through the hub proxy.
    ( curl -sN --max-time 8 "$HUB/api/chat/events" >"$SSE_LOG" 2>&1 & )
    sleep 1.5
    grep -q "^retry: 2000" "$SSE_LOG" && echo "   SSE:  retry line received through hub proxy ✔"
    curl -s "$HUB/api/chat/history" | grep -q '{"messages"' && echo "   history endpoint ✔"
fi

cat <<EOF

==================== M3.3 manual QA cheat sheet ====================
hub       : $HUB           (workspace: $WORKSPACE)
sidecar   : http://127.0.0.1:$SPORT       (direct, bypasses the proxy)
live SSE  : curl -N $HUB/api/chat/events
            (retry line + \`: ping\` heartbeat every 25 s; no replay after connect)

1. ask the agent to write a plot (watch SSE in a second terminal):
     curl -N $HUB/api/chat/events
     curl -X POST $HUB/api/chat/send -H 'content-type: application/json' \\
          -d '{"text":"Replace plot.py with a scatter of CMP vs XCORD_MIDPT from the loaded dataset, ~2000 points, using bm.show()"}'
   expect: 202 {"started":true}; SSE shows agent_start → tool_execution_start/end
   (write) → text_delta… ; plot.py changes on disk; watcher auto-runs it —
   hub SSE (curl -N $HUB/api/events) shows {"type":"run_result",...}

2. busy guard — send twice quickly:      second POST → 409
3. replay:      curl -s $HUB/api/chat/history | python3 -m json.tool
4. new chat:    curl -X POST $HUB/api/chat/new   → 202; SSE {"type":"reset"};
                history empty; old session file kept under $WORKSPACE/.chat/
5. stop mid-turn: send "count slowly to 30", then
                curl -X POST $HUB/api/chat/stop  → SSE {"type":"stopped"}
6. crash-resilience (restart-once-then-down):
     kill -9 \$(pgrep -f 'main.ts --workspace $WORKSPACE')
     sleep 6; curl -s $HUB/api/health   # chat.state=ready, restarts=1, new port
     kill -9 again within 60 s → chat.state=down + reason; /api/chat/* → 503;
     hub + worker keep running
7. model override (restart demo with):
     uv run bash scripts/m33_chat_demo.sh --model zai/glm-5.3-flash
8. sidecar stderr log: tail -f $WORKSPACE/.data/sidecar.log
====================================================================
Ctrl+C to stop everything.
EOF
wait
