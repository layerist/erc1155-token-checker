# ERC1155 Token Checker

This Python script uses the `web3.py` library to check which ERC1155 tokens are held by wallets on the Polygon network. It reads wallet addresses from a file, queries the token balances, and writes the results to an output file.

## Prerequisites

- Python 3.x
- `web3.py` library

Install the `web3.py` library if you haven't already:

```bash
pip install web3
```

## Usage

1. Clone the repository or download the script.

2. Replace `0xYourERC1155ContractAddress` in the script with your actual ERC1155 contract address.

3. Update the `token_ids` list in the script with the IDs of the tokens you want to check.

4. Create a file named `wallet_addresses.txt` in the same directory as the script. List the wallet addresses you want to check, one per line. For example:

    ```
    0x1234...abcd
    0x5678...efgh
    ```

5. Run the script:

    ```bash
    python erc1155_checker.py
    ```

6. The script will output the results to a file named `wallet_tokens.txt` and print the results to the console.

## Example

Input (`wallet_addresses.txt`):
```
0x1234...abcd
0x5678...efgh
```

Output (`wallet_tokens.txt`):
```
Address: 0x1234...abcd
  Token ID: 1, Balance: 10
  Token ID: 3, Balance: 5
Address: 0x5678...efgh has no ERC1155 tokens
```

## Notes

- Ensure you have a stable internet connection as the script connects to the Polygon network.
- The script uses the `balanceOf` function to query token balances. Ensure your ERC1155 contract supports this function.

Feel free to contribute to this repository by submitting issues or pull requests.
