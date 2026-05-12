#!/usr/bin/env bash
set -euo pipefail

AIDIR="$(cd "$(dirname "$0")" && pwd)"

ensure_python3() {
  if command -v python3 >/dev/null 2>&1; then
    return
  fi
  if [ "$(uname -s)" = "Darwin" ]; then
    bash "$AIDIR/scripts/bootstrap_python_macos.sh"
    if [ -x /opt/homebrew/bin/brew ]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
      eval "$(/usr/local/bin/brew shellenv)"
    fi
    hash -r
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11+ is required. Install Python and run Locus again."
    exit 1
  fi
}

ensure_python3
PYTHON3="$(command -v python3)"

ALLOW_MODELS="${LOCAL_COMPUTER_ALLOW_MODELS:-0}"
while [ "$#" -gt 0 ]; do
  case "${1:-}" in
    --allow-models)
      ALLOW_MODELS=1
      export LOCAL_COMPUTER_ALLOW_MODELS=1
      shift
      ;;
    --max-ram-gb)
      if [ -z "${2:-}" ]; then
        echo "--max-ram-gb requires a value"
        exit 1
      fi
      export LOCAL_COMPUTER_MAX_RAM_GB="$2"
      shift 2
      ;;
    --no-auto-select-models)
      export LOCAL_COMPUTER_AUTO_SELECT_MODELS=0
      shift
      ;;
    --allow-external-ai)
      export LOCAL_COMPUTER_ALLOW_EXTERNAL_AI=1
      shift
      ;;
    --allow-cloud-workers)
      export LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

export OLLAMA_NUM_PARALLEL=1
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-5m}"
export TOKENIZERS_PARALLELISM=false
export LOCAL_COMPUTER_MAX_GPU_PERCENT="${LOCAL_COMPUTER_MAX_GPU_PERCENT:-95}"
export LOCAL_COMPUTER_ALLOW_EXTERNAL_AI="${LOCAL_COMPUTER_ALLOW_EXTERNAL_AI:-0}"
export LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS="${LOCAL_COMPUTER_ALLOW_CLOUD_WORKERS:-0}"
export LOCAL_COMPUTER_SKIP_MODEL_VALIDATE="${LOCAL_COMPUTER_SKIP_MODEL_VALIDATE:-1}"
export PYTHONPATH="$AIDIR${PYTHONPATH:+:$PYTHONPATH}"

if [ -n "$PYTHON3" ]; then
  eval "$(
    PYTHONPATH="$AIDIR${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON3" - <<'PY' 2>/dev/null || true
from scripts.resource_policy import resource_budget
budget = resource_budget()
for key, value in budget.env.items():
    print(f'export {key}="{value}"')
PY
  )"
fi

if [ "$ALLOW_MODELS" = "1" ]; then
  export LOCAL_COMPUTER_SKIP_MODEL_VALIDATE=0
  if ! command -v ollama >/dev/null 2>&1; then
    echo "Install Ollama: https://ollama.ai"
    exit 1
  fi

  if ! OLLAMA_LIST="$(ollama list 2>/dev/null)"; then
    echo "Ollama is not running. Start it with: ollama serve"
    exit 1
  fi
  echo "[models] Hardware-aware recommendation:"
  if [ -n "${LOCAL_COMPUTER_MAX_RAM_GB:-}" ]; then
    "$PYTHON3" "$AIDIR/scripts/model_selector.py" --max-ram-gb "$LOCAL_COMPUTER_MAX_RAM_GB" || true
  else
    "$PYTHON3" "$AIDIR/scripts/model_selector.py" || true
  fi
else
  export LOCAL_COMPUTER_ALLOW_MODELS=0
fi



if lsof -ti:8765 >/dev/null 2>&1; then
  echo "Kill existing server: lsof -ti:8765 | xargs kill"
  exit 1
fi

VENV="$AIDIR/.venv"
"$PYTHON3" "$AIDIR/scripts/setup_manager.py" --bootstrap

source "$VENV/bin/activate"

cd "$AIDIR"

if [ "$#" -gt 0 ]; then
  if [ "$ALLOW_MODELS" = "1" ]; then
    echo "[run] Running one-shot research query with local models enabled"
    python scripts/orchestrator.py "$@"
  else
    echo "[run] Running model-free workspace query"
    python scripts/workspace_agent.py "$@"
  fi
else
  echo "[run] Starting dashboard server at http://127.0.0.1:8765"
  python scripts/ui_server.py --host 127.0.0.1 --port 8765
fi
