#!/usr/bin/env bash
# run_app.sh  —  Launch Locus as a native macOS window.
# This is called by the .app bundle in your Dock.
# It activates the venv, starts the dashboard server,
# then opens the UI in a dedicated frameless WebKit window via Python.
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

# ── 4. Open the dashboard in a native macOS WebKit window ──────────────────
# Uses PyObjC (ships with macOS Python) to create a frameless WKWebView app.
"$VENV/bin/python" - <<'PYAPP'
import sys, os, threading, time

try:
    import AppKit
    import WebKit
    import objc
except ImportError:
    # PyObjC not in venv — fall back to opening in the default browser
    import webbrowser
    webbrowser.open("http://localhost:8765")
    # Keep the server alive
    import time
    while True:
        time.sleep(60)

import AppKit, WebKit, objc
from Foundation import NSURL, NSURLRequest

DASH_URL = "http://localhost:8765"

class AppDelegate(AppKit.NSObject):
    def applicationDidFinishLaunching_(self, note):
        # Window
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable
        )
        rect = AppKit.NSMakeRect(100, 100, 1280, 820)
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style,
            AppKit.NSBackingStoreBuffered, False
        )
        win.setTitle_("Locus")
        win.setMinSize_(AppKit.NSMakeSize(800, 500))

        # WKWebView
        cfg = WebKit.WKWebViewConfiguration.alloc().init()
        wv = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            win.contentView().bounds(), cfg
        )
        wv.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        win.contentView().addSubview_(wv)

        url  = NSURL.URLWithString_(DASH_URL)
        req  = NSURLRequest.requestWithURL_(url)
        wv.loadRequest_(req)

        win.makeKeyAndOrderFront_(None)
        self._win = win
        self._wv  = wv
        self._monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown,
            self.handleKeyEvent_
        )

    def handleKeyEvent_(self, event):
        # Option+Space focuses Locus while the app is active. A fully global
        # shortcut requires macOS Accessibility approval and a signed bundle.
        if event.keyCode() == 49 and (event.modifierFlags() & AppKit.NSEventModifierFlagOption):
            self._win.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            self._wv.evaluateJavaScript_completionHandler_(
                "if (window.openLocusCommandPalette) { window.openLocusCommandPalette(document.querySelector('#queryInput')?.value || ''); } else { document.querySelector('#queryInput')?.focus(); document.querySelector('#queryInput')?.select(); }",
                None
            )
            return None
        return event

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

app  = AppKit.NSApplication.sharedApplication()
del_ = AppDelegate.alloc().init()
app.setDelegate_(del_)
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
app.activateIgnoringOtherApps_(True)
app.run()
PYAPP
