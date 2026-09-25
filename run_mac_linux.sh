#!/usr/bin/env bash
# First run creates a virtual environment and installs packages; later runs just start the app.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi
echo "Starting PC Trade Scanner at http://localhost:8501  (Ctrl+C to stop)"
python -m streamlit run app.py --server.headless false
