#!/usr/bin/env bash
# ===================================================================
#  bacpro - Linux / macOS build script
#  Produces: dist/bacpro  (single self-contained executable)
#
#  Usage:  ./build_linux.sh
# ===================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "=== bacpro build ==="
echo

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null 2>&1 || { echo "[ERROR] $PY not found"; exit 1; }

echo "[1/4] Creating virtual environment .venv ..."
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/4] Installing dependencies ..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

echo "[3/4] Generating icon ..."
python tools/make_icon.py

echo "[4/4] Running PyInstaller ..."
pyinstaller bacpro.spec --noconfirm --clean

echo
echo "=== Build complete ==="
echo "Executable: $(pwd)/dist/bacpro"
echo
echo "Run it with:  ./dist/bacpro"
echo
