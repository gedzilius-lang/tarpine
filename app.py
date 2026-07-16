import os, requests, struct, base64, random, time, math
from flask import Flask, render_template_string, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={os.getenv('HELIUS_API_KEY', '').strip()}"
AGENT_WALLET = 'BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s'
HEADERS = {'User-Agent': 'Mozilla/5.0'}

@app.route('/')
def index():
    return render_template_string(open("dashboard.html").read())

@app.route('/api/recalibrate', methods=['POST'])
def recalibrate():
    req = request.get_json()
    mint = req.get('mint')
    
    try:
        # 1. Fetch Bonding Curve PDA
        pda_resp = requests.get(f"https://frontend-api.pump.fun/coins/{mint}", headers=HEADERS).json()
        curve_pda = pda_resp.get("bonding_curve")
        
        # 2. Batch Request for Stats
        batch = [
            {"jsonrpc":"2.0","id":1,"method":"getAccountInfo","params":[curve_pda, {"encoding":"base64"}]},
            {"jsonrpc":"2.0","id":2,"method":"getBalance","params":[AGENT_WALLET]}
        ]
        r = requests.post(HELIUS_RPC, json=batch).json()
        
        # 3. Decode
        data = base64.b64decode(r[0]['result']['value']['data'][0])
        v_tok = struct.unpack('<Q', data[8:16])[0] / 1e6
        v_sol = struct.unpack('<Q', data[16:24])[0] / 1e9
        
        return jsonify({
            "market_cap": (v_sol/v_tok)*1e9*145 if v_tok > 0 else 0,
            "liquidity": v_sol,
            "agent_sol": r[1]['result']['value'] / 1e9,
            "msg": "Sync successful"
        })
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
