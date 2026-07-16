import requests, struct, base64, os, time, threading
from colorama import init, Fore, Style

init(autoreset=True)

# Path to your .env file
ENV_PATH = r"C:\Users\GEDZI\Desktop\PNL 3\.env"

PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5AMX'
AGENT_WALLET = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
RPC_HEADERS = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}

def load_env():
    """Parses .env file for RPC and WSS keys."""
    env_vars = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, 'r') as f:
            for line in f:
                if '=' in line:
                    key, val = line.strip().split('=', 1)
                    env_vars[key.strip()] = val.strip()
    return env_vars.get('RPC_URL'), env_vars.get('WSS_URL')

# State Cache
state_cache = {
    'v_sol': 30.0, 'v_tok': 1000000000.0, 'r_sol': 0.0, 'r_tok': 0.0,
    'sol_price': 155.0, 'last_trade_time': time.time(), 'last_sig': '', 
    'network_triggered': False, 'trade_history': []
}
cache_lock = threading.Lock()

def get_pda(mint_str):
    from solders.pubkey import Pubkey
    return str(Pubkey.find_program_address([b'bonding-curve', bytes(Pubkey.from_string(mint_str))], Pubkey.from_string(PUMP_PROGRAM))[0])

def network_polling_worker(rpc_url, mint_str):
    pda = get_pda(mint_str)
    while True:
        try:
            r_curve = requests.post(rpc_url, headers=RPC_HEADERS, json={'jsonrpc':'2.0','id':1,'method':'getAccountInfo','params':[pda, {'encoding':'base64'}]}, timeout=4).json()
            if 'result' in r_curve and r_curve['result']['value']:
                data = base64.b64decode(r_curve['result']['value']['data'])
                vt = struct.unpack('<Q', data[8:16])[0] / 1e6
                vs = struct.unpack('<Q', data[16:24])[0] / 1e9
                rt = struct.unpack('<Q', data[24:32])[0] / 1e6
                rs = struct.unpack('<Q', data[32:40])[0] / 1e9
                
                with cache_lock:
                    last_vs = state_cache['v_sol']
                    if vs != last_vs:
                        state_cache['trade_history'].append({'time': time.time(), 'type': 'buy' if vs > last_vs else 'sell'})
                    state_cache.update({'v_sol': vs, 'v_tok': vt, 'r_sol': rs, 'network_triggered': True})
                    state_cache['trade_history'] = [t for t in state_cache['trade_history'] if time.time() - t['time'] <= 60]
        except: pass
        time.sleep(3.0)

def interface_render_loop():
    while True:
        print("\033[H", end="")
        with cache_lock:
            v_s, v_t, r_s, hist = state_cache['v_sol'], state_cache['v_tok'], state_cache['r_sol'], list(state_cache['trade_history'])
        
        # Fixed: using v_t (the local variable) instead of the undefined v_tok
        mcap = (v_s / v_t if v_t > 0 else 0) * 1_000_000_000 * 155.0
        buys, sells = sum(1 for t in hist if t['type'] == 'buy'), sum(1 for t in hist if t['type'] == 'sell')
        
        print(f"=========================================================\033[K")
        print(f" MAYHEM TERMINAL GEYSER v2.2.4 DELUXE (FIXED)\033[K")
        print(f" TIMESTAMP: {time.strftime('%H:%M:%S')}\033[K")
        print(f"=========================================================\033[K")
        print(f"[📊 METRICS] MCAP: ${mcap:,.2f} | LIQ: {r_s:.4f} SOL\033[K")
        print(f"[⚔️ COMBAT]   {buys} Buys / {sells} Sells (60s)\033[K")
        print(f"[⚙️ PLAYBOOK] T1: {((r_s*0.01)+0.02):.4f} SOL\033[K")
        print(f"=========================================================\033[K")
        for _ in range(5): print("\033[K")
        time.sleep(0.03)

if __name__ == '__main__':
    rpc, wss = load_env()
    if not rpc:
        print("Error: RPC_URL not found in .env file.")
        exit()
    mint = input('Enter Token Mint: ').strip()
    os.system('cls' if os.name == 'nt' else 'clear')
    threading.Thread(target=network_polling_worker, args=(rpc, mint), daemon=True).start()
    interface_render_loop()