# PoC 2: cross-chain messages carry a destination gas limit that the sending
# contract hardcodes. Here the message asks for 375,000 gas, a value sized to
# today's costs, and the receiver stores a batch root plus 8 accounting
# slots. That fits on mainnet. Under Glamsterdam pricing it doesn't, so the
# message is under-gassed when it arrives on L1.
#
# Uses the real OP L1 contracts on Glamsterdam Sepolia and on Ethereum mainnet,
# with eth_simulateV1. The withdrawal proof is placed with a state override
# (as if it had been proven a week ago); everything after that is the real
# OptimismPortal2.finalizeWithdrawalTransaction and L1CrossDomainMessenger.relayMessage.
# For each chain it finalizes at eth_estimateGas, which is what a wallet or
# relayer sends, and then sweeps gas limits.
#
# Every call (eth_call, eth_estimateGas, eth_simulateV1) is pinned to the same
# L1 block, the latest one when the chain's run starts. The simulated blocks
# are the two that would follow it (pinned time + 12s and + 24s); the proof is
# placed early enough to be mature at the pinned block itself, so the estimate
# and the simulation see the same maturity state. `from` and `tx.origin` are
# CALLER for every call. Every JSON-RPC request and response is appended to
# runs/latest/rpc.jsonl, and the printed output goes to runs/latest/output.txt.
import json, os, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

MAINNET, SEPOLIA = 'https://ethereum-rpc.publicnode.com', 'https://ethereum-sepolia-rpc.publicnode.com'
CHAINS = {  # L1 RPC, OP L1CrossDomainMessenger proxy. OP Sepolia already runs the v9 messenger that records failures.
    'OP mainnet on mainnet': (MAINNET, '0x25ace71c97B33Cc4729CF772ae268934F7ab5fA1'),
    'OP Sepolia on Glamsterdam Sepolia': (SEPOLIA, '0x58Cc85b8D04EA49cC6DBd3CbFFd00B4B8D6cb3ef'),
}
L2_MESSENGER = '0x4200000000000000000000000000000000000007'
TARGET = '0x00000000000000000000000000000000000dEAD0'  # Receiver code, placed by state override
SENDER = '0x00000000000000000000000000000000000B0B01'  # any L2 sender
CALLER = '0x00000000000000000000000000000000000B0B02'  # whoever finalizes on L1
MIN_GAS = 375_000  # what the sender hardcodes: 300k base + 75k per accounting record
SLOTS = 8
MESSAGE = subprocess.check_output(['cast', 'calldata', 'fromRemote(bytes32,uint256[])', '0x' + 'ab' * 32, '[' + ','.join(str(i + 1) for i in range(SLOTS)) + ']'], text=True).strip()
RECEIVER = json.load(open('out/Receiver.sol/Receiver.json'))['deployedBytecode']['object']

cast = lambda *a: subprocess.check_output(['cast', *map(str, a)], text=True).strip()
word = lambda r: int(r[2:66], 16)
addr = lambda r: '0x' + r[-40:]

os.makedirs('runs/latest', exist_ok=True)
LOG, LOCK = open('runs/latest/rpc.jsonl', 'w'), threading.Lock()
OUT = open('runs/latest/output.txt', 'w')
def say(line): print(line); OUT.write(line + '\n'); OUT.flush()

def rpc(url, method, params):
    req = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    for _ in range(5):
        try:
            x = json.loads(subprocess.check_output(['curl', '-s', '-m', '60', '-H', 'content-type: application/json', '-d', json.dumps(req), url]))
            if 'result' in x:
                with LOCK: LOG.write(json.dumps({'url': url, 'request': req, 'response': x}) + '\n'); LOG.flush()
                return x['result']
        except Exception: pass
    raise SystemExit(f'RPC failed: {method} {url}')

def sweep(name, url, messenger, gases):
    pinned = rpc(url, 'eth_getBlockByNumber', ['latest', False])
    block, now = pinned['number'], int(pinned['timestamp'], 16)
    view = lambda to, sig, *a: rpc(url, 'eth_call', [{'to': to, 'data': cast('calldata', sig, *a)}, block])
    portal = addr(view(messenger, 'portal()'))
    registry = addr(view(portal, 'anchorStateRegistry()'))
    factory = addr(view(registry, 'disputeGameFactory()'))
    delay = word(view(portal, 'proofMaturityDelaySeconds()'))
    proven_at = now - delay - 3600  # mature at the pinned block, so the estimate and the simulation agree
    # The proof must point at a valid game created before proven_at. The anchor game is usually
    # too young for that, so binary search the factory for the newest game old enough, then
    # step back until the registry accepts its claim.
    created = lambda i: int(view(factory, 'gameAtIndex(uint256)', i)[66:130], 16)
    lo, hi = 0, word(view(factory, 'gameCount()')) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if created(mid) < proven_at: lo = mid
        else: hi = mid - 1
    while True:
        game = addr(view(factory, 'gameAtIndex(uint256)', lo)[:194])
        if word(view(registry, 'isGameClaimValid(address)', game)): break
        lo -= 1

    # The L2 message: relayMessage on the L1 messenger, sent by the L2 messenger.
    relay = cast('calldata', 'relayMessage(uint256,address,address,uint256,uint256,bytes)', (1 << 240) | 1, SENDER, TARGET, 0, MIN_GAS, MESSAGE)
    msg_hash = cast('keccak', relay)
    w_nonce = (1 << 240) | 2
    w_gas = word(view(messenger, 'baseGas(bytes,uint32)', MESSAGE, MIN_GAS))
    w_hash = cast('keccak', cast('abi-encode', 'f(uint256,address,address,uint256,uint256,bytes)', w_nonce, L2_MESSENGER, messenger, 0, w_gas, relay))
    finalize = cast('calldata', 'finalizeWithdrawalTransaction((uint256,address,address,uint256,uint256,bytes))', f'({w_nonce},{L2_MESSENGER},{messenger},0,{w_gas},{relay})')
    # provenWithdrawals[w_hash][CALLER] = (game, proven_at), mapping at slot 57
    proof_slot = cast('keccak', cast('abi-encode', 'f(address,bytes32)', CALLER, cast('keccak', cast('abi-encode', 'f(bytes32,uint256)', w_hash, 57))))
    proof = '0x' + f'{proven_at:024x}' + game[2:].lower()
    reads = [(portal, cast('calldata', 'finalizedWithdrawals(bytes32)', w_hash)),
             (messenger, cast('calldata', 'successfulMessages(bytes32)', msg_hash)),
             (messenger, cast('calldata', 'failedMessages(bytes32)', msg_hash))]

    overrides = {portal: {'stateDiff': {proof_slot: proof}}, TARGET: {'code': RECEIVER}, CALLER: {'balance': hex(10**20)}}

    def run(gas, show=False):
        r = rpc(url, 'eth_simulateV1', [{'blockStateCalls': [
            {'stateOverrides': overrides,
             'blockOverrides': {'time': hex(now + 12)},
             'calls': [{'from': CALLER, 'to': portal, 'data': finalize, 'gas': hex(gas)}]},
            {'blockOverrides': {'time': hex(now + 24)},
             'calls': [{'from': CALLER, 'to': t, 'data': d} for t, d in reads]}]}, block])
        raw = [c['returnData'] for c in r[1]['calls']]
        if show:
            say(f'    finalize call status {r[0]["calls"][0]["status"]}, gasUsed {int(r[0]["calls"][0]["gasUsed"], 16):,}')
            for label, v in zip(('finalizedWithdrawals[withdrawalHash]', 'successfulMessages[msgHash]', 'failedMessages[msgHash]'), raw): say(f'    {label} = {v}')
        finalized, delivered, recorded = (int(v, 16) for v in raw)
        if not finalized: return 'tx reverts, try again later'
        if delivered: return 'delivered'
        if recorded: return 'recorded as failed, anyone can replay it'
        return 'LOST: finalized, not delivered, not recorded'

    version = cast('abi-decode', 'f()(string)', view(messenger, 'version()'))
    say(f'\n{name}, messenger {version}, message asks for {MIN_GAS:,} gas')
    say(f'  pinned block {int(block, 16):,} (timestamp {now}), simulated blocks at +12s and +24s')
    say(f'  from / tx.origin {CALLER}, portal {portal}, messenger {messenger}')
    say(f'  proof: game {game} (factory index {lo}), proven at {proven_at}, maturity delay {delay}')
    say(f'  withdrawalHash {w_hash}')
    say(f'  msgHash (versioned, nonce {hex((1 << 240) | 1)}) {msg_hash}')
    est = int(rpc(url, 'eth_estimateGas', [{'from': CALLER, 'to': portal, 'data': finalize}, block, overrides]), 16)
    say(f'  finalize at eth_estimateGas ({est:,}):')
    say(f'    {run(est, show=True)}')
    say(f'  finalize at 2x the estimate ({2 * est:,}):')
    say(f'    {run(2 * est, show=True)}')

    with ThreadPoolExecutor(8) as pool: outs = list(pool.map(run, gases))
    rows, prev = [], None
    for g, out in zip(gases, outs):
        if out != prev: rows.append([out, g, g]); prev = out
        else: rows[-1][2] = g
    say(f'  sweep of gas limits {gases[0]:,} to {gases[-1]:,}:')
    for out, a, b in rows: say(f'    {a:>10,} to {b:>10,}  {out}')

gases = list(range(400_000, 2_000_000, 25_000)) + list(range(2_000_000, 10_000_001, 500_000))
for name, (url, messenger) in CHAINS.items(): sweep(name, url, messenger, gases)
