@echo off
REM First run creates a virtual environment and installs packages; later runs just start the app.
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || (echo Python 3.11+ not found. Install it from python.org and tick "Add Python to PATH". & pause & exit /b 1)
  call .venv\Scripts\activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt || (echo Package install failed. & pause & exit /b 1)
) else (
  call .venv\Scripts\activate
)
echo Starting PC Trade Scanner at http://localhost:8501  (close this window to stop)
python -m streamlit run app.py --server.headless false
pause
