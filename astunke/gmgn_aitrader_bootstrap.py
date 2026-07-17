import os
import sys
import time
import shutil
import socket
import subprocess
from pathlib import Path

REPO_URL = "https://github.com/GMGNAI/skillmarket-demos.git"
TARGET_DIR = Path.home() / "gmgn-demos"
APP_DIR = TARGET_DIR / "aitrader"
APP_URL = "http://127.0.0.1:8000"


def run(cmd, cwd=None):
    print(f"\n>>> {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def wait_for_port(host: str, port: int, timeout: int = 30) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def open_browser(url: str):
    ps = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"Start-Process '{url}'"
    ]
    try:
        subprocess.Popen(ps)
    except Exception:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass


def main():
    print("GMGN AITrader bootstrap starting...")
    print(f"Repo: {REPO_URL}")
    print(f"Target: {TARGET_DIR}")

    git = shutil.which("git")
    pip = shutil.which("pip") or shutil.which("pip3")
    py = sys.executable

    if not git:
        raise SystemExit("git not found in PATH. Install Git first.")
    if not pip:
        raise SystemExit("pip not found in PATH. Install Python/pip first.")

    if TARGET_DIR.exists():
        print(f"Removing existing directory: {TARGET_DIR}")
        shutil.rmtree(TARGET_DIR)

    run([git, "clone", "--depth=1", REPO_URL, str(TARGET_DIR)])

    if not APP_DIR.exists():
        raise SystemExit(f"Expected app directory not found: {APP_DIR}")

    req = APP_DIR / "requirements.txt"
    app = APP_DIR / "app.py"
    if not req.exists():
        raise SystemExit(f"Missing requirements file: {req}")
    if not app.exists():
        raise SystemExit(f"Missing app file: {app}")

    run([pip, "install", "-r", str(req)], cwd=APP_DIR)

    print("Starting browser opener...")
    opener = subprocess.Popen([py, "-c", f"import time,webbrowser; time.sleep(2); webbrowser.open('{APP_URL}')"])

    print(f"Launching app at {APP_URL}")
    app_proc = subprocess.Popen([py, "app.py"], cwd=str(APP_DIR))

    if wait_for_port("127.0.0.1", 8000, timeout=30):
        print(f"Server is reachable at {APP_URL}")
    else:
        print("Server did not confirm on port 8000 within 30s. Check terminal logs.")

    try:
        app_proc.wait()
    finally:
        if opener and opener.poll() is None:
            opener.terminate()


if __name__ == "__main__":
    main()
