// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// A cross-chain receiver with the same shape as a bridge endpoint accepting a
/// batch from its peer chain: store the batch root and some per-batch accounting.
/// Each field is a new storage slot the first time it's written.
contract Receiver {
    bytes32 public inboxRoot;
    uint256 public nonce;
    mapping(uint256 => uint256) public accountingOf;

    function fromRemote(bytes32 root, uint256[] calldata accounts) external payable {
        inboxRoot = root;
        nonce++;
        for (uint256 i; i < accounts.length; i++) accountingOf[accounts[i]] = block.timestamp;
    }
}
