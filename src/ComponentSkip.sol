// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

// ponytail: fixed addresses so the PoC places code with state overrides, no deploy needed
address constant COMPONENT = address(uint160(0xC0C0));
address constant TOKEN = address(uint160(0xC0C1));
address constant HOOK = address(uint160(0xC0C2));

/// After a mint, records the holder (like a pay hook or data hook).
contract Hook {
    mapping(address => uint256) public firstSeen;
    function afterMint(address holder) external {
        if (firstSeen[holder] == 0) firstSeen[holder] = block.timestamp; // new slot for a new holder
    }
}

/// Mints the component's tokens to whoever paid it.
contract Token {
    mapping(address => uint256) public balanceOf;
    uint256 public totalSupply;
    event Transfer(address indexed from, address indexed to, uint256 value);
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount; // new slot for a new holder
        totalSupply += amount;
        emit Transfer(address(0), to, amount);
        Hook(HOOK).afterMint(to);
    }
}

/// Any composable contract the vault pays into: another project, a split
/// recipient, a hook. This one keeps the payment and mints tokens to the payer.
contract Component {
    uint256 public balance;
    function pay(address payer) external payable {
        balance += msg.value;
        Token(TOKEN).mint(payer, msg.value * 1000);
    }
}

/// Same shape as a Juicebox withdrawal that routes part of the funds to
/// another contract: pay the component in a try, and if that payment fails,
/// don't block the user. Keep the component's share in the vault instead.
contract Vault {
    uint256 public sharesNotSent;

    event ComponentPaid(uint256 amount);
    event ComponentFailed(uint256 amount);

    function withdraw(uint256 amount) external {
        uint256 share = amount / 40; // 2.5% goes to the component
        try Component(COMPONENT).pay{value: share}(msg.sender) {
            emit ComponentPaid(share);
        } catch {
            sharesNotSent += share;
            emit ComponentFailed(share);
        }
        payable(msg.sender).transfer(amount - share);
    }
}
