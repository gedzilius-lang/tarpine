import requests, struct, base64, os, time, threading
from colorama import init, Fore, Style
from solders.pubkey import Pubkey

init(autoreset=True)

# Configuration
PUMP_PROGRAM = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
AGENT_WALLET = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
ENV_PATH = r"C:\Users\GEDZI\Desktop\PNL 3\.env"
session = requests.Session()
session.headers.update({'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})

def load_env():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, 'r') as f:
            for line in f:
                if 'RPC_URL=' in line:
                    return line.split('=', 1)[1].strip()
    return None

def extract_mint(user_input):
    clean = user_input.strip().rstrip('/')
    return clean.split('/')[-1] if 'pump.fun' in clean else clean

def get_pda(mint_str):
    mint_pubkey = Pubkey.from_string(mint_str.strip())
    program_id = Pubkey.from_string(PUMP_PROGRAM)
    pda, _ = Pubkey.find_program_address([b'bonding-curve', bytes(mint_pubkey)], program_id)
    return str(pda)

state_cache = {
    'v_sol': 30.0, 'v_tok': 1000000000.0, 'r_sol': 0.0, 'sol_price': 155.0,
    'agent_sol': 0.0, 'last_trade_time': time.time(), 'failure_count': 0, 'trade_history': []
}
cache_lock = threading.Lock()

def network_polling_worker(rpc_url, pda_str):
    while True:
        try:
            # 1. Fetch Curve Data
            r = session.post(rpc_url, json={'jsonrpc':'2.0','id':1,'method':'getAccountInfo','params':[pda_str, {'encoding':'base64'}]}, timeout=5)
            # 2. Fetch Agent Balance
            r_agent = session.post(rpc_url, json={'jsonrpc':'2.0','id':1,'method':'getBalance','params':[AGENT_WALLET]}, timeout=5)
            
            if r.status_code == 200 and 'result' in r.json() and r.json()['result']['value']:
                data = base64.b64decode(r.json()['result']['value']['data'])
                vt, vs, rs = struct.unpack('<Q', data[8:16])[0]/1e6, struct.unpack('<Q', data[16:24])[0]/1e9, struct.unpack('<Q', data[32:40])[0]/1e9
                agent_sol = float(r_agent.json()['result']['value']) / 1e9 if r_agent.status_code == 200 else 0.0
                
                with cache_lock:
                    if vs != state_cache['v_sol']:
                        state_cache['trade_history'].append({'time': time.time(), 'type': 'buy' if vs > state_cache['v_sol'] else 'sell'})
                    state_cache.update({'v_sol': vs, 'v_tok': vt, 'r_sol': rs, 'agent_sol': agent_sol, 'failure_count': 0})
                    state_cache['trade_history'] = [t for t in state_cache['trade_history'] if time.time() - t['time'] <= 60]
            
            # Fetch Price every 60s
            if time.time() % 60 < 5:
                p = session.get('https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112', timeout=5).json()
                state_cache['sol_price'] = float(p['data']['So11111111111111111111111111111111111111112']['price'])
        except: state_cache['failure_count'] += 1
        time.sleep(3.0)

def interface():
    while True:
        print("\033[H", end="")
        with cache_lock:
            v_s, v_t, r_s, s_p, hist, fails, a_sol = state_cache['v_sol'], state_cache['v_tok'], state_cache['r_sol'], state_cache['sol_price'], list(state_cache['trade_history']), state_cache['failure_count'], state_cache['agent_sol']
        
        mcap = (v_s / v_t if v_t > 0 else 0) * 1_000_000_000 * s_p
        buys, sells = sum(1 for t in hist if t['type'] == 'buy'), sum(1 for t in hist if t['type'] == 'sell')
        tension_p = min(1.0, (time.time() - state_cache['last_trade_time']) / 90.0)
        
        # Playbook Math
        modulo_remainder = abs(a_sol) % 0.0459
        base_floor = 0.0125 + modulo_remainder
        tier1 = base_floor + 0.0033
        tier2 = base_floor + 0.0412 + 0.0039
        
        print(f"=========================================================\033[K")
        print(f" MAYHEM TERMINAL v2.2.5 DELUXE (PRODUCTION)\033[K")
        print(f" STATUS: {'ONLINE' if fails < 5 else 'STALE'}\033[K")
        print(f"=========================================================\033[K")
        print(f"[📊 METRICS] MCAP: ${mcap:,.2f} | LIQ: {r_s:.4f} SOL\033[K")
        print(f"[⚔️ COMBAT]   {buys} Buys / {sells} Sells (60s)\033[K")
        print(f"[🔮 TENSION] {'['+'█'*int(tension_p*20)+'░'*(20-int(tension_p*20))+']'} {tension_p*100:.1f}%\033[K")
        print(f"[⚙️ PLAYBOOK] T1: {tier1:.4f} SOL | T2: {tier2:.4f} SOL\033[K")
        print(f"=========================================================\033[K")
        for _ in range(5): print("\033[K")
        time.sleep(0.05)

if __name__ == '__main__':
    rpc = load_env()
    if not rpc: print("Error: RPC_URL missing in .env"); exit()
    mint_input = input('Enter Token Mint or Pump.fun URL: ').strip()
    try:
        mint_addr = extract_mint(mint_input)
        pda = get_pda(mint_addr)
        os.system('cls')
        threading.Thread(target=network_polling_worker, args=(rpc, pda), daemon=True).start()
        interface()
    except Exception as e:
        print(f"Initialization Error: {e}")