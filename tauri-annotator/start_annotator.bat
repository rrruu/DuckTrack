@echo off
setlocal

set "ROOT=%~dp0"
set "EXE_DEBUG=%ROOT%target\debug\ducktrack-annotator.exe"
set "EXE_RELEASE=%ROOT%target\release\ducktrack-annotator.exe"
set "VENV_PY=%ROOT%..\.venv\Scripts\python.exe"

set "EXE=%EXE_RELEASE%"
if not exist "%EXE%" (
  set "EXE=%EXE_DEBUG%"
)

if not exist "%EXE%" (
  echo [DuckTrack] Annotator exe not found:
  echo %EXE_DEBUG%
  echo %EXE_RELEASE%
  echo.
  echo Build first:
  echo   cd /d "%ROOT%"
  echo   npm run tauri build
  echo   or
  echo   npm run tauri build -- --debug
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

start "DuckTrack Annotator" "%EXE%"
exit /b 0
