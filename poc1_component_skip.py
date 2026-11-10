# PoC 1: today, a try/catch payment to another contract happens at every gas
# limit where the transaction succeeds. On Glamsterdam, the caller can make that
# inner payment fail, while the transaction still succeeds, by choosing the gas limit.
# Runs Vault.withdraw at every gas limit in a range, with eth_simulateV1 on
# Glamsterdam Sepolia and on Ethereum mainnet. Balances start non-zero,
# like contract balances that already hold funds. No deploy and no key needed.
import json, subprocess
from concurrent.futures import ThreadPoolExecutor
RPC = {'mainnet': 'https://ethereum-rpc.publicnode.com', 'glamsterdam sepolia': 'https://ethereum-sepolia-rpc.publicnode.com'}
VAULT = '0x' + 'a' * 40
AT = lambda n: '0x' + '0' * 36 + n
USER = '0x000000000000000000000000000000000000B0B0'
code = lambda n: json.load(open(f'out/ComponentSkip.sol/{n}.json'))['deployedBytecode']['object']
cast = lambda *a: subprocess.check_output(['cast', *a], text=True).strip()
PAID, FAILED = cast('keccak', 'ComponentPaid(uint256)'), cast('keccak', 'ComponentFailed(uint256)')
DATA = cast('calldata', 'withdraw(uint256)', str(10**18))

ONE_V = '0x' + '0' * 63 + '1'
SLOT1 = '0x' + '0' * 63 + '1'
ONE = {'0x' + '0' * 64: ONE_V}  # balances that already hold funds start at 1

def rpc(url, method, params):
    for _ in range(5):
        try: return json.loads(subprocess.check_output(['curl', '-s', '-m', '60', '-H', 'content-type: application/json', '-d', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}), url]))
        except Exception: pass
    raise SystemExit(f'RPC failed: {url}')

def run(url, gas):
    r = rpc(url, 'eth_simulateV1', [{'blockStateCalls': [{
        'stateOverrides': {VAULT: {'code': code('Vault'), 'balance': hex(10**19), 'stateDiff': ONE}, AT('c0c0'): {'code': code('Component'), 'stateDiff': ONE}, AT('c0c1'): {'code': code('Token'), 'stateDiff': {SLOT1: ONE_V}}, AT('c0c2'): {'code': code('Hook')}, USER: {'balance': hex(10**19)}},
        'calls': [{'from': USER, 'to': VAULT, 'data': DATA, 'gas': hex(gas)}]}]}, 'latest'])['result'][0]['calls'][0]
    if int(r['status'], 16) == 0: return 'tx reverts'
    topics = [l['topics'][0] for l in r['logs']]
    return 'component paid' if PAID in topics else 'COMPONENT SKIPPED' if FAILED in topics else '?'

lo, hi, step = 30_000, 900_000, 2_000
for name, url in RPC.items():
    rows, prev = [], None
    gases = range(lo, hi, step)
    with ThreadPoolExecutor(8) as pool: outs = list(pool.map(lambda g: run(url, g), gases))
    for g, out in zip(gases, outs):
        if out != prev: rows.append([out, g, g]); prev = out
        else: rows[-1][2] = g
    print(f'\n{name}: Vault.withdraw at gas limits {lo:,} to {hi:,}')
    for out, a, b in rows: print(f'  {a:>9,} to {b:>9,}  {out}')
