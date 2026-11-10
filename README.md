# Glamsterdam PoCs

Two simulations, each run on Ethereum mainnet and on Sepolia, which already runs
Glamsterdam gas rules. They use `eth_simulateV1` on public RPCs, so there's nothing
to deploy and no key is needed. Requires `forge`, `cast`, `curl` and `python3`.

```
forge build
python3 poc1_component_skip.py
python3 poc2_hardcoded_gas_limit.py
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

## PoC 2: a hardcoded cross-chain gas limit, and why an app can't cleanly fix it

Cross-chain messages carry a destination gas limit chosen by the sending contract.
`src/Receiver.sol` has the same shape as a bridge receiver accepting a batch from its
peer chain: it stores the batch root and 8 accounting slots. The message asks for
375,000 gas, which is what Juicebox's suckers hardcode today. The PoC runs the real
`OptimismPortal2` and `L1CrossDomainMessenger`, finalizing the withdrawal first at
`eth_estimateGas`, which is what a wallet or relayer sends, then across a sweep.

```
Base on mainnet, messenger "2.11.0", message asks for 375,000 gas
  finalize at eth_estimateGas (919,790): delivered
  finalize at 2x the estimate (1,839,580): delivered

Base Sepolia on Glamsterdam Sepolia, messenger "2.11.0", message asks for 375,000 gas
  finalize at eth_estimateGas (1,510,982): LOST: finalized, not delivered, not recorded
  finalize at 2x the estimate (3,021,964): delivered
     1,025,000 to  1,575,000  LOST: finalized, not delivered, not recorded
     1,600,000 to 10,000,000  delivered

OP mainnet on mainnet, messenger "2.11.1", message asks for 375,000 gas
  finalize at eth_estimateGas (931,416): delivered
  finalize at 2x the estimate (1,862,832): delivered

OP Sepolia on Glamsterdam Sepolia, messenger "2.13.0", message asks for 375,000 gas
  finalize at eth_estimateGas (1,517,344): recorded as failed, anyone can replay it
  finalize at 2x the estimate (3,034,688): delivered
     1,025,000 to  1,600,000  recorded as failed, anyone can replay it
     1,625,000 to 10,000,000  delivered
```

On mainnet the message lands at the estimate. Under Glamsterdam the same message,
finalized at the estimate, is lost on Base's current messenger, or needs a second
transaction on OP's upgraded one. `eth_estimateGas` finds the lowest gas at which
the transaction doesn't revert, and a relay that fails open doesn't revert, so the
estimate lands in exactly the window that fails. The 375k minimum is too small to
make the portal reject it.

The loss itself is an OP messenger bug that OP fixed in contracts v9
(ethereum-optimism/optimism PR 23788, merged 2026-10-30): the messenger kept 40,000
gas to record a failure, and under EIP-8037 the record costs about 98k. Base Sepolia
and the OP and Base mainnet messengers still run 2.11.x. With the fix, the message
isn't lost, but it still needs a second transaction.

The real problem is the 375,000. It was right when it was written. A repricing makes
it wrong, and the contract that sends it is immutable. Every way an app can respond
has the same flaw:

- **Hardcode a bigger number.** Tuned to the new gas rules. Wrong again after the next
  repricing.
- **Let the sender choose the limit.** On OP Stack L2 to L1, the message can only be
  finalized by an L1 transaction that supplies at least that much gas. The send is
  permissionless, so without a cap anyone could request a limit no transaction can
  provide and strand the batch's ETH in the portal. The cap has to be a number that
  stays under L1's transaction gas cap, and the sending chain has no way to read
  that. Another hardcoded number.
- **Make the cap an admin setting.** Works, but adds an admin to a system designed
  not to have one.
- **Require permission to raise the limit.** No number at all, but then a
  permissionless relayer gets the second transaction anyway.

Juicebox's cross-chain contracts have about 375k, 300k and 200k gas constants, the
OP messenger has 40k, and every other contract that sends a cross-chain message has
its own. None of them can be updated in place, and the chain gives them nothing to
scale from.
