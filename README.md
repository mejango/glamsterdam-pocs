# Glamsterdam PoCs

Two simulations, each run on Ethereum mainnet and on Sepolia, which already runs
Glamsterdam gas rules. They use `eth_simulateV1` on public RPCs, so there's nothing
to deploy and no key is needed. Requires `forge`, `cast`, `curl` and `python3`.

```
forge build
python3 poc1_component_skip.py
python3 poc2_op_message_lost.py
```

## PoC 1: a try/catch payment to another contract can be skipped by choosing the gas limit

`src/ComponentSkip.sol` has the same shape as a Juicebox withdrawal that routes part
of the funds to another composable contract (another project, a split recipient, a
hook). `Vault.withdraw` pays 2.5% to a component in a `try`. The component mints
tokens to the payer, and a hook records the new holder. If the component's payment
reverts, the vault keeps that share and the user is still paid. That way a broken
component can't lock users' funds.

```
mainnet: Vault.withdraw at gas limits 30,000 to 900,000
     30,000 to   106,000  tx reverts
    108,000 to   898,000  component paid

glamsterdam sepolia: Vault.withdraw at gas limits 30,000 to 900,000
     30,000 to   180,000  tx reverts
    182,000 to   294,000  COMPONENT SKIPPED
    296,000 to   296,000  tx reverts
    298,000 to   898,000  component paid
```

On mainnet, no gas limit gets the transaction through without paying the component.
On Glamsterdam, any limit from 182k to 294k does. Juicebox V6 uses this pattern for
cash outs, payout splits, reserved token splits and NFT tier splits, so in each of
those the caller can make the inner payment fail while the outer call still
succeeds. The contracts are immutable.

## PoC 2: OP Stack messages to L1 can be lost

Uses the real `OptimismPortal2` and `L1CrossDomainMessenger` contracts. The
withdrawal proof is placed with a state override. Everything after that runs the
real code. The message's target burns all the gas it gets.

```
Base on mainnet, messenger "2.11.0"
     500,000 to    725,000  tx reverts, try again later
     750,000 to 10,000,000  recorded as failed, anyone can replay it

Base Sepolia on Glamsterdam Sepolia, messenger "2.11.0"
     500,000 to    800,000  tx reverts, try again later
     825,000 to  7,500,000  LOST: finalized, not delivered, not recorded
   7,750,000 to 10,000,000  recorded as failed, anyone can replay it

OP mainnet on mainnet, messenger "2.11.1"
     500,000 to    725,000  tx reverts, try again later
     750,000 to 10,000,000  recorded as failed, anyone can replay it

OP Sepolia on Glamsterdam Sepolia, messenger "2.13.0"
     500,000 to    800,000  tx reverts, try again later
     825,000 to 10,000,000  recorded as failed, anyone can replay it
```

After calling the target, messenger 2.11.x keeps 40,000 gas (`RELAY_RESERVED_GAS`)
to write `failedMessages[hash] = true`. Under EIP-8037 that new slot costs about
98k. Below about 7.7M gas, the 1/64 the messenger holds back is under 98k too,
so the write runs out of gas. The portal has already marked the withdrawal
finalized, so the message can't be finalized, relayed or replayed again.

OP fixed this in contracts v9 (ethereum-optimism/optimism PR 23788, merged
2026-10-30). The L1 reserve is now 135,000, and OP Sepolia runs it (2.13.0).
Base Sepolia and both mainnets still run 2.11.x. Every OP Stack chain's L1
messenger needs that upgrade before Glamsterdam reaches mainnet.
