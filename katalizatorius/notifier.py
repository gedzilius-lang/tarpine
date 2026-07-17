"""Telegram alerting.

Sends run on a worker thread behind a queue so a slow or rate-limited Telegram
API can never stall the scanner or the render loop. Alerts are edge-triggered:
each fires on a state transition, not on every cycle that satisfies it.
"""

import html
import queue
import threading
import time

import requests

LIQUIDITY_MOVE_PCT = 15.0
LIQUIDITY_ALERT_COOLDOWN_S = 120.0
GRADUATION_MARKS = (50.0, 75.0, 90.0)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat_id = chat_id
        self._session = requests.Session()
        self._queue = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self.sent = 0
        self.ok = False
        self.label = "unverified"

    def verify(self) -> bool:
        """Confirm the token works and the chat is reachable before the UI starts."""
        try:
            resp = self._session.get(f"{self._base}/getMe", timeout=8)
            body = resp.json()
            if not body.get("ok"):
                self.label = f"bad token ({body.get('description', 'rejected')})"
                return False
            self.label = "@" + body["result"].get("username", "bot")
            self.ok = True
            return True
        except Exception as exc:
            self.label = f"unreachable ({type(exc).__name__})"
            return False

    def start(self) -> None:
        threading.Thread(target=self._worker, daemon=True, name="tg-sender").start()

    def send(self, text: str) -> None:
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            pass

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                text = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._deliver(text)

    def _deliver(self, text: str) -> None:
        for _ in range(3):
            try:
                resp = self._session.post(
                    f"{self._base}/sendMessage",
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=10,
                )
                if resp.status_code == 429:
                    wait = resp.json().get("parameters", {}).get("retry_after", 3)
                    time.sleep(min(wait, 30))
                    continue
                body = resp.json()
                if body.get("ok"):
                    self.sent += 1
                    self.ok = True
                else:
                    self.ok = False
                    self.label = str(body.get("description", "send failed"))[:32]
                return
            except Exception as exc:
                self.ok = False
                self.label = f"send error ({type(exc).__name__})"
                time.sleep(2)

    def status(self) -> dict:
        return {"ok": self.ok, "label": self.label, "sent": self.sent}


class AlertEngine:
    """Watches metric transitions and pushes notifications for notable ones."""

    def __init__(self, notifier: TelegramNotifier, pda: str, mint: str = None):
        self._tg = notifier
        self._pda = pda
        self._mint = mint
        self._fired = set()
        self._last_liq = None
        self._last_liq_alert = 0.0
        self._was_healthy = True

    def _link(self) -> str:
        if self._mint:
            return f'\n<a href="https://pump.fun/{self._mint}">pump.fun</a>'
        return ""

    def announce_start(self, m: dict) -> None:
        target = self._mint or self._pda
        self._tg.send(
            "<b>Patas monitor started</b>\n"
            f"<code>{html.escape(target)}</code>\n"
            f"MCAP ${m['mcap_usd']:,.0f} · LIQ {m['liquidity_sol']:.3f} SOL · "
            f"{m['graduation_pct']:.1f}% to graduation" + self._link()
        )

    def evaluate(self, state: dict, m: dict) -> None:
        if m["stale"]:
            # Don't alert on numbers we no longer trust.
            self._health_check(state, m)
            return
        self._health_check(state, m)
        self._graduation_check(m)
        self._liquidity_check(m)

    def _health_check(self, state: dict, m: dict) -> None:
        healthy = not m["stale"] and state.get("status") == "ONLINE"
        if not healthy and self._was_healthy:
            err = state.get("last_error") or "no recent successful poll"
            self._tg.send(f"⚠️ <b>Feed degraded</b>\n<code>{html.escape(str(err))[:180]}</code>")
        elif healthy and not self._was_healthy:
            self._tg.send("✅ <b>Feed recovered</b>")
        self._was_healthy = healthy

    def _graduation_check(self, m: dict) -> None:
        if m["graduated"]:
            if "graduated" not in self._fired:
                self._fired.add("graduated")
                self._tg.send(
                    "🎓 <b>GRADUATED</b>\n"
                    f"Final MCAP ${m['mcap_usd']:,.0f}\n"
                    "Curve complete — liquidity migrating to Raydium." + self._link()
                )
            return
        for mark in GRADUATION_MARKS:
            key = f"grad-{mark}"
            if m["graduation_pct"] >= mark and key not in self._fired:
                self._fired.add(key)
                self._tg.send(
                    f"📈 <b>{mark:.0f}% to graduation</b>\n"
                    f"LIQ {m['liquidity_sol']:.2f} SOL · "
                    f"{m['sol_to_graduate']:.2f} SOL remaining\n"
                    f"MCAP ${m['mcap_usd']:,.0f}" + self._link()
                )

    def _liquidity_check(self, m: dict) -> None:
        liq = m["liquidity_sol"]
        if self._last_liq is None or self._last_liq <= 0:
            self._last_liq = liq
            return
        change = ((liq / self._last_liq) - 1.0) * 100.0
        if abs(change) < LIQUIDITY_MOVE_PCT:
            return
        now = time.time()
        if now - self._last_liq_alert < LIQUIDITY_ALERT_COOLDOWN_S:
            return
        self._last_liq_alert = now
        arrow = "🟢" if change > 0 else "🔴"
        self._tg.send(
            f"{arrow} <b>Liquidity {change:+.1f}%</b>\n"
            f"{self._last_liq:.3f} → {liq:.3f} SOL\n"
            f"MCAP ${m['mcap_usd']:,.0f} · {m['trades_per_min']:.0f} tx/min" + self._link()
        )
        self._last_liq = liq
