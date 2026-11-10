// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

// ponytail: fixed addresses so the PoC places code with state overrides, no deploy needed
address constant FEE_PROJECT = address(uint160(0xFEE0));
address constant TOKEN = address(uint160(0xFEE1));
address constant HOOK = address(uint160(0xFEE2));

/// After a mint, records the holder (like a pay hook or data hook).
contract Hook {
    mapping(address => uint256) public firstSeen;
    function afterMint(address holder) external {
        if (firstSeen[holder] == 0) firstSeen[holder] = block.timestamp; // new slot for a new holder
    }
}

/// Mints fee-project tokens to whoever paid the fee.
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

/// The fee project: keeps the fee and mints its tokens to the payer.
contract FeeProject {
    uint256 public balance;
    function payFee(address payer) external payable {
        balance += msg.value;
        Token(TOKEN).mint(payer, msg.value * 1000);
    }
}

/// Same shape as a Juicebox cash out: pay the fee in a try, and if the fee
/// payment fails, don't block the user. Keep the fee in the vault instead.
contract Vault {
    uint256 public feesNotPaid;

    event FeePaid(uint256 amount);
    event FeeFailed(uint256 amount);

    function cashOut(uint256 amount) external {
        uint256 fee = amount / 40; // 2.5%
        try FeeProject(FEE_PROJECT).payFee{value: fee}(msg.sender) {
            emit FeePaid(fee);
        } catch {
            feesNotPaid += fee;
            emit FeeFailed(fee);
        }
        payable(msg.sender).transfer(amount - fee);
    }
}
