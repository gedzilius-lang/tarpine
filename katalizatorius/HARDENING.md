# Patas Curve Monitor — Architecture & Hardening Roadmap

A read-only telemetry dashboard for pump.fun bonding curves, with Telegram
alerting. This document explains how the current build works and lays out a
staged path to make it production-grade — the roadmap for turning a solid
single-file tool into best-in-class infrastructure.

---

## 1. What it does today

Polls a bonding-curve account over Solana JSON-RPC, decodes the reserves, and
renders live market cap, liquidity depth, graduation progress, and trade
velocity — pushing edge-triggered notifications to Telegram on notable moves.

It **reads**. It holds no keys, signs nothing, and submits no transactions. Every
call is `getAccountInfo`, `getSignaturesForAddress`, or a public price lookup.

### Module map

| File | Responsibility | Key design choice |
|---|---|---|
| `telemetry.py` | State singleton + RPC ingestion | Success and failure paths are **separate** — a failed poll never refreshes freshness or flips status to `ONLINE`. Exponential backoff on the scanner. |
| `processor.py` | Derived metrics | Stateless per call except a 60-sample mcap ring buffer for drift. Reads `token_total_supply` from the account rather than assuming 1B. |
| `ui.py` | ANSI dashboard | Cursor-home + per-line erase (no full clear) = zero flicker. UTF-8 reconfigure so block glyphs survive a non-console stdout. |
| `notifier.py` | Telegram delivery + alert engine | Delivery runs on a worker thread behind a bounded queue, so a slow/rate-limited API can never stall the scanner or render loop. Alerts are **edge-triggered**. |
| `main.py` | Orchestrator | Owns four threads: scanner, price, telegram-sender, render. Explicit `load_dotenv()`, not an import side effect. |
| `patas-curve-monitor.cmd` | Windows launcher | Detects interpreter, checks files/deps, seeds `.env`, sets console UTF-8, launches. |

### Data contract — pump.fun `BondingCurve` account (little-endian)

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

Two facts every integrator gets wrong:
- `getAccountInfo` returns `data` as `[payload, "base64"]` — a **two-element
  list**, not a bare string. Decoding the list directly raises `TypeError`.
- Market cap must read `token_total_supply` from the account. Hardcoding 1B
  breaks silently on any non-standard mint.

---

## 2. Correctness properties already enforced

These are the failure modes the current build is explicitly designed to avoid.
They read as a checklist because they *are* one — regressions here are subtle.

- **A dead feed cannot masquerade as a live one.** Status flips to `STALE_DATA`
  after N consecutive failures; freshness age is shown on every frame.
- **Silence is not a signal.** Velocity is measured from real signature history,
  not inferred from the absence of updates.
- **Baselines don't get poisoned.** The drift average only ingests live, non-zero
  readings — a cold start or an RPC outage can't drag the SMA to zero and fake a
  swing.
- **Alerts fire once per transition**, not once per cycle above a threshold.
- **The UI degrades instead of crashing** when stdout can't encode a glyph.

---

## 3. Hardening roadmap

Ordered by leverage. Each tier is independently shippable.

### Tier 1 — Reliability (do these first)

- **Multi-endpoint RPC with failover.** Round-robin across 2–3 providers
  (Helius, Triton, QuickNode) with per-endpoint health scoring; drop an endpoint
  on repeated failure and re-probe it on a timer. Removes the single point of
  failure the current one-URL design has.
- **WebSocket `accountSubscribe` instead of polling.** Push updates on state
  change give sub-second latency and eliminate steady-state RPC spend and
  rate-limit pressure. Keep polling as a fallback when the socket drops.
- **Verify account ownership before parsing.** Confirm the account's `owner` is
  the pump.fun program id before trusting the byte layout — otherwise a wrong
  address silently decodes garbage. (Currently guarded only by length.)
- **Layout versioning.** Tag the parser by discriminator/account size so a
  program upgrade surfaces as "unknown layout vN," not corrupted numbers.
- **Slot tracking.** Carry the `context.slot` from each response and reject reads
  that arrive out of order.

### Tier 2 — Correctness & precision

- **Integer-lamports math end to end.** Compute price/mcap in base units with
  `Decimal` for display; drop float accumulation entirely.
- **Median price feed.** Aggregate CoinGecko + an on-chain oracle (Pyth) and take
  the median, so one bad quote can't move the USD column. Cache with a short TTL.
- **Batch reads.** `getMultipleAccounts` when watching several curves, to cut
  round-trips.

### Tier 3 — Observability

- **Structured logging** to a rotating file (JSON lines), not `print`.
- **Metrics endpoint.** Prometheus counters/histograms for poll latency, error
  rate, alert volume, socket reconnects. Makes "is it healthy?" a dashboard, not
  a guess.
- **Self-diagnostics panel** in the UI: p50/p99 poll latency, endpoint in use,
  consecutive-failure count.

### Tier 4 — Testing & CI

- **Recorded RPC fixtures** (VCR-style) for deterministic offline tests of the
  ingestion path.
- **Property-based tests** on the parser (round-trip arbitrary valid accounts).
- **CI**: GitHub Actions running `ruff` (lint), `mypy` (types), and the test
  suite on every push. Pin dependencies with a lockfile.

### Tier 5 — Security & secrets

- **Secrets from environment or OS keyring only** — never a committed file.
  (See the note below; this project ships `.env.example` + a `.gitignore`d
  `.env` precisely to enforce this.)
- **Pre-commit secret scanning.** A `gitleaks` / `detect-secrets` pre-commit hook
  blocks an API key from ever reaching a commit. This is the single highest-value
  addition for anyone who has been bitten by a leaked key.
- **Least-privilege, rotatable RPC keys**, with rotation documented as routine.

### Tier 6 — Distribution & UX

- **Proper packaging** — `pyproject.toml`, console entry point, optional
  `Dockerfile` for headless deployment.
- **Multi-token watchlist** with persistence to SQLite for historical charts.
- **Configurable alert thresholds** and multiple Telegram targets.
- **On-demand Telegram commands** (`/status`, `/watch <mint>`) via long-polling
  or webhook, turning the bot two-way.

---

## 4. A word on secrets

If you take one thing from this document: **never commit a credential.** The
`.gitignore` here excludes `.env`; the code reads every secret from the
environment. Add a `gitleaks` pre-commit hook (Tier 5) and the mistake becomes
impossible rather than merely discouraged. If a key ever does land in a commit,
rotating it is the only real remedy — deleting the file leaves it in history.
