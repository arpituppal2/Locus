#!/usr/bin/env bash
# Locus single-command launcher.
# Starts the dashboard in model-free mode unless LOCAL_COMPUTER_ALLOW_MODELS=1.
set -euo pipefail

AIDIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${LOCAL_COMPUTER_PORT:-8765}"
VENV="$AIDIR/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  bash "$AIDIR/scripts/bootstrap_python_macos.sh"
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
export PYTHONPATH="$AIDIR${PYTHONPATH:+:$PYTHONPATH}"
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"
export LOCAL_COMPUTER_MAX_GPU_PERCENT="${LOCAL_COMPUTER_MAX_GPU_PERCENT:-95}"
export TOKENIZERS_PARALLELISM=false

"$PYTHON3" "$AIDIR/scripts/setup_manager.py" --bootstrap
source "$VENV/bin/activate"

eval "$(
  python - <<'PY' 2>/dev/null || true
from scripts.resource_policy import resource_budget
for key, value in resource_budget().env.items():
    print(f'export {key}="{value}"')
PY
)"

if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
  echo "[start] Port $PORT is already in use. Stop that process or set LOCAL_COMPUTER_PORT."
  exit 1
fi

echo "[start] Starting Locus at http://127.0.0.1:$PORT"
python "$AIDIR/scripts/ui_server.py" --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$PORT/api/ping" >/dev/null 2>&1 && break || true
  sleep 0.3
done

open "http://127.0.0.1:$PORT" 2>/dev/null || true
echo "[start] Locus is running in model-free mode. Press Ctrl+C to stop."
trap "kill $SERVER_PID 2>/dev/null; exit" INT TERM
wait "$SERVER_PID"
