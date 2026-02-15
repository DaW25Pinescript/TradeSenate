# TradeSenate (v1)

A minimal **Python + HTML** prototype for a multi-agent “Trade Senate” decision panel.

## What’s in this repo

- `UI/debate.html` — single-file UI that loads `STATE/debate.json` (falls back to demo if missing)
- `STATE/debate_writer.py` — writes `STATE/debate.json` using an atomic write (Windows-safe)
- `STATE/canonical_state_v1.py` — minimal canonical-state builder scaffold (extend later)

## Quickstart (Windows)

1) Create a virtual env (optional):
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2) Generate a demo debate file:
```powershell
py .\STATE\debate_writer.py
```

3) Run a local web server from the repo root:
```powershell
py -m http.server 8000
```

4) Open in browser:
- http://localhost:8000/UI/debate.html

## Notes

- `STATE/debate.json` is intentionally **gitignored** (it’s a live feed file).
- Later we’ll add:
  - schema validation
  - replay snapshots in `REPLAYS/`
  - real agent runner (Magnus + specialist agents) that consumes canonical state
