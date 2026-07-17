"""Derived curve metrics.

Descriptive only — this module reports what the curve is doing. It does not
emit trade signals or position sizes.
"""

import collections
import time

# Standard pump.fun curve completes near this much real SOL raised. The
# `complete` flag on the account is ground truth; this constant only drives the
# progress bar and can drift if the program is redeployed with new params.
GRADUATION_SOL = 85.0

STALE_SECONDS = 15.0


class LogicProcessor:
    """Turns raw curve state into display metrics. One instance, called per cycle."""

    def __init__(self, sma_window: int = 60):
        self._mcap_history = collections.deque(maxlen=sma_window)

    def compute(self, state: dict) -> dict:
        v_sol = state.get("v_sol", 0.0)
        v_tok = state.get("v_tok", 0.0)
        supply = state.get("total_supply", 0.0)
        sol_price = state.get("sol_price", 0.0)

        price_sol = (v_sol / v_tok) if v_tok > 0 else 0.0
        mcap_sol = price_sol * supply
        mcap_usd = mcap_sol * sol_price

        # Only feed the average from live readings; zeros from a cold start or a
        # dead RPC would drag the baseline down and fake a drift reading.
        if mcap_usd > 0:
            self._mcap_history.append(mcap_usd)
        sma = (sum(self._mcap_history) / len(self._mcap_history)) if self._mcap_history else 0.0
        drift_pct = ((mcap_usd / sma) - 1.0) * 100.0 if sma > 0 else 0.0

        graduated = bool(state.get("complete", False))
        r_sol = state.get("r_sol", 0.0)
        progress = 1.0 if graduated else min(1.0, r_sol / GRADUATION_SOL if GRADUATION_SOL else 0.0)

        last_ok = state.get("last_poll_ok", 0.0)
        age = (time.time() - last_ok) if last_ok else None
        stale = age is None or age > STALE_SECONDS

        last_trade = state.get("last_trade_ts")
        return {
            "price_sol": price_sol,
            "price_usd": price_sol * sol_price,
            "mcap_sol": mcap_sol,
            "mcap_usd": mcap_usd,
            "mcap_sma": sma,
            "mcap_drift_pct": drift_pct,
            "liquidity_sol": r_sol,
            "liquidity_usd": r_sol * sol_price,
            "tokens_left": state.get("r_tok", 0.0),
            "graduation_pct": progress * 100.0,
            "graduated": graduated,
            "sol_to_graduate": max(0.0, GRADUATION_SOL - r_sol) if not graduated else 0.0,
            "trades_per_min": state.get("trades_per_min", 0.0),
            "velocity_saturated": state.get("velocity_saturated", False),
            "seconds_since_trade": (time.time() - last_trade) if last_trade else None,
            "data_age": age,
            "stale": stale,
        }
