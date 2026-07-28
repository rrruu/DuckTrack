@echo off
setlocal

set "ROOT=%~dp0"
set "EXE=%ROOT%tauri-annotator\target\release\ducktrack-annotator.exe"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%EXE%" (
  echo [DuckTrack] Release annotator not found:
  echo %EXE%
  echo.
  echo Build it first:
  echo   cd /d "%ROOT%tauri-annotator"
  echo   npm run tauri build
  echo.
  pause
  exit /b 1
)

if exist "%VENV_PY%" (
  set "DUCKTRACK_BACKEND_PYTHON=%VENV_PY%"
)
set "DUCKTRACK_CLOUD_URL=http://118.145.32.133:20027"
set "DUCKTRACK_CLOUD_TOKEN=cff31b749e096805bf2efd84f489a65d93bedf7fc93daead1c35f668eef23488"
set "DUCKTRACK_CAPTURE_POLL_FPS=0.5"
set "DUCKTRACK_CONTINUOUS_VIDEO=0"
set "DUCKTRACK_OA_ENABLED=0"


set "DUCKTRACK_QWEN_URL="
set "DUCKTRACK_QWEN_TOKEN="
set "DUCKTRACK_QWEN_MODEL=Qwen/Qwen3.6-27B"
set "DUCKTRACK_QWEN_ENABLE_THINKING=0"
set "DUCKTRACK_QWEN_MAX_TOKENS=4096"
start "DuckTrack Annotator" "%EXE%"
exit /b 0
