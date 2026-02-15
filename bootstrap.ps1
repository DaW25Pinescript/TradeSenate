$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== TradeSenate bootstrap =="

# Prefer python, fallback to py
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) { throw "Python not found. Install Python and ensure it's on PATH." }

# Generate a demo debate.json (non-fatal if it fails)
try {
  & $py.Path ".\STATE\debate_writer.py"
  Write-Host "Generated STATE\debate.json"
} catch {
  Write-Warning "Could not generate debate.json (continuing): $($_.Exception.Message)"
}

Write-Host "Serving http://localhost:8000/UI/debate.html"
& $py.Path -m http.server 8000
