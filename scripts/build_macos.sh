#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-build.txt

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onefile \
  --name D2Runner \
  main.py

echo "Build complete: dist/D2Runner"

