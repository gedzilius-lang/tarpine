import { Connection, Keypair, VersionedTransaction } from '@solana/web3.js';
import fetch from 'cross-fetch';
import { Wallet } from '@project-serum/anchor';
import bs58 from 'bs58';

const mint = process.argv[2];
const solIn = parseFloat(process.argv[3] || '0');
const slippagePct = parseFloat(process.argv[4] || '25');
const priorityFeeSol = parseFloat(process.argv[5] || '0.002');

const rpcUrl = process.env.RPC_URL;
const privateKey = process.env.PRIVATE_KEY || process.env.TRADING_PRIVATE_KEY;
if (!mint) throw new Error('mint missing');
if (!rpcUrl) throw new Error('RPC_URL missing');
if (!privateKey) throw new Error('PRIVATE_KEY missing');

const connection = new Connection(rpcUrl, 'confirmed');
const wallet = new Wallet(Keypair.fromSecretKey(bs58.decode(privateKey)));
const inputMint = 'So11111111111111111111111111111111111111112';
const outputMint = mint;
const amount = Math.round(solIn * 1e9);
const slippageBps = Math.round(slippagePct * 100);
const prioritizationFeeLamports = Math.round(priorityFeeSol * 1e9);

const quoteUrl = `https://quote-api.jup.ag/v6/quote?inputMint=${inputMint}&outputMint=${outputMint}&amount=${amount}&slippageBps=${slippageBps}`;
const quoteResponse = await (await fetch(quoteUrl)).json();
if (quoteResponse.error) {
  throw new Error(`Jupiter quote failed: ${quoteResponse.error}`);
}

const swapRes = await (await fetch('https://quote-api.jup.ag/v6/swap', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    quoteResponse,
    userPublicKey: wallet.publicKey.toString(),
    wrapAndUnwrapSol: true,
    dynamicComputeUnitLimit: true,
    prioritizationFeeLamports: prioritizationFeeLamports > 0 ? prioritizationFeeLamports : 'auto'
  })
})).json();

if (swapRes.error || !swapRes.swapTransaction) {
  throw new Error(`Jupiter swap build failed: ${swapRes.error || 'no swapTransaction'}`);
}

const swapTransactionBuf = Buffer.from(swapRes.swapTransaction, 'base64');
const transaction = VersionedTransaction.deserialize(swapTransactionBuf);
transaction.sign([wallet.payer]);

const latestBlockHash = await connection.getLatestBlockhash();
const rawTransaction = transaction.serialize();
const txid = await connection.sendRawTransaction(rawTransaction, {
  skipPreflight: true,
  maxRetries: 2,
});

await connection.confirmTransaction({
  blockhash: latestBlockHash.blockhash,
  lastValidBlockHeight: latestBlockHash.lastValidBlockHeight,
  signature: txid,
});

process.stdout.write(JSON.stringify({
  ok: true,
  provider: 'jupiter',
  signature: txid,
  message: 'Jupiter transaction submitted and confirmed'
}));
