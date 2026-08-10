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

rem Relaunch this script minimized once, so the console does not flash on double-click.
if /I not "%~1"=="__MINIMIZED__" (
  start "DuckTrackLaunch" /min cmd /c ""%~f0" __MINIMIZED__"
  exit /b 0
)

rem Kill stale local backend on port 18765 so a fresh process inherits model env vars.
powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

if exist "%VENV_PY%" (
  set "DUCKTRACK_BACKEND_PYTHON=%VENV_PY%"
)
set "DUCKTRACK_CLOUD_URL=http://118.145.32.133:20027"
set "DUCKTRACK_CLOUD_TOKEN=cff31b749e096805bf2efd84f489a65d93bedf7fc93daead1c35f668eef23488"
set "DUCKTRACK_CAPTURE_POLL_FPS=0.5"
set "DUCKTRACK_CONTINUOUS_VIDEO=0"
set "DUCKTRACK_OA_ENABLED=0"

set "DUCKTRACK_QWEN_URL=https://api.xxx.com/v1/chat/completions"
set "DUCKTRACK_QWEN_TOKEN=sk-xxx"
set "DUCKTRACK_QWEN_MODEL=Qwen/Qwen3.6-27B"
set "DUCKTRACK_QWEN_ENABLE_THINKING=0"
set "DUCKTRACK_QWEN_MAX_TOKENS=4096"
rem Feature-2 v2: VL model for sub-task split (can differ from step annotator model)
set "DUCKTRACK_SUBTASK_MODEL=Qwen/Qwen3-VL-32B-Instruct"
set "DUCKTRACK_SUBTASK_MAX_IMAGES=12"
set "DUCKTRACK_SUBTASK_VISION=1"
set "DUCKTRACK_SUBTASK_FALLBACK_TEXT=1"

start "DuckTrack" "%EXE%"
exit /b 0
