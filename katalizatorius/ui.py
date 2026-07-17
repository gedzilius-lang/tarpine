"""Zero-flicker ANSI dashboard.

Cursor-home + per-line erase rather than a full clear, so the frame is
overwritten in place instead of blanking between repaints.
"""

import sys

from colorama import Fore, Style, init

init(autoreset=True)

# Windows falls back to cp1252 when stdout isn't a console (piped, redirected,
# some IDE terminals), which can't encode the block-drawing characters below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = "\033[H"
CLEAR_LINE = "\033[K"
CLEAR_BELOW = "\033[J"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

WIDTH = 62


def _bar(fraction: float, width: int = 24) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = int(fraction * width)
    return "█" * filled + "░" * (width - filled)


def _age(seconds) -> str:
    if seconds is None:
        return "never"
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m ago"
    return f"{seconds / 3600:.1f}h ago"


class DashboardUI:
    @staticmethod
    def enter() -> None:
        sys.stdout.write(HIDE_CURSOR)
        sys.stdout.flush()

    @staticmethod
    def leave() -> None:
        sys.stdout.write(SHOW_CURSOR + "\n")
        sys.stdout.flush()

    @staticmethod
    def render(state: dict, m: dict, ctx: dict) -> None:
        out = [HOME]

        def line(text: str = "") -> None:
            out.append(text + CLEAR_LINE + "\n")

        status = state.get("status", "OFFLINE")
        if status == "ONLINE" and not m["stale"]:
            status_str = f"{Fore.GREEN}ONLINE{Style.RESET_ALL}"
        elif status == "OFFLINE":
            status_str = f"{Fore.YELLOW}CONNECTING{Style.RESET_ALL}"
        else:
            status_str = f"{Fore.RED}{status}{Style.RESET_ALL}"

        line(f"{Fore.CYAN}{'=' * WIDTH}")
        line(f"{Fore.CYAN}  PATAS CURVE MONITOR {Style.DIM}(read-only){Style.RESET_ALL}")
        line(f"{Fore.CYAN}{'=' * WIDTH}")
        line(f"  {Style.DIM}curve{Style.RESET_ALL}  {ctx['pda']}")
        line(f"  {Style.DIM}status{Style.RESET_ALL} {status_str}   "
             f"{Style.DIM}updated{Style.RESET_ALL} {_age(m['data_age'])}   "
             f"{Style.DIM}fails{Style.RESET_ALL} {state.get('failure_count', 0)}")
        line()

        if m["graduated"]:
            line(f"  {Fore.MAGENTA}◆ GRADUATED — curve complete, liquidity moved to Raydium")
            line()

        line(f"{Fore.WHITE}  MARKET CAP   {Fore.GREEN}${m['mcap_usd']:>14,.2f}{Style.RESET_ALL}"
             f"   {Style.DIM}({m['mcap_sol']:.2f} SOL){Style.RESET_ALL}")
        drift = m["mcap_drift_pct"]
        drift_col = Fore.GREEN if drift >= 0 else Fore.RED
        line(f"{Fore.WHITE}  vs 60s avg   {drift_col}{drift:>+14.2f}%{Style.RESET_ALL}"
             f"   {Style.DIM}(avg ${m['mcap_sma']:,.0f}){Style.RESET_ALL}")
        line(f"{Fore.WHITE}  PRICE        {Fore.CYAN}${m['price_usd']:>14.8f}{Style.RESET_ALL}"
             f"   {Style.DIM}({m['price_sol']:.10f} SOL){Style.RESET_ALL}")
        line()

        line(f"{Fore.WHITE}  LIQUIDITY    {Fore.YELLOW}{m['liquidity_sol']:>13.4f} SOL{Style.RESET_ALL}"
             f"  {Style.DIM}(${m['liquidity_usd']:,.0f}){Style.RESET_ALL}")
        line(f"{Fore.WHITE}  TOKENS LEFT  {Fore.YELLOW}{m['tokens_left']:>17,.0f}{Style.RESET_ALL}")
        line()

        pct = m["graduation_pct"]
        line(f"{Fore.WHITE}  GRADUATION   [{Fore.CYAN}{_bar(pct / 100.0)}{Fore.WHITE}] {pct:5.1f}%")
        if not m["graduated"]:
            line(f"               {Style.DIM}{m['sol_to_graduate']:.2f} SOL remaining{Style.RESET_ALL}")
        line()

        tpm = m["trades_per_min"]
        sat = " +" if m["velocity_saturated"] else ""
        line(f"{Fore.WHITE}  VELOCITY     {Fore.CYAN}{tpm:>10.1f}{sat} tx/min{Style.RESET_ALL}"
             f"   {Style.DIM}last {_age(m['seconds_since_trade'])}{Style.RESET_ALL}")
        line()

        tg = ctx.get("telegram")
        if tg:
            tg_col = Fore.GREEN if tg["ok"] else Fore.RED
            line(f"  {Style.DIM}telegram{Style.RESET_ALL} {tg_col}{tg['label']}{Style.RESET_ALL}"
                 f"   {Style.DIM}sent {tg['sent']}{Style.RESET_ALL}")

        err = state.get("last_error")
        if err:
            line(f"  {Fore.RED}last error{Style.RESET_ALL} {Style.DIM}{err[:WIDTH - 14]}")

        line(f"{Fore.CYAN}{'=' * WIDTH}")
        line(f"  {Style.DIM}Ctrl+C to exit{Style.RESET_ALL}")
        out.append(CLEAR_BELOW)

        sys.stdout.write("".join(out))
        sys.stdout.flush()
