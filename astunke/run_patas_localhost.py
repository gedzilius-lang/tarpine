import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / 'patas_localhost_stack'
VENV_DIR = APP_DIR / '.venv'
BACKEND = APP_DIR / 'backend.py'
URL = 'http://127.0.0.1:8000'


def wait_for_port(host: str, port: int, timeout: int = 30) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def run(cmd, cwd=None):
    print('>>>', ' '.join(str(x) for x in cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def venv_python() -> Path:
    if os.name == 'nt':
        return VENV_DIR / 'Scripts' / 'python.exe'
    return VENV_DIR / 'bin' / 'python'


def main():
    if not APP_DIR.exists():
        raise SystemExit(f'Missing app directory: {APP_DIR}')
    if not BACKEND.exists():
        raise SystemExit(f'Missing backend: {BACKEND}')

    py = sys.executable
    if not VENV_DIR.exists():
        run([py, '-m', 'venv', str(VENV_DIR)], cwd=APP_DIR)

    vpy = venv_python()
    run([str(vpy), '-m', 'pip', 'install', '--upgrade', 'pip'], cwd=APP_DIR)
    run([str(vpy), '-m', 'pip', 'install', 'fastapi', 'uvicorn[standard]', 'requests', 'pydantic'], cwd=APP_DIR)

    if not (APP_DIR / '.env').exists():
        (APP_DIR / '.env').write_text('RPC_URL=\n', encoding='utf-8')

    print(f'Launching backend at {URL}')
    proc = subprocess.Popen([str(vpy), '-m', 'uvicorn', 'backend:app', '--host', '127.0.0.1', '--port', '8000'], cwd=str(APP_DIR))

    if wait_for_port('127.0.0.1', 8000, 30):
        time.sleep(1)
        webbrowser.open(URL)
        print(f'Opened {URL}')
    else:
        print('Port 8000 did not open in time. Check the terminal output.')

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == '__main__':
    main()
