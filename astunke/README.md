# Patas Localhost Stack

Localhost-only educational dashboard adapted from the Tarpine / Mayhem terminal ideas.

## What is included

- `backend.py` — FastAPI backend that derives the pump.fun bonding-curve PDA, polls Helius RPC, decodes curve bytes, computes tension and T1/T2 sizing, and exposes a localhost API.
- `web/index.html` — clean browser dashboard for localhost use.
- `run_patas_localhost.py` — one-click bootstrap for PowerShell or local terminal.
- `.env` — put your `RPC_URL=` here.

## Run

```powershell
python .\run_patas_localhost.py
```

## Notes

- Execution buttons are currently a safe dry-run stub.
- Demo mode works without RPC.
- Live mode requires a valid Solana RPC URL, ideally Helius.
