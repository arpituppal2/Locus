#!/usr/bin/env bash
set -euo pipefail

if command -v python3 >/dev/null 2>&1; then
  exit 0
fi

if [ "${LOCAL_COMPUTER_AUTO_INSTALL_PYTHON:-1}" = "0" ]; then
  echo "[python] Python 3 is required. Install Python 3.11+ and run Locus again."
  exit 1
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "[python] Automatic Python bootstrap is currently supported on macOS and Windows only."
  exit 1
fi

echo "[python] Python 3 was not found. Installing it automatically for Locus."

if ! command -v brew >/dev/null 2>&1; then
  echo "[python] Homebrew was not found. Installing Homebrew first."
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

brew update
brew install python

if ! command -v python3 >/dev/null 2>&1; then
  echo "[python] Python installation finished, but python3 is still not on PATH."
  exit 1
fi

echo "[python] Python installed: $(python3 --version)"
