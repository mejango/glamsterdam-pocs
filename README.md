# Glamsterdam PoC

A simulation run on Ethereum mainnet and on Sepolia, which already runs Glamsterdam
gas rules. It uses `eth_simulateV1` on a public RPC, so there's nothing to deploy and
no key is needed. Requires `forge`, `cast`, `curl` and `python3`.

```
forge build
python3 poc1_component_skip.py
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
