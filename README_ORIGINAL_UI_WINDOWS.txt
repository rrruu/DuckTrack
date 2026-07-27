DuckTrack Original Tauri UI + Cloud Workflow
============================================

This package restores the original DuckTrack Tauri interface as the main app.
The original recording, step list, zoom, action overlay, delete, and instruction
editing experience is retained.

New capabilities
----------------
- Low-load adaptive capture and automatic cloud upload.
- Cloud session list and Qwen annotation state.
- Download a cloud session into the local cache and open it in the original UI.
- Before, after 250ms, and settled frame switching.
- Qwen intent, result, confidence, risk, and review fields.
- Save reviewed edits to trajectory.reviewed.json on the cloud server.
- Re-run Qwen annotation from the workflow screen.
- Red drag trajectory overlay.

Windows prerequisites
---------------------
- Python 3.11+ 64-bit
- Node.js 20+
- Rust stable with Cargo
- Microsoft Edge WebView2 Runtime

Build once
----------
Open PowerShell in this directory:

  Set-ExecutionPolicy -Scope Process Bypass
  .\install_original_ui.ps1

Run
---
Double-click start_annotator.bat.

Cloud configuration is prefilled for this deployment. The package contains a
private upload token and must not be distributed publicly.
