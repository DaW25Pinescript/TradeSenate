# TradeSenate bootstrap (Windows PowerShell)
# Run from the repo root:  powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1

$ErrorActionPreference = "Stop"

Write-Host "== TradeSenate bootstrap =="

# 1) Create venv if missing
if (!(Test-Path ".\.venv")) {
  py -m venv .venv
  Write-Host "Created .venv"
}

# 2) Generate demo debate.json
.\.venv\Scripts\python.exe .\STATE\debate_writer.py

Write-Host "Demo STATE\debate.json generated."

# 3) Start server
Write-Host "Starting local server: http://localhost:8000/UI/debate.html"
.\.venv\Scripts\python.exe -m http.server 8000
