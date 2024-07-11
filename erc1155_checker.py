import json
from web3 import Web3

# Connect to the Polygon network
w3 = Web3(Web3.HTTPProvider('https://polygon-rpc.com/'))

# Check if connected
if not w3.isConnected():
    raise Exception("Failed to connect to the Polygon network")

# ABI for the standard ERC1155 contract
ERC1155_ABI = json.loads('[{"constant":true,"inputs":[{"internalType":"address","name":"account","type":"address"},{"internalType":"uint256","name":"id","type":"uint256"}],"name":"balanceOf","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"payable":false,"stateMutability":"view","type":"function"},{"constant":true,"inputs":[{"internalType":"address","name":"account","type":"address"},{"internalType":"uint256[]","name":"ids","type":"uint256[]"}],"name":"balanceOfBatch","outputs":[{"internalType":"uint256[]","name":"","type":"uint256[]"}],"payable":false,"stateMutability":"view","type":"function"}]')

# Function to get ERC1155 tokens for a given address
def get_erc1155_tokens(address):
    # Replace with your ERC1155 contract address
    contract_address = '0xYourERC1155ContractAddress'
    contract = w3.eth.contract(address=contract_address, abi=ERC1155_ABI)

    # Token IDs to check
    token_ids = [1, 2, 3, 4, 5]  # Replace with actual token IDs

    tokens = []
    for token_id in token_ids:
        balance = contract.functions.balanceOf(address, token_id).call()
        if balance > 0:
            tokens.append((token_id, balance))
    return tokens

# Read wallet addresses from file
with open('wallet_addresses.txt', 'r') as file:
    wallet_addresses = [line.strip() for line in file]

# Open file to write results
with open('wallet_tokens.txt', 'w') as outfile:
    for wallet in wallet_addresses:
        tokens = get_erc1155_tokens(wallet)
        if tokens:
            print(f'Address: {wallet}')
            outfile.write(f'Address: {wallet}\n')
            for token_id, balance in tokens:
                print(f'  Token ID: {token_id}, Balance: {balance}')
                outfile.write(f'  Token ID: {token_id}, Balance: {balance}\n')
        else:
            print(f'Address: {wallet} has no ERC1155 tokens')
            outfile.write(f'Address: {wallet} has no ERC1155 tokens\n')

print('Done!')
