import json
import os
from web3 import Web3
from dotenv import load_dotenv
import logging
import argparse

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables from a .env file
load_dotenv()

def connect_to_polygon():
    """Connect to the Polygon network using the provided RPC URL."""
    polygon_rpc_url = os.getenv('POLYGON_RPC_URL', 'https://polygon-rpc.com/')
    w3 = Web3(Web3.HTTPProvider(polygon_rpc_url))
    
    if not w3.isConnected():
        logging.error("Failed to connect to the Polygon network.")
        raise ConnectionError("Failed to connect to the Polygon network.")
    
    logging.info("Successfully connected to the Polygon network.")
    return w3

def load_abi(abi_path):
    """Load the ABI JSON from the specified file."""
    try:
        with open(abi_path, 'r') as abi_file:
            abi = json.load(abi_file)
        logging.info(f"ABI successfully loaded from {abi_path}")
        return abi
    except FileNotFoundError:
        logging.error(f"ABI file not found: {abi_path}")
        raise
    except json.JSONDecodeError:
        logging.error(f"Invalid ABI JSON format in file: {abi_path}")
        raise

def get_contract(w3, contract_address, abi):
    """Retrieve the contract object using the provided contract address and ABI."""
    try:
        contract = w3.eth.contract(address=Web3.toChecksumAddress(contract_address), abi=abi)
        logging.info(f"Contract loaded at address: {contract_address}")
        return contract
    except Exception as e:
        logging.error(f"Failed to load contract: {e}")
        raise

def get_erc1155_tokens(contract, wallet_address, token_ids):
    """Fetch ERC1155 token balances for the specified wallet and token IDs."""
    tokens = []
    try:
        wallet_address = Web3.toChecksumAddress(wallet_address)
        for token_id in token_ids:
            balance = contract.functions.balanceOf(wallet_address, token_id).call()
            if balance > 0:
                tokens.append((token_id, balance))
                logging.info(f"Token ID: {token_id}, Balance: {balance} for address: {wallet_address}")
    except Exception as e:
        logging.error(f"Error fetching tokens for address {wallet_address}: {e}")
    return tokens

def read_wallet_addresses(file_path):
    """Read wallet addresses from a file."""
    if not os.path.exists(file_path):
        logging.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"{file_path} not found.")
    
    with open(file_path, 'r') as file:
        wallet_addresses = [line.strip() for line in file if line.strip()]
        logging.info(f"Loaded {len(wallet_addresses)} wallet addresses.")
    
    return wallet_addresses

def write_token_data(outfile_path, wallet, tokens):
    """Write token balances to an output file."""
    with open(outfile_path, 'a') as outfile:
        outfile.write(f'Address: {wallet}\n')
        if tokens:
            for token_id, balance in tokens:
                outfile.write(f'  Token ID: {token_id}, Balance: {balance}\n')
        else:
            outfile.write(f'  No ERC1155 tokens found.\n')
    logging.info(f"Token data written for address: {wallet}")

def main(contract_address, token_ids, wallet_addresses_file, output_file, abi_path):
    """Main function to execute the ERC1155 balance check."""
    w3 = connect_to_polygon()
    abi = load_abi(abi_path)
    contract = get_contract(w3, contract_address, abi)
    wallet_addresses = read_wallet_addresses(wallet_addresses_file)
    
    for wallet in wallet_addresses:
        tokens = get_erc1155_tokens(contract, wallet, token_ids)
        write_token_data(output_file, wallet, tokens)

    logging.info('Processing completed.')

if __name__ == "__main__":
    # Argument parser for command line execution
    parser = argparse.ArgumentParser(description="Fetch ERC1155 token balances for a list of wallet addresses.")
    parser.add_argument('--contract', type=str, required=True, 
                        help='The ERC1155 contract address.')
    parser.add_argument('--tokens', type=int, nargs='+', required=True, 
                        help='List of token IDs to check.')
    parser.add_argument('--wallets', type=str, default='wallet_addresses.txt', 
                        help='Path to the file containing wallet addresses.')
    parser.add_argument('--output', type=str, default='wallet_tokens.txt', 
                        help='Path to the output file for storing token balances.')
    parser.add_argument('--abi', type=str, required=True, 
                        help='Path to the ABI JSON file for the ERC1155 contract.')

    args = parser.parse_args()

    try:
        main(args.contract, args.tokens, args.wallets, args.output, args.abi)
    except Exception as e:
        logging.error(f"Error in execution: {e}")
        exit(1)
