"""State container and RPC ingestion for the Patas curve monitor.

Read-only: every call here is getAccountInfo / getSignaturesForAddress against
public account state. Nothing in this module signs or submits transactions.
"""

import base64
import struct
import threading
import time

import requests
from solders.pubkey import Pubkey

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_DECIMALS = 6
LAMPORTS_PER_SOL = 1_000_000_000

# Account layout, pump.fun BondingCurve:
#   [0:8]   anchor discriminator
#   [8:16]  virtual_token_reserves  u64
#   [16:24] virtual_sol_reserves    u64
#   [24:32] real_token_reserves     u64
#   [32:40] real_sol_reserves       u64
#   [40:48] token_total_supply      u64
#   [48]    complete                bool
# Later program revisions append a creator pubkey; unpack_from tolerates the
# extra trailing bytes rather than asserting an exact length.
_CURVE_STRUCT = struct.Struct("<QQQQQ")
_MIN_CURVE_LEN = 49

STATUS_OFFLINE = "OFFLINE"
STATUS_ONLINE = "ONLINE"
STATUS_STALE = "STALE_DATA"

STALE_AFTER_FAILURES = 5


def derive_curve_pda(mint: str) -> str:
    """Bonding curve PDA for a pump.fun mint."""
    seeds = [b"bonding-curve", bytes(Pubkey.from_string(mint))]
    pda, _bump = Pubkey.find_program_address(seeds, Pubkey.from_string(PUMP_PROGRAM_ID))
    return str(pda)


def parse_curve_account(raw: bytes) -> dict:
    """Decode a BondingCurve account into human units."""
    if len(raw) < _MIN_CURVE_LEN:
        raise ValueError(
            f"account is {len(raw)} bytes, expected >= {_MIN_CURVE_LEN} — "
            "is this address really a bonding curve?"
        )
    v_tok, v_sol, r_tok, r_sol, supply = _CURVE_STRUCT.unpack_from(raw, 8)
    return {
        "v_tok": v_tok / 10**TOKEN_DECIMALS,
        "v_sol": v_sol / LAMPORTS_PER_SOL,
        "r_tok": r_tok / 10**TOKEN_DECIMALS,
        "r_sol": r_sol / LAMPORTS_PER_SOL,
        "total_supply": supply / 10**TOKEN_DECIMALS,
        "complete": bool(raw[48]),
    }


class PatasTelemetry:
    """Thread-safe singleton holding the most recent observed state.

    The success and failure paths are deliberately separate: a failed poll must
    never refresh the freshness timestamp or mark the engine ONLINE.
    """

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._lock = threading.RLock()
                inst.state = {
                    "v_sol": 0.0,
                    "v_tok": 0.0,
                    "r_sol": 0.0,
                    "r_tok": 0.0,
                    "total_supply": 0.0,
                    "complete": False,
                    "sol_price": 0.0,
                    "trades_per_min": 0.0,
                    "velocity_saturated": False,
                    "last_trade_ts": None,
                    "last_poll_ok": 0.0,
                    "failure_count": 0,
                    "consecutive_failures": 0,
                    "last_error": None,
                    "status": STATUS_OFFLINE,
                }
                cls._instance = inst
        return cls._instance

    def update_curve(self, data: dict) -> None:
        """Success path only. Marks the engine live and fresh."""
        with self._lock:
            self.state.update(data)
            self.state["last_poll_ok"] = time.time()
            self.state["consecutive_failures"] = 0
            self.state["last_error"] = None
            self.state["status"] = STATUS_ONLINE

    def update_fields(self, data: dict) -> None:
        """Merge auxiliary readings without touching liveness bookkeeping."""
        with self._lock:
            self.state.update(data)

    def record_failure(self, err: str) -> None:
        with self._lock:
            self.state["failure_count"] += 1
            self.state["consecutive_failures"] += 1
            self.state["last_error"] = err
            if self.state["consecutive_failures"] >= STALE_AFTER_FAILURES:
                self.state["status"] = STATUS_STALE

    def get_state(self) -> dict:
        with self._lock:
            return dict(self.state)


class TelemetryScanner:
    """Persistent-session reader for one bonding curve account."""

    def __init__(self, rpc_url: str, pda: str):
        if not rpc_url:
            raise ValueError("RPC_URL is empty — check your .env")
        self.rpc_url = rpc_url
        self.pda = pda
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._req_id = 0
        self.backoff = 0.0

    def _rpc(self, method: str, params: list):
        self._req_id += 1
        resp = self.session.post(
            self.rpc_url,
            json={"jsonrpc": "2.0", "id": self._req_id, "method": method, "params": params},
            timeout=6,
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise RuntimeError(f"RPC {err.get('code')}: {err.get('message')}")
        return body.get("result")

    def _note_success(self) -> None:
        self.backoff = 0.0

    def _note_failure(self, telemetry: PatasTelemetry, err: Exception) -> None:
        telemetry.record_failure(f"{type(err).__name__}: {err}")
        self.backoff = min(30.0, self.backoff * 2 or 1.0)

    def poll_curve(self, telemetry: PatasTelemetry) -> bool:
        try:
            result = self._rpc(
                "getAccountInfo", [self.pda, {"encoding": "base64", "commitment": "confirmed"}]
            )
            value = (result or {}).get("value")
            if value is None:
                raise RuntimeError(f"account {self.pda[:8]}… not found on this cluster")
            # getAccountInfo returns data as [payload, encoding], not a bare string.
            raw = base64.b64decode(value["data"][0])
            telemetry.update_curve(parse_curve_account(raw))
            self._note_success()
            return True
        except Exception as exc:
            self._note_failure(telemetry, exc)
            return False

    def poll_velocity(self, telemetry: PatasTelemetry, window_s: int = 60) -> None:
        """Trade rate from real signature history, not inferred from silence."""
        limit = 100
        try:
            result = self._rpc(
                "getSignaturesForAddress",
                [self.pda, {"limit": limit, "commitment": "confirmed"}],
            )
            sigs = result or []
            stamps = [s["blockTime"] for s in sigs if s.get("blockTime")]
            now = time.time()
            recent = [t for t in stamps if now - t <= window_s]
            telemetry.update_fields(
                {
                    "trades_per_min": len(recent) * (60.0 / window_s),
                    # All 100 slots inside the window means the true rate is higher.
                    "velocity_saturated": len(sigs) >= limit and len(recent) >= limit,
                    "last_trade_ts": max(stamps) if stamps else None,
                }
            )
        except Exception as exc:
            self._note_failure(telemetry, exc)


class PriceFeed:
    """USD quote for SOL. Public endpoint, no key required."""

    URL = "https://api.coingecko.com/api/v3/simple/price"

    def __init__(self):
        self.session = requests.Session()

    def poll(self, telemetry: PatasTelemetry) -> None:
        try:
            resp = self.session.get(
                self.URL, params={"ids": "solana", "vs_currencies": "usd"}, timeout=6
            )
            resp.raise_for_status()
            telemetry.update_fields({"sol_price": float(resp.json()["solana"]["usd"])})
        except Exception:
            # A stale quote only affects the USD column; leave the last good value.
            pass
