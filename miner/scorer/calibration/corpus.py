"""Calibration corpus for ONCHAIN_TX_LOOKUP.

Each case is (question, ground_truth, good_answer, bad_answer). The GOOD answer
should be judged materially above the BAD answer by any correct scorer; the
signal we care about is mean(good) - mean(bad), matching the node's
"candidate_margin" formula.

Cases mirror the answer shapes real miners produce for on-chain tx / wallet /
event lookups: hashes, addresses, amounts, times, block heights, direction,
counterparties.
"""

CASES = [
    # 1. exact tx hash + amount answer
    dict(
        q="What was the value transferred in tx 0x9f2c1a8b3e5d7f04c6b9e1a2d3f4c5b6a7e8d9f0a1b2c3d4e5f6a7b8c9d0e1f2?",
        gt="Transaction 0x9f2c1a8b3e5d7f04c6b9e1a2d3f4c5b6a7e8d9f0a1b2c3d4e5f6a7b8c9d0e1f2 transferred 12.5 ETH from 0x742d35Cc6634C0532925a3b8D404d3aAb3E2f44e to 0x8ba1f109551bD432803012645Hac136c4b8f9C1E at block 21456789.",
        good="Tx 0x9f2c1a8b3e5d7f04c6b9e1a2d3f4c5b6a7e8d9f0a1b2c3d4e5f6a7b8c9d0e1f2 sent 12.5 ETH from 0x742d35Cc6634C0532925a3b8D404d3aAb3E2f44e to 0x8ba1f109551bD432803012645Hac136c4b8f9C1E in block 21456789.",
        bad="The transaction transferred a large amount of ETH between two wallets around that time.",
    ),
    # 2. wrong address (same shape) - identifiers must match exactly
    dict(
        q="Which address received the 500 USDC transfer in block 19200000?",
        gt="Address 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 received 500 USDC in that transfer.",
        good="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 received the 500 USDC transfer.",
        bad="0xB0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 received the 500 USDC transfer.",
    ),
    # 3. terse-correct vs verbose-wrong
    dict(
        q="How much ETH was sent in tx 0xabc123?",
        gt="Tx 0xabc123 sent 3.75 ETH from 0xDEAD to 0xBEEF at block 100000.",
        good="3.75 ETH.",
        bad="A meaningful amount of Ether was transferred between the sender and the receiver during that block, part of a broader pattern of on-chain activity commonly seen in recent months across many wallets and protocols.",
    ),
    # 4. wrong figure by 10x
    dict(
        q="What was the gas fee of block 20000000?",
        gt="Block 20000000 had an average gas fee of 12 gwei.",
        good="12 gwei.",
        bad="120 gwei.",
    ),
    # 5. wrong scale (millions vs billions)
    dict(
        q="What was the total USDT volume on-chain today?",
        gt="Total USDT on-chain volume today was $2.14 billion.",
        good="Around $2.13 billion USDT moved on-chain today.",
        bad="Around $214 million USDT moved on-chain today.",
    ),
    # 6. wrong direction (sender <-> receiver)
    dict(
        q="Who sent the 1000 USDC in tx 0x1234?",
        gt="Address 0xA1 sent 1000 USDC to 0xB2 in that transaction.",
        good="0xA1 sent 1000 USDC to 0xB2.",
        bad="0xB2 sent 1000 USDC to 0xA1.",
    ),
    # 7. keyword stuff (harvest ground-truth words, no coherence)
    dict(
        q="Describe tx 0xfeed.",
        gt="Tx 0xfeed transferred 1.2 ETH from wallet 0xCAFE to contract 0xBABE at block 500000 with 21000 gas used.",
        good="Tx 0xfeed sent 1.2 ETH from 0xCAFE to 0xBABE at block 500000, gas 21000.",
        bad="tx 0xfeed 1.2 ETH wallet 0xCAFE contract 0xBABE block 500000 21000 gas transferred used",
    ),
    # 8. padding / repetition
    dict(
        q="What did wallet 0xAAAA do at block 100?",
        gt="Wallet 0xAAAA deposited 5 ETH to Aave at block 100.",
        good="Wallet 0xAAAA deposited 5 ETH to Aave at block 100.",
        bad=("Wallet 0xAAAA deposited 5 ETH to Aave at block 100. " * 40).strip(),
    ),
    # 9. contradictory answer (says X and also not X)
    dict(
        q="Was the transaction successful?",
        gt="The transaction succeeded and moved 2 ETH to 0xC0DE.",
        good="Yes, it succeeded and moved 2 ETH to 0xC0DE.",
        bad="Yes it succeeded but it failed and reverted with no ETH moved.",
    ),
    # 10. right words, wrong entity
    dict(
        q="Which exchange received the deposit in tx 0xbeef?",
        gt="Binance received the 10 BTC deposit in tx 0xbeef.",
        good="Binance received the 10 BTC deposit.",
        bad="Coinbase received the 10 BTC deposit.",
    ),
    # 11. off-topic answer
    dict(
        q="What was transferred in tx 0xabcd?",
        gt="0.5 WBTC was transferred in tx 0xabcd.",
        good="0.5 WBTC.",
        bad="The weather has been unusually warm this week.",
    ),
    # 12. blank / whitespace
    dict(
        q="Value of tx 0xdead?",
        gt="1.5 ETH.",
        good="1.5 ETH.",
        bad="   ",
    ),
    # 13. rounded number vs wrong magnitude phrased same
    dict(
        q="How much USDC in tx 0x1?",
        gt="Tx 0x1 moved 1,234,567.89 USDC.",
        good="Roughly 1.23M USDC.",
        bad="About 12.3 million USDC.",
    ),
    # 14. right amount, wrong token
    dict(
        q="What token was moved in tx 0x2?",
        gt="Tx 0x2 moved 100 DAI from 0xA to 0xB.",
        good="100 DAI moved from 0xA to 0xB.",
        bad="100 USDT moved from 0xA to 0xB.",
    ),
    # 15. negation flip
    dict(
        q="Did wallet 0xAAAA make a profit on that trade?",
        gt="Yes, 0xAAAA made a profit of 3.2 ETH.",
        good="0xAAAA profited 3.2 ETH on the trade.",
        bad="0xAAAA did not make a profit on the trade.",
    ),
    # 16. block number correct vs off by 1
    dict(
        q="What block did the transaction land in?",
        gt="Block 18500123.",
        good="Block 18500123.",
        bad="Block 18500122.",
    ),
    # 17. structured JSON answer
    dict(
        q="Give tx details for 0xabc.",
        gt='{"hash":"0xabc","from":"0xA","to":"0xB","value":"1.5 ETH","block":100}',
        good='{"hash":"0xabc","from":"0xA","to":"0xB","value":"1.5 ETH","block":100}',
        bad='{"hash":"0xdef","from":"0xX","to":"0xY","value":"5 ETH","block":200}',
    ),
    # 18. partial identifier truncation (address prefix only, ambiguous)
    dict(
        q="What address made the largest transfer?",
        gt="0x742d35Cc6634C0532925a3b8D404d3aAb3E2f44e made the largest transfer of 500 ETH.",
        good="0x742d35Cc6634C0532925a3b8D404d3aAb3E2f44e transferred 500 ETH - the largest.",
        bad="Some 0x742d wallet moved a lot.",
    ),
    # 19. multiple facts, one wrong
    dict(
        q="Summarize the tx.",
        gt="At block 20000000, wallet 0xAAAA sent 5 ETH to 0xBBBB and paid 0.002 ETH in gas.",
        good="At block 20000000, 0xAAAA sent 5 ETH to 0xBBBB, gas fee 0.002 ETH.",
        bad="At block 20000000, 0xAAAA sent 50 ETH to 0xCCCC, gas fee 0.02 ETH.",
    ),
    # 20. word-list dump (harvested) should NOT beat concise correct
    dict(
        q="What happened?",
        gt="Wallet 0xF00 swapped 1 ETH for 3200 USDC on Uniswap V3.",
        good="0xF00 swapped 1 ETH for 3200 USDC on Uniswap V3.",
        bad="Swapped Wallet 0xF00 for 1 ETH USDC on Uniswap V3 3200. Wallet 0xF00 swapped 1 ETH for USDC 3200 V3 Uniswap on.",
    ),
    # 21. contradiction of key fact
    dict(
        q="Was there a mint event?",
        gt="Yes, 1M USDC was minted to 0xC1RCLE at block 21000000.",
        good="Yes, 1M USDC minted to 0xC1RCLE at block 21000000.",
        bad="No mint event occurred in that block.",
    ),
    # 22. wrong protocol
    dict(
        q="Which protocol was interacted with?",
        gt="The transaction interacted with Uniswap V3.",
        good="Uniswap V3.",
        bad="SushiSwap.",
    ),
    # 23. hash off by one character
    dict(
        q="Tx hash of the largest ETH transfer today?",
        gt="0xdeadbeef00000000000000000000000000000000000000000000000000000000",
        good="0xdeadbeef00000000000000000000000000000000000000000000000000000000",
        bad="0xdeadbeef00000000000000000000000000000000000000000000000000000001",
    ),
    # 24. correct dense answer vs vague filler
    dict(
        q="What's the balance of 0xVAULT?",
        gt="0xVAULT holds 12,500 ETH and 8.4M USDC.",
        good="12,500 ETH plus 8.4M USDC.",
        bad="A significant amount of assets.",
    ),
    # 25. right facts vs wrong exchange
    dict(
        q="What did the whale do?",
        gt="Wallet 0xWH moved 2000 ETH to Kraken.",
        good="0xWH moved 2000 ETH to Kraken.",
        bad="0xWH moved 2000 ETH to FTX.",
    ),
]

if __name__ == "__main__":
    print(f"{len(CASES)} cases")
