#!/usr/bin/env bash
# run_app.sh  —  Launch Locus as a native macOS menu-bar overlay.
# This is called by the .app bundle in ~/Applications.
# It activates the venv, starts the dashboard server,
# then opens the UI in a floating WebKit panel via Python.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
if ! command -v python3 >/dev/null 2>&1; then
    bash "$DIR/scripts/bootstrap_python_macos.sh"
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    hash -r
fi
PYTHON3="$(command -v python3)"
export LOCAL_COMPUTER_ALLOW_MODELS="${LOCAL_COMPUTER_ALLOW_MODELS:-0}"
export LOCAL_COMPUTER_ALLOW_EXTERNAL_AI="${LOCAL_COMPUTER_ALLOW_EXTERNAL_AI:-0}"
export LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS="${LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS:-0}"
export LOCAL_COMPUTER_SKIP_MODEL_VALIDATE="${LOCAL_COMPUTER_SKIP_MODEL_VALIDATE:-1}"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"
export LOCAL_COMPUTER_MAX_GPU_PERCENT="${LOCAL_COMPUTER_MAX_GPU_PERCENT:-95}"
export TOKENIZERS_PARALLELISM=false

# ── 1. Bootstrap venv if needed ────────────────────────────────────────────
"$PYTHON3" "$DIR/scripts/setup_manager.py" --bootstrap

eval "$(
  "$VENV/bin/python" - <<'PY' 2>/dev/null || true
from scripts.resource_policy import resource_budget
for key, value in resource_budget().env.items():
    print(f'export {key}="{value}"')
PY
)"

# ── 2. Kill any stale dashboard server ─────────────────────────────────────
lsof -ti tcp:8765 | xargs kill -9 2>/dev/null || true

# ── 3. Start dashboard WebSocket server in background ──────────────────────
"$VENV/bin/python" "$DIR/scripts/ui_server.py" --host 127.0.0.1 --port 8765 &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Wait for the server to be ready (max 5 s)
for i in $(seq 1 10); do
  nc -z 127.0.0.1 8765 2>/dev/null && break
  sleep 0.5
done

# ── 4. Open the native menu-bar overlay ────────────────────────────────────
"$VENV/bin/python" "$DIR/scripts/locus_macos_app.py"
