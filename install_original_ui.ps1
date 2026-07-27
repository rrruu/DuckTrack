$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$TauriRoot = Join-Path $Root "tauri-annotator"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ with the py launcher is required."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js 20+ is required."
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Rust stable is required. Install it from https://rustup.rs first."
}

Push-Location $Root
try {
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { & py -3 -m venv .venv }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -r requirements-unified.txt
} finally {
    Pop-Location
}

Push-Location $TauriRoot
try {
    & npm ci
    & npm run tauri build
} finally {
    Pop-Location
}

Write-Host "DuckTrack original UI build completed."
Write-Host "Run start_annotator.bat from the package root."
