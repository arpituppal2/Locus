#!/usr/bin/env python3
"""
LocalComputer.py - legacy one-click launcher for Locus.

Run from anywhere:
    python3 ~/Locus/LocalComputer.py

What it does (automatically, every time):
  1. Pulls latest code from GitHub
  2. Creates a venv if one doesn't exist
  3. Installs / updates dependencies
  4. Installs Playwright Chromium if missing
  5. Skips Ollama unless local model mode is explicitly enabled
  6. Starts the Locus UI server
  7. Opens http://127.0.0.1:8765 in your default browser
"""

import os
import sys
import json
import subprocess
import time
import webbrowser
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT   = Path(__file__).resolve().parent
VENV   = ROOT / ".venv"
PORT   = 8765
URL    = f"http://127.0.0.1:{PORT}"

# Python inside the venv
if sys.platform == "win32":
    VENV_PY = VENV / "Scripts" / "python.exe"
else:
    VENV_PY = VENV / "bin" / "python"


# ── Helpers ──────────────────────────────────────────────────────────────────

def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a command, printing it first, raising on failure."""
    print(f"  › {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def run_silent(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True)


def step(msg: str):
    print(f"\n{'─'*50}\n  {msg}\n{'─'*50}")


def local_models_enabled() -> bool:
    env = os.environ.get("LOCAL_COMPUTER_ALLOW_MODELS")
    if env is not None:
        return env.strip().lower() in {"1", "true", "yes", "on", "allow", "enabled"}
    try:
        runtime = json.loads((ROOT / "configs" / "runtime.json").read_text())
    except Exception:
        runtime = {}
    return bool(runtime.get("allow_local_models", False))


# ── 1. Git pull ───────────────────────────────────────────────────────────────

def git_pull():
    step("Pulling latest code…")
    result = run_silent(["git", "-C", str(ROOT), "pull"])
    if result.returncode == 0:
        out = result.stdout.decode().strip()
        print(f"  {out if out else 'Already up to date.'}")
    else:
        print("  ⚠️  git pull failed (no internet? that's ok, continuing with local code)")


# ── 2. Venv ───────────────────────────────────────────────────────────────────

def ensure_venv():
    if VENV_PY.exists():
        print(f"\n  ✓ venv already exists at {VENV}")
        return
    step("Creating virtual environment…")
    run([sys.executable, "-m", "venv", str(VENV)])
    print("  ✓ venv created")


# ── 3. Dependencies ───────────────────────────────────────────────────────────

def install_deps():
    req = ROOT / "requirements.txt"
    if not req.exists():
        print("\n  ⚠️  No requirements.txt found — skipping pip install")
        return
    step("Installing / updating dependencies…")
    run([str(VENV_PY), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(VENV_PY), "-m", "pip", "install", "--quiet", "-r", str(req)])
    print("  ✓ dependencies up to date")


# ── 4. Playwright ─────────────────────────────────────────────────────────────

def ensure_playwright():
    # Check if Chromium executable already exists
    result = run_silent([str(VENV_PY), "-m", "playwright", "install", "--dry-run"])
    # Always run install — it's a no-op if already installed, fast either way
    step("Checking Playwright Chromium…")
    run([str(VENV_PY), "-m", "playwright", "install", "chromium"])
    print("  ✓ Chromium ready")


# ── 5. Ollama ─────────────────────────────────────────────────────────────────

def ensure_ollama():
    if not local_models_enabled():
        step("Local models disabled")
        os.environ["LOCAL_COMPUTER_ALLOW_MODELS"] = "0"
        os.environ.setdefault("LOCAL_COMPUTER_SKIP_MODEL_VALIDATE", "1")
        print("  Skipping Ollama startup. Set LOCAL_COMPUTER_ALLOW_MODELS=1 to enable local inference.")
        return

    step("Checking Ollama…")
    # Is ollama installed?
    result = run_silent(["which", "ollama"])
    if result.returncode != 0:
        print("  ⚠️  Ollama not found. Install it from https://ollama.com then re-run.")
        print("      Continuing anyway — browser tasks will still work without Ollama.")
        return

    # Is it already running?
    ping = run_silent(["curl", "-s", "--max-time", "2", "http://localhost:11434/api/tags"])
    if ping.returncode == 0:
        print("  ✓ Ollama already running")
        return

    # Start it in the background
    print("  Starting Ollama server…")
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give it a moment to come up
    for _ in range(10):
        time.sleep(1)
        ping = run_silent(["curl", "-s", "--max-time", "1", "http://localhost:11434/api/tags"])
        if ping.returncode == 0:
            print("  ✓ Ollama started")
            return
    print("  ⚠️  Ollama didn't respond in time — continuing anyway")


# ── 6. UI Server ──────────────────────────────────────────────────────────────

def start_server() -> subprocess.Popen:
    step(f"Starting Locus on {URL} …")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    if not local_models_enabled():
        env["LOCAL_COMPUTER_ALLOW_MODELS"] = "0"
        env.setdefault("LOCAL_COMPUTER_SKIP_MODEL_VALIDATE", "1")
    proc = subprocess.Popen(
        [str(VENV_PY), str(ROOT / "scripts" / "ui_server.py"), "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
    )
    # Wait until the server is accepting connections
    import urllib.request, urllib.error
    for i in range(20):
        time.sleep(0.8)
        try:
            urllib.request.urlopen(f"{URL}/api/ping", timeout=2)
            print(f"  ✓ Server is up (pid {proc.pid})")
            return proc
        except Exception:
            pass
        if proc.poll() is not None:
            print("  ✗ Server crashed — check for errors above")
            sys.exit(1)
    print("  ⚠️  Server slow to start — opening browser anyway")
    return proc


# ── 7. Open browser ───────────────────────────────────────────────────────────

def open_browser():
    step(f"Opening {URL} in your browser…")
    webbrowser.open(URL)
    print("  ✓ Done! Close this terminal window to stop the server.\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═"*50)
    print("  🖥  Locus - starting up")
    print("═"*50)

    git_pull()
    ensure_venv()
    install_deps()
    ensure_playwright()
    ensure_ollama()
    server = start_server()
    open_browser()

    # Keep running until Ctrl+C
    try:
        server.wait()
    except KeyboardInterrupt:
        print("\n  Shutting down…")
        server.terminate()
        server.wait()
        print("  ✓ Stopped. Goodbye!\n")


if __name__ == "__main__":
    main()
