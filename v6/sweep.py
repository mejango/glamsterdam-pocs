# Juicebox V6 under Glamsterdam pricing: the four fail-open paths from the
# glamsterdam@ethereum.org email (findings 2 to 5), reproduced with eth_simulateV1
# against the live V6 contracts on Glamsterdam Sepolia, and on Ethereum mainnet
# for comparison. Nothing is deployed: a fresh project is launched inside the
# simulation, paid, and then the path under test is called across a sweep of
# gas limits. Each path has a try/catch around a payment into another contract.
# Under today's pricing the caller runs out of gas before that payment can fail
# on gas alone; under Glamsterdam the inner call fails, the catch swallows it,
# and the outer transaction succeeds.
#
#   payout    sendPayoutsOf: a payout split to another project is skipped, the
#             money returns to the paying project with its payout limit used
#   nft       pay with an NFT tier whose price is split to another project: the
#             buyer keeps the NFT, the split payment fails and stays with the project
#   reserved  sendReservedTokensToSplitsOf: a reserved split to another project
#             fails, the beneficiary receives raw tokens instead
#   cashout   cashOutTokensOf: the 2.5% fee payment fails and the fee is forgiven
#
# Usage: python3 sweep.py <sepolia|ethereum> [payout|nft|reserved|cashout ...]
# Every call is pinned to one block. Every JSON-RPC request and response for the
# setup, the ample-gas run and the first gas of each outcome band is written to
# runs/<chain>/<scenario>.jsonl; the whole sweep's (gas, status, gasUsed, events)
# goes to runs/<chain>/<scenario>.grid.json. Requires `cast` (foundry) and curl.
import json, os, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

RPC = {'sepolia': 'https://ethereum-sepolia-rpc.publicnode.com', 'ethereum': 'https://ethereum-rpc.publicnode.com'}
# Juicebox V6 core, same addresses on both chains.
C = '0x3fcec3572e84b624477bcff4e2cf1f7deab648f1'    # JBController
MT = '0x130f5dd2bd8805443cf41755253d778a75a67f53'   # JBMultiTerminal
PJ = '0x6017d1fba9dc279bfa0b03fd931c22e242ab3691'   # JBProjects
TOKENS = '0x1f80d8f057ee36b4c2656d107e4e4558b71ba7d9'  # JBTokens
DEP = '0x3ffdc94e7f1de4b74c52158ec9dd3b965585f451'  # JB721TiersHookProjectDeployer
N, NC = '0x000000000000000000000000000000000000EEEe', 0xEEEe  # native token and its currency
OWN, PAY, HOLDER, BEN, BUY = ('0x00000000000000000000000000000000000B0B0' + d for d in '12345')
ZERO = '0x0000000000000000000000000000000000000000'
EVENTS = {
    '0x4b73f3c5cfb174c9d0494138d07ff8ee3aa400df46327d9893ed5ac122dd558a': 'FeeReverted',
    '0xe1dc2e46221329e3b79397625c100336d78d12de8fbbeefcc76c350ba44a89b3': 'PayoutReverted',
    '0xdba24f3504238ca84f6411d16a14bb7ae54061b0a80892bf93eb5a8912ed7355': 'ReservedDistributionReverted',
    '0x66801396c588b520bd3bcfb2e638b273ffffea3c6015e2b900cecf1d4d341e51': 'SplitPayoutReverted',
    '0xb514e730b3f8ad3aa94b6857bcc5ff4a46954bdcf8c4b0346705b1d0ac7a4325': 'ProcessFee',
    '0x946c70c84d6886ad6041aecbcdd3c9afb80133d1ba92011a9f54dbc173e6d8a7': 'SendPayoutToSplit',
    '0xbfcad5a78fa104a30927810dfb51a41d1119d43ce12be27620ab589e1273cebe': 'SendReservedTokensToSplit',
    '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef': 'Transfer',
    '0x133161f1c9161488f777ab9a26aae91d47c0d9a3fafb398960f138db02c73797': 'Pay',
}
SCENARIOS = {  # name: (event that marks the swallowed failure, what the tx did when it is present)
    'payout': ('PayoutReverted', 'payout succeeds, split to the other project skipped, money back in the paying project'),
    'nft': ('SplitPayoutReverted', 'buyer keeps the NFT, tier split payment to the other project failed'),
    'reserved': ('ReservedDistributionReverted', 'distribution succeeds, split to the other project failed, beneficiary got raw tokens'),
    'cashout': ('FeeReverted', 'cash-out succeeds, 2.5% fee payment failed and the fee is forgiven'),
}

chain = sys.argv[1]; URL = RPC[chain]
os.makedirs(f'runs/{chain}', exist_ok=True)
cast = lambda *a: subprocess.check_output(['cast', *map(str, a)], text=True).strip()
cd = lambda sig, *a: cast('calldata', sig, *a)
LOCK = threading.Lock()

def rpc(method, params, log=None):
    req = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    for _ in range(6):
        try:
            x = json.loads(subprocess.check_output(['curl', '-s', '-m', '60', '-H', 'content-type: application/json', '-d', json.dumps(req), URL]))
            if 'result' in x:
                if log:
                    with LOCK: log.write(json.dumps({'request': req, 'response': x}) + '\n'); log.flush()
                return x['result']
        except Exception: pass
    raise SystemExit(f'RPC failed: {method}')

LAUNCH = 'launchProjectFor(address,string,(uint48,uint32,uint112,uint32,address,(uint16,uint16,uint32,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,address,uint16),(uint256,(uint32,uint64,address,bool,uint48,address)[])[],(address,address,(uint224,uint32)[],(uint224,uint32)[])[])[],(address,(address,uint8,uint32)[])[],string)'
split = lambda proj=0, ben=BEN, pct=10**9: f'({pct},{proj},{ben},false,0,{ZERO})'
def ruleset(payout_splits='', reserved_splits='', limit=10**18):
    # 50% reserved, 50% cash-out tax, allowOwnerMinting and allowSetTerminals on, everything else off
    bools = ','.join(['false', 'false', 'true', 'false', 'true'] + ['false'] * 9)
    meta = f'(5000,5000,{NC},{bools},{ZERO},0)'
    groups = ([f'({NC},[{payout_splits}])'] if payout_splits else []) + ([f'(1,[{reserved_splits}])'] if reserved_splits else [])
    limits = f'[({MT},{N},[({limit},{NC})],[])]' if limit else '[]'
    return f'[(0,0,{10**21},0,{ZERO},{meta},[{",".join(groups)}],{limits})]'
launch = lambda rs, uri='x', terms=None: cd(LAUNCH, OWN, uri, rs, terms or f'[({MT},[({N},18,{NC})])]', '')
call = lambda to, data, frm=OWN, value=0, gas=15_000_000: {'from': frm, 'to': to, 'data': data, 'gas': hex(gas), **({'value': hex(value)} if value else {})}

def paymeta(target):
    # JBMetadataResolver entry for the 721 hook: id = bytes4(target ^ keccak("pay")), data = abi.encode(true, [1])
    tid = bytes(a ^ b for a, b in zip(bytes.fromhex(target[2:]), bytes.fromhex(cast('keccak', 'pay')[2:])))[:4]
    data = bytes.fromhex('00' * 31 + '01' + '00' * 31 + '40' + '00' * 31 + '01' + '00' * 31 + '01')
    return '0x' + bytes(32).hex() + (tid + bytes([2]) + bytes(27)).hex() + data.hex()

def scenario(name, block, X, fee):
    """Returns (setup blocks, target call builder, from) with project X being the next project id."""
    pay = call(MT, cd('pay(uint256,address,uint256,address,uint256,string,bytes)', X, N, 2 * 10**18, HOLDER, 0, '', '0x'), PAY, 2 * 10**18)
    erc20 = call(C, cd('deployERC20For(uint256,string,string,bytes32)', X, 'T', 'T', '0x' + '11' * 32))
    if name == 'cashout':
        setup = [[call(C, launch(ruleset(limit=0)), OWN, fee)], [pay]]
        target = (MT, cd('cashOutTokensOf(address,uint256,uint256,address,uint256,address,bytes)', HOLDER, X, 5 * 10**20, N, 0, HOLDER, '0x'), HOLDER)
    elif name == 'payout':
        # Project X+1 gets an ERC-20 so paying it mints a real token, as paying any established project does.
        setup = [[call(C, launch(ruleset(payout_splits=split(proj=X + 1))), OWN, fee), call(C, launch(ruleset(limit=0), 'y'), OWN, fee)],
                 [call(C, cd('deployERC20For(uint256,string,string,bytes32)', X + 1, 'U', 'U', '0x' + '33' * 32)), pay]]
        target = (MT, cd('sendPayoutsOf(uint256,address,uint256,uint256,uint256)', X, N, 10**18, NC, 0), BEN)
    elif name == 'reserved':
        # Project X+1 must accept project X's ERC-20, whose address is only known after the deploy call: read it in a simulation.
        probe = rpc('eth_simulateV1', [{'blockStateCalls': [{'stateOverrides': OV, 'calls': [call(C, launch(ruleset(limit=0)), OWN, fee), erc20, call(TOKENS, cd('tokenOf(uint256)', X))]}]}, block], LOG)
        token = '0x' + probe[0]['calls'][2]['returnData'][-40:]
        currency = int(token, 16) & 0xffffffff
        meta = f'(0,0,{currency},false,false,false,false,true,{",".join(["false"] * 9)},{ZERO},0)'  # denominated in that token
        rs_y = f'[(0,0,{10**21},0,{ZERO},{meta},[],[])]'
        setup = [[call(C, launch(ruleset(limit=0, reserved_splits=split(proj=X + 1))), OWN, fee)],
                 [erc20, call(C, launch(rs_y, 'y', f'[({MT},[({token},18,{currency})])]'), OWN, fee), pay]]
        target = (C, cd('sendReservedTokensToSplitsOf(uint256)', X), BEN)
    elif name == 'nft':
        price = 10**16
        tier = f'({price},1000,0,0,{ZERO},0x{"11" * 32},0,0,(false,false,false,false,false,false,false),{5 * 10**8},[{split(proj=X)}])'  # half the price goes to project X
        deploy_cfg = f'(n,s,b,{ZERO},c,([{tier}],{NC},18),(false,false,false,false,false))'
        rs_meta = f'(0,0,{NC},{",".join(["false"] * 13)},0)'
        launch_cfg = f'(uri,[(0,0,{10**18},0,{ZERO},{rs_meta},[],[])],[({MT},[({N},18,{NC})])],memo)'
        sig = 'launchProjectFor(address,(string,string,string,address,string,((uint104,uint32,uint32,uint16,address,bytes32,uint24,uint8,(bool,bool,bool,bool,bool,bool,bool),uint32,(uint32,uint64,address,bool,uint48,address)[])[],uint32,uint8),(bool,bool,bool,bool,bool)),(string,(uint48,uint32,uint112,uint32,address,(uint16,uint16,uint32,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,bool,uint16),(uint256,(uint32,uint64,address,bool,uint48,address)[])[],(address,address,(uint224,uint32)[],(uint224,uint32)[])[])[],(address,(address,uint8,uint32)[])[],string),address,bytes32)'
        setup = [[call(C, launch(ruleset(limit=0)), OWN, fee)], [call(DEP, cd(sig, OWN, deploy_cfg, launch_cfg, C, '0x' + '22' * 32), OWN, fee, 28_000_000)]]
        probe = rpc('eth_simulateV1', [{'blockStateCalls': [{'stateOverrides': OV, 'calls': setup[0]}, {'calls': setup[1]}]}, block], LOG)
        hook = '0x' + probe[1]['calls'][0]['returnData'][-40:]
        probe = rpc('eth_simulateV1', [{'blockStateCalls': [{'stateOverrides': OV, 'calls': setup[0]}, {'calls': setup[1]}, {'calls': [call(hook, cd('METADATA_ID_TARGET()'), BUY)]}]}, block], LOG)
        meta_target = '0x' + probe[2]['calls'][0]['returnData'][-40:]
        target = (MT, cd('pay(uint256,address,uint256,address,uint256,string,bytes)', X + 1, N, price, BUY, 0, '', paymeta(meta_target)), BUY, price)
    return setup, target

OV = {a: {'balance': hex(10**20)} for a in (OWN, PAY, HOLDER, BEN, BUY)}
for name in sys.argv[2:] or SCENARIOS:
    LOG = open(f'runs/{chain}/{name}.jsonl', 'w')
    head = rpc('eth_getBlockByNumber', ['latest', False], LOG)
    block = head['number']
    X = int(rpc('eth_call', [{'to': PJ, 'data': cd('count()')}, block], LOG), 16) + 1
    fee = int(rpc('eth_call', [{'to': PJ, 'data': cd('creationFee()')}, block], LOG), 16)
    setup, target = scenario(name, block, X, fee)
    to, data, frm, *value = target
    marker, meaning = SCENARIOS[name]

    def run(gas, log=None):
        blocks = [{'stateOverrides': OV, 'calls': setup[0]}, {'calls': setup[1]}, {'calls': [call(to, data, frm, value[0] if value else 0, gas)]}]
        r = rpc('eth_simulateV1', [{'blockStateCalls': blocks}, block], log)
        bad = [k for b in r[:-1] for k in b['calls'] if k['status'] != '0x1']
        if bad: raise SystemExit(f'setup failed: {json.dumps(bad)[:400]}')
        k = r[-1]['calls'][0]
        events = [EVENTS.get(l['topics'][0], l['topics'][0][:10]) for l in k['logs']]
        return {'ok': k['status'] == '0x1', 'used': int(k['gasUsed'], 16), 'peak': int(k.get('maxUsedGas', k['gasUsed']), 16), 'events': events}

    ample = run(16_000_000, LOG)
    print(f'\n{chain}, {name}: project {X}, pinned block {int(block, 16):,}, from {frm}')
    print(f'  at 16M gas: {"ok" if ample["ok"] else "REVERTS"}, gasUsed {ample["used"]:,}, peak {ample["peak"]:,}, events {sorted(set(ample["events"]))}')
    if not ample['ok'] or marker in ample['events']: raise SystemExit('baseline is not clean, stopping')
    grid = list(range(int(ample['peak'] * 0.25), int(ample['peak'] * 1.1), 2_000))
    with ThreadPoolExecutor(8) as pool: outs = list(pool.map(run, grid))
    label = lambda o: 'tx reverts' if not o['ok'] else meaning if marker in o['events'] else 'everything delivered'
    bands = []
    for g, o in zip(grid, outs):
        if not bands or bands[-1][0] != label(o): bands.append([label(o), g, g])
        else: bands[-1][2] = g
    print(f'  sweep {grid[0]:,} to {grid[-1]:,} step 2,000:')
    for lbl, a, b in bands: print(f'    {a:>10,} to {b:>10,}  {lbl}')
    for lbl, a, b in bands: run(a, LOG)  # the first gas of every band, with its request and response saved
    json.dump([{'gas': g, **o} for g, o in zip(grid, outs)], open(f'runs/{chain}/{name}.grid.json', 'w'))
