"""Patas curve monitor — orchestrator.

Threads:
  scanner  — curve account every 2s, signature history every 6s
  price    — SOL/USD every 60s
  tg-sender— telegram delivery queue (owned by notifier)
  main     — processor + render at 4Hz
"""

import getpass
import os
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()  # explicit here, not as an import side effect in another module

from notifier import AlertEngine, TelegramNotifier  # noqa: E402
from processor import LogicProcessor  # noqa: E402
from telemetry import PatasTelemetry, PriceFeed, TelemetryScanner, derive_curve_pda  # noqa: E402
from ui import DashboardUI  # noqa: E402

CURVE_INTERVAL = 2.0
VELOCITY_EVERY = 3  # curve polls per velocity poll -> ~6s
PRICE_INTERVAL = 60.0
RENDER_INTERVAL = 0.25


def resolve_target() -> tuple:
    """Return (pda, mint_or_None) from env, else prompt for a mint."""
    pda = os.getenv("PDA_ADDRESS", "").strip()
    if pda:
        return pda, None
    mint = input("Mint address: ").strip()
    if not mint:
        sys.exit("No mint given.")
    try:
        return derive_curve_pda(mint), mint
    except Exception as exc:
        sys.exit(f"Could not derive curve PDA from that mint: {exc}")


def resolve_telegram() -> tuple:
    """Token/chat id from env, else prompt. Token is read without echo."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token:
        token = getpass.getpass("Telegram bot token (hidden, blank to skip): ").strip()
    if not token:
        return None, None
    if not chat_id:
        chat_id = input("Telegram chat id: ").strip()
    if not chat_id:
        print("No chat id — continuing without alerts.")
        return None, None
    return token, chat_id


def main() -> None:
    rpc_url = os.getenv("RPC_URL", "").strip()
    if not rpc_url:
        sys.exit("RPC_URL missing. Copy .env.example to .env and fill it in.")

    pda, mint = resolve_target()
    token, chat_id = resolve_telegram()

    telemetry = PatasTelemetry()
    scanner = TelemetryScanner(rpc_url, pda)
    processor = LogicProcessor()
    price_feed = PriceFeed()

    notifier = None
    alerts = None
    if token:
        notifier = TelegramNotifier(token, chat_id)
        print("Verifying Telegram…")
        if notifier.verify():
            notifier.start()
            alerts = AlertEngine(notifier, pda, mint)
            print(f"  connected as {notifier.label}")
        else:
            print(f"  {notifier.label} — continuing without alerts.")
            notifier = None

    print(f"Watching curve {pda}")
    print("Priming…")
    if not scanner.poll_curve(telemetry):
        state = telemetry.get_state()
        sys.exit(f"First poll failed: {state['last_error']}")
    scanner.poll_velocity(telemetry)
    price_feed.poll(telemetry)

    stop = threading.Event()

    def scanner_loop() -> None:
        tick = 0
        while not stop.is_set():
            scanner.poll_curve(telemetry)
            if tick % VELOCITY_EVERY == 0:
                scanner.poll_velocity(telemetry)
            tick += 1
            stop.wait(CURVE_INTERVAL + scanner.backoff)

    def price_loop() -> None:
        while not stop.is_set():
            stop.wait(PRICE_INTERVAL)
            if not stop.is_set():
                price_feed.poll(telemetry)

    threading.Thread(target=scanner_loop, daemon=True, name="scanner").start()
    threading.Thread(target=price_loop, daemon=True, name="price").start()

    if alerts:
        alerts.announce_start(processor.compute(telemetry.get_state()))

    os.system("cls" if os.name == "nt" else "clear")
    DashboardUI.enter()
    ctx = {"pda": pda, "telegram": None}
    try:
        while True:
            state = telemetry.get_state()
            metrics = processor.compute(state)
            if alerts:
                alerts.evaluate(state, metrics)
                ctx["telegram"] = notifier.status()
            DashboardUI.render(state, metrics, ctx)
            time.sleep(RENDER_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        DashboardUI.leave()
        print("Stopped.")


if __name__ == "__main__":
    main()
