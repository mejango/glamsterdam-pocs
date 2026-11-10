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

`src/ComponentSkip.sol` has the same shape as a withdrawal that routes part
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
On Glamsterdam, any limit from 182k to 294k does. Treasury protocols use this pattern
wherever one contract routes funds into another it doesn't control: fees, payout
splits, reserved token splits, NFT tier splits. In each of those the caller can make
the inner payment fail while the outer call still succeeds. The contracts are
immutable.

## PoC 2: a hardcoded cross-chain gas limit, and why an app can't cleanly fix it

On most bridges, a cross-chain message carries a destination gas limit set on the
sending chain, usually by the sending contract. (Arbitrum's L2 to L1 path is the
exception: whoever executes the message on L1 supplies the gas.) `src/Receiver.sol` has the same shape as a bridge receiver accepting a batch from its
peer chain: it stores the batch root and 8 accounting slots. The message asks for
375,000 gas, a value sized to today's costs. The PoC runs the real
`OptimismPortal2` and `L1CrossDomainMessenger`, finalizing the withdrawal first at
`eth_estimateGas`, which is what a wallet or relayer sends, then across a sweep.
OP Sepolia already runs the v9 messenger, which records a failed relay so anyone
can replay it, and this assumes every OP Stack chain upgrades to it before
Glamsterdam.

```
OP mainnet on mainnet, messenger "2.11.1", message asks for 375,000 gas
  finalize at eth_estimateGas (931,416): delivered
  finalize at 2x the estimate (1,862,832): delivered
  sweep of gas limits 400,000 to 10,000,000:
       400,000 to    900,000  tx reverts, try again later
       925,000 to 10,000,000  delivered

OP Sepolia on Glamsterdam Sepolia, messenger "2.13.0", message asks for 375,000 gas
  finalize at eth_estimateGas (1,517,344): recorded as failed, anyone can replay it
  finalize at 2x the estimate (3,034,688): delivered
  sweep of gas limits 400,000 to 10,000,000:
       400,000 to  1,000,000  tx reverts, try again later
     1,025,000 to  1,600,000  recorded as failed, anyone can replay it
     1,625,000 to 10,000,000  delivered
```

On mainnet the message lands at the estimate. Under Glamsterdam the same message,
finalized at the estimate, arrives under-gassed. `eth_estimateGas` finds the lowest
gas at which the transaction doesn't revert, and a relay that fails open doesn't
revert, so the estimate lands in exactly the window that fails. The 375k minimum is
too small to make the portal reject it.

What that means for someone moving funds from the L2 to L1, which already takes a
week on an OP Stack chain:

**Today.** They send the batch on the L2. After the output root is posted, they
prove the withdrawal on L1. Seven days later they finalize it on L1, with whatever
gas their wallet estimates. The message is delivered, the funds are credited, and
they claim them. Two L1 transactions, both of which their wallet knows how to send.

**Under Glamsterdam.** The same, up to the finalize. The wallet estimates gas, the
transaction succeeds, and nothing is credited. The portal has marked the withdrawal
finalized, so finalizing again reverts. The messenger has recorded the relay as
failed, but no wallet or bridge UI watches for that. Someone has to find the
`FailedRelayedMessage` event, rebuild the original message from it, and call
`relayMessage` on the messenger directly with about twice the gas the wallet chose.
Until someone does, the funds sit in the messenger. Three L1 transactions, the last
of which the user's tools don't know exists, and the only sign anything went wrong
is a balance that didn't change.

Nothing is lost in the second case, but every batch sent at the hardcoded limit
goes through it. That third transaction is what an app would want to prevent.

The constant was right when it was written. A repricing makes it wrong, and the
contract that sends it is immutable. To get rid of the third transaction, an app
has to change what limit its messages ask for, and every way of doing that has the
same flaw:

- **Hardcode a bigger number.** Tuned to the new gas rules. Wrong again after the next
  repricing.
- **Let the sender choose the limit.** On OP Stack L2 to L1, the message can only be
  finalized by an L1 transaction that supplies at least that much gas. The send is
  permissionless, so without a cap anyone could request a limit no transaction can
  provide and strand the batch's ETH in the portal. The cap has to be a number that
  stays under L1's transaction gas cap, and the sending chain has no way to read
  that. Another hardcoded number.
- **Put an admin in charge of it.** Either an admin sets the cap, or only an admin
  may raise the limit. Both work, and both add an admin to a system designed not to
  have one. The second also leaves everyone else, like a public relayer, at the
  hardcoded minimum, so their batches still need the third transaction.

Every contract that sends a cross-chain message carries constants like this one.
None of them can be updated in place, and the chain gives them nothing to scale from.
