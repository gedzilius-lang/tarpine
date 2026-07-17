# Patas Curve Monitor

Read-only telemetry dashboard for a pump.fun bonding curve, with Telegram alerts.

Polls the curve account over JSON-RPC, decodes the reserves, and renders live
market cap, liquidity depth, graduation progress, and trade velocity. Nothing in
this project signs, submits, or simulates a transaction — it holds no keys and
has no write path.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env    # then fill in RPC_URL
python main.py
```

You'll be prompted for a mint address (the curve PDA is derived from it), and
optionally a Telegram bot token + chat id if they aren't in `.env`. The token is
read without echo. Leave it blank to run without alerts.

## Modules

| File | Role |
|---|---|
| `telemetry.py` | Singleton state + RPC ingestion. Account decode, signature history, backoff. |
| `processor.py` | Derived metrics — price, mcap, graduation progress, drift vs 60s average. |
| `ui.py` | ANSI dashboard. Cursor-home + per-line erase, no full clear, no flicker. |
| `notifier.py` | Telegram delivery queue + edge-triggered alert engine. |
| `main.py` | Orchestrator. Thread setup, poll cadence, render loop. |

## Account layout

pump.fun `BondingCurve`, little-endian:

```
[0:8]   discriminator
[8:16]  virtual_token_reserves  u64   (6 decimals)
[16:24] virtual_sol_reserves    u64   (lamports)
[24:32] real_token_reserves     u64   (6 decimals)
[32:40] real_sol_reserves       u64   (lamports)
[40:48] token_total_supply      u64   (6 decimals)
[48]    complete                bool
[49:81] creator                 pubkey  (later program revisions)
```

Market cap reads `token_total_supply` from the account rather than assuming the
1B default, so it stays correct for non-standard mints. `unpack_from` tolerates
the trailing `creator` field.

`getAccountInfo` returns `data` as `[payload, "base64"]` — a two-element list,
not a bare string. Decoding the list directly raises `TypeError`.

## Alerts

Edge-triggered, so each fires once per transition rather than every cycle:

- graduation progress crossing 50% / 75% / 90%
- curve completion
- liquidity moves over ±15% (2 min cooldown)
- feed degraded / recovered

## Notes

- Graduation progress uses an 85 SOL target for the bar. The `complete` flag on
  the account is ground truth; the constant only drives the percentage and can
  drift if the program is redeployed with new parameters.
- Velocity counts signatures against the curve PDA, capped at 100 per lookup. A
  `+` next to the rate means the window was saturated and the true rate is higher.
- SOL/USD comes from CoinGecko's public endpoint — no key needed.
