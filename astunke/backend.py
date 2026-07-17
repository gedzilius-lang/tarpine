import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_CURVE_DISCRIMINATOR = "17b7f83760d8ac60"
B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ED25519_P = 2**255 - 19
TOKEN_DECIMALS = 6
SOL_DECIMALS = 9
DEFAULT_SOL_USD = 155.0
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / '.env'
CONFIG_PATH = SCRIPT_DIR / 'config.json'
WEB_DIR = SCRIPT_DIR / 'web'

app = FastAPI(title='Patas Localhost Stack', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

state_lock = threading.Lock()
state: Dict[str, Any] = {
    'mint': None,
    'curve': None,
    'name': None,
    'symbol': None,
    'v_sol': 30.0,
    'v_tok': 1_000_000_000.0,
    'r_sol': 0.0,
    'sol_price': DEFAULT_SOL_USD,
    'failure_count': 0,
    'last_activity': time.time(),
    'last_poll': None,
    'graduated': False,
    'note': None,
    'trade_history': [],
    'mode': 'idle',
    'execution_log': [],
}
runtime = {
    'poll_thread': None,
    'stop_event': None,
}
requests_session = requests.Session()
requests_session.headers.update({'Content-Type': 'application/json', 'User-Agent': 'PatasLocalhost/0.1'})


def load_env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip()
    for k, v in os.environ.items():
        out.setdefault(k, v)
    return out


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def helius_rpc_url() -> Optional[str]:
    env = load_env()
    if env.get('RPC_URL'):
        return env['RPC_URL']
    cfg = load_config()
    net = cfg.get('Network', {}) if isinstance(cfg, dict) else {}
    api_key = net.get('HeliusApiKey')
    base = net.get('HeliusRpcBaseUrl', 'https://mainnet.helius-rpc.com')
    if api_key and not str(api_key).startswith('PASTE'):
        return f"{base.rstrip('/')}?api-key={api_key}"
    return None


def b58decode(text: str) -> bytes:
    num = 0
    for ch in text:
        idx = B58_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError(f'invalid base58 character {ch!r}')
        num = num * 58 + idx
    body = num.to_bytes((num.bit_length() + 7) // 8, 'big') if num else b''
    nlead = 0
    for ch in text:
        if ch == '1':
            nlead += 1
        else:
            break
    return b'\x00' * nlead + body


def b58encode(raw: bytes) -> str:
    num = int.from_bytes(raw, 'big')
    out = ''
    while num > 0:
        num, rem = divmod(num, 58)
        out = B58_ALPHABET[rem] + out
    for byte in raw:
        if byte == 0:
            out = '1' + out
        else:
            break
    return out or '1'


def is_on_curve(point: bytes) -> bool:
    if len(point) != 32:
        return False
    p = ED25519_P
    sign = point[31] >> 7 & 1
    y = int.from_bytes(point, 'little') & ((1 << 255) - 1)
    if y >= p:
        return False
    d = (-121665 * pow(121666, p - 2, p)) % p
    y2 = y * y % p
    u = (y2 - 1) % p
    v = (d * y2 + 1) % p
    if v == 0:
        return False
    v3 = pow(v, 3, p)
    v7 = pow(v, 7, p)
    exp = (p - 5) // 8
    x = u * v3 * pow((u * v7) % p, exp, p) % p
    check = v * x * x % p
    if check == u:
        pass
    elif check == (-u) % p:
        sqrtm1 = pow(2, (p - 1) // 4, p)
        x = x * sqrtm1 % p
    else:
        return False
    if x == 0 and sign == 1:
        return False
    return True


def derive_bonding_curve_pda(mint: str) -> str:
    mintb = b58decode(mint)
    if len(mintb) != 32:
        raise ValueError('mint does not base58-decode to 32 bytes')
    progb = b58decode(PUMP_PROGRAM_ID)
    seed = b'bonding-curve'
    marker = b'ProgramDerivedAddress'
    for bump in range(255, -1, -1):
        h = hashlib.sha256(seed + mintb + bytes([bump]) + progb + marker).digest()
        if not is_on_curve(h):
            return b58encode(h)
    raise ValueError('no off-curve bonding-curve PDA found')


def decode_curve_data(b64data: str) -> Dict[str, Any]:
    raw = base64.b64decode(b64data)
    if len(raw) < 48:
        raise ValueError(f'curve data is {len(raw)} bytes, need at least 48')
    return {
        'disc': raw[0:8].hex(),
        'vTok': int.from_bytes(raw[8:16], 'little'),
        'vSol': int.from_bytes(raw[16:24], 'little'),
        'rTok': int.from_bytes(raw[24:32], 'little'),
        'rSol': int.from_bytes(raw[32:40], 'little'),
        'total': int.from_bytes(raw[40:48], 'little'),
        'complete': raw[48] != 0 if len(raw) > 48 else False,
        'creator': b58encode(raw[49:81]) if len(raw) >= 81 else None,
    }


def extract_mint(user_input: str) -> str:
    clean = user_input.strip().rstrip('/')
    return clean.split('/')[-1] if 'pump.fun' in clean else clean


def helius_rpc(method: str, params: List[Any]) -> Dict[str, Any]:
    rpc_url = helius_rpc_url()
    if not rpc_url:
        raise RuntimeError('RPC_URL missing in .env or config.json')
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    resp = requests_session.post(rpc_url, json=payload, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if data.get('error'):
        raise RuntimeError(data['error'].get('message', 'RPC error'))
    return data.get('result')


def fetch_sol_usd() -> float:
    try:
        with urlopen('https://api.coinbase.com/v2/prices/SOL-USD/spot', timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return float(data['data']['amount'])
    except Exception:
        with state_lock:
            return float(state.get('sol_price') or DEFAULT_SOL_USD)


def get_asset_metadata(mint: str) -> Dict[str, Optional[str]]:
    try:
        result = helius_rpc('getAsset', [{'id': mint}])
        md = ((result or {}).get('content') or {}).get('metadata') or {}
        return {'name': md.get('name'), 'symbol': md.get('symbol')}
    except Exception:
        return {'name': None, 'symbol': None}


def calculate_tension(last_activity: float) -> float:
    return min(1.0, max(0.0, (time.time() - last_activity) / 90.0))


def calculate_tiers(liquidity: float) -> Dict[str, float]:
    modulo = abs(liquidity) % 0.0459
    base = 0.0125 + modulo
    return {'t1': base + 0.0033, 't2': base + 0.0451, 'modulo': modulo, 'base': base}


def mcap_usd(v_sol: float, v_tok: float, sol_price: float) -> float:
    if v_tok <= 0:
        return 0.0
    return (v_sol / v_tok) * 1_000_000_000 * sol_price


def spot_price_usd(v_sol: float, v_tok: float, sol_price: float) -> float:
    if v_tok <= 0:
        return 0.0
    return (v_sol / v_tok) * sol_price


def trim_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = time.time()
    return [h for h in history if now - h['time'] <= 60]


def fetch_curve_snapshot(mint: str) -> Dict[str, Any]:
    curve = derive_bonding_curve_pda(mint)
    acct = helius_rpc('getAccountInfo', [curve, {'encoding': 'base64', 'commitment': 'confirmed'}])
    value = (acct or {}).get('value')
    if value is None:
        raise RuntimeError('No bonding curve account found for mint')
    if value.get('owner') != PUMP_PROGRAM_ID:
        raise RuntimeError('Account owner is not pump.fun program')
    decoded = decode_curve_data(value['data'][0])
    if decoded['disc'] != PUMP_CURVE_DISCRIMINATOR:
        pass
    meta = get_asset_metadata(mint)
    return {
        'mint': mint,
        'curve': curve,
        'name': meta.get('name'),
        'symbol': meta.get('symbol'),
        'decoded': decoded,
    }


def apply_snapshot(snapshot: Dict[str, Any]):
    decoded = snapshot['decoded']
    vs = decoded['vSol'] / 1e9
    vt = decoded['vTok'] / 1e6
    rs = decoded['rSol'] / 1e9
    with state_lock:
        previous_vs = float(state['v_sol'])
        if vs != previous_vs:
            state['trade_history'].append({
                'time': time.time(),
                'type': 'buy' if vs > previous_vs else 'sell',
                'delta_sol': round(abs(vs - previous_vs), 6),
            })
            state['last_activity'] = time.time()
        state['trade_history'] = trim_history(state['trade_history'])
        state.update({
            'mint': snapshot['mint'],
            'curve': snapshot['curve'],
            'name': snapshot.get('name'),
            'symbol': snapshot.get('symbol'),
            'v_sol': vs,
            'v_tok': vt,
            'r_sol': rs,
            'graduated': bool(decoded.get('complete')),
            'note': 'GRADUATED - liquidity migrated to PumpSwap' if decoded.get('complete') else None,
            'last_poll': time.time(),
            'failure_count': 0,
            'mode': 'live',
        })


def polling_worker(mint: str, stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            snap = fetch_curve_snapshot(mint)
            apply_snapshot(snap)
            with state_lock:
                state['sol_price'] = fetch_sol_usd()
        except Exception as exc:
            with state_lock:
                state['failure_count'] += 1
                state['mode'] = 'stale'
                state['note'] = str(exc)
        stop_event.wait(3.0)


def start_live_monitor(mint: str):
    stop_current_monitor()
    stop_event = threading.Event()
    thread = threading.Thread(target=polling_worker, args=(mint, stop_event), daemon=True)
    runtime['stop_event'] = stop_event
    runtime['poll_thread'] = thread
    thread.start()


def stop_current_monitor():
    ev = runtime.get('stop_event')
    if ev:
        ev.set()
    runtime['stop_event'] = None
    runtime['poll_thread'] = None


def start_demo_monitor(mint: str):
    stop_current_monitor()
    with state_lock:
        state.update({
            'mint': mint,
            'curve': 'DEMO_CURVE',
            'name': 'Demo Token',
            'symbol': 'DEMO',
            'v_sol': 32.0,
            'v_tok': 998_000_000.0,
            'r_sol': 1.85,
            'sol_price': fetch_sol_usd(),
            'failure_count': 0,
            'last_activity': time.time(),
            'last_poll': time.time(),
            'graduated': False,
            'note': 'Demo mode active',
            'trade_history': [],
            'mode': 'demo',
        })

    def loop(stop_event: threading.Event):
        while not stop_event.is_set():
            with state_lock:
                import random
                is_trade = random.random() < 0.45
                if is_trade:
                    delta = random.uniform(0.01, 0.18)
                    buy = random.random() > 0.4
                    state['v_sol'] = max(28.0, state['v_sol'] + (delta if buy else -delta))
                    state['v_tok'] = max(800_000_000.0, state['v_tok'] - (delta * 1_350_000 if buy else -delta * 1_200_000))
                    state['r_sol'] = max(0.1, state['r_sol'] + (random.uniform(0.0, 0.05) if buy else -random.uniform(0.0, 0.04)))
                    state['trade_history'].append({
                        'time': time.time(),
                        'type': 'buy' if buy else 'sell',
                        'delta_sol': round(delta, 6),
                    })
                    state['last_activity'] = time.time()
                state['trade_history'] = trim_history(state['trade_history'])
                state['sol_price'] = max(100.0, min(300.0, state['sol_price'] + random.uniform(-0.4, 0.4)))
                state['last_poll'] = time.time()
            stop_event.wait(1.0)

    stop_event = threading.Event()
    thread = threading.Thread(target=loop, args=(stop_event,), daemon=True)
    runtime['stop_event'] = stop_event
    runtime['poll_thread'] = thread
    thread.start()


class MonitorRequest(BaseModel):
    mint: str
    mode: str = 'live'


class ExecuteRequest(BaseModel):
    tier: str
    slippage: float = 25.0
    priorityFee: float = 0.002
    dryRun: bool = True


@app.get('/api/health')
def health():
    return {'ok': True, 'rpcConfigured': bool(helius_rpc_url())}


@app.post('/api/monitor/start')
def monitor_start(req: MonitorRequest):
    mint = extract_mint(req.mint)
    if not mint:
        raise HTTPException(400, 'mint required')
    if req.mode == 'demo':
        start_demo_monitor(mint)
    else:
        start_live_monitor(mint)
    return {'ok': True, 'mint': mint, 'mode': req.mode}


@app.post('/api/monitor/stop')
def monitor_stop():
    stop_current_monitor()
    with state_lock:
        state['mode'] = 'idle'
    return {'ok': True}


@app.get('/api/state')
def get_state():
    with state_lock:
        current = dict(state)
        current['trade_history'] = list(state['trade_history'])[-25:]
    tension = calculate_tension(float(current['last_activity']))
    tiers = calculate_tiers(float(current['r_sol']))
    buys = sum(1 for h in current['trade_history'] if h['type'] == 'buy')
    sells = sum(1 for h in current['trade_history'] if h['type'] == 'sell')
    current['metrics'] = {
        'mcap_usd': mcap_usd(current['v_sol'], current['v_tok'], current['sol_price']),
        'spot_usd': spot_price_usd(current['v_sol'], current['v_tok'], current['sol_price']),
        'liquidity_usd': current['r_sol'] * current['sol_price'],
        'buys_60s': buys,
        'sells_60s': sells,
        'tension': tension,
        'tiers': tiers,
    }
    return current


@app.post('/api/execute/buy')
def execute_buy(req: ExecuteRequest):
    with state_lock:
        tiers = calculate_tiers(float(state['r_sol']))
        mint = state.get('mint')
        mode = state.get('mode')
    if not mint:
        raise HTTPException(400, 'No active mint monitored yet')
    amount = tiers['t1'] if req.tier.lower() == 't1' else tiers['t2'] if req.tier.lower() == 't2' else None
    if amount is None:
        raise HTTPException(400, 'tier must be t1 or t2')
    execution = {
        'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'mint': mint,
        'tier': req.tier.lower(),
        'sol_in': round(amount, 4),
        'slippage': req.slippage,
        'priorityFee': req.priorityFee,
        'dryRun': req.dryRun,
        'status': 'simulated' if req.dryRun or mode in ('demo', 'idle', 'stale') else 'queued',
        'message': 'Localhost educational stub only — wire Jupiter or your execution backend next.',
    }
    with state_lock:
        state['execution_log'].append(execution)
        state['execution_log'] = state['execution_log'][-20:]
    return {'ok': True, 'execution': execution}


@app.get('/')
def index():
    return FileResponse(WEB_DIR / 'index.html')


app.mount('/', StaticFiles(directory=str(WEB_DIR), html=True), name='web')
