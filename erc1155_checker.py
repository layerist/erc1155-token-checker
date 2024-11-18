import json
import os
import logging
import argparse
from web3 import Web3
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Load environment variables
load_dotenv()

def connect_to_polygon():
    """Establish a connection to the Polygon network."""
    polygon_rpc_url = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com/")
    w3 = Web3(Web3.HTTPProvider(polygon_rpc_url))
    
    if not w3.isConnected():
        logging.error("Failed to connect to the Polygon network.")
        raise ConnectionError("Could not connect to the Polygon network.")
    
    logging.info("Connected to the Polygon network.")
    return w3

def load_abi(abi_path):
    """Load the ABI from a file."""
    if not os.path.isfile(abi_path):
        logging.error(f"ABI file not found: {abi_path}")
        raise FileNotFoundError(f"ABI file not found: {abi_path}")
    
    try:
        with open(abi_path, "r") as file:
            abi = json.load(file)
        logging.info("ABI loaded successfully.")
        return abi
    except json.JSONDecodeError:
        logging.error("Invalid ABI JSON format.")
        raise ValueError("Invalid ABI JSON format.")

def get_contract(w3, contract_address, abi):
    """Create a contract instance."""
    try:
        contract = w3.eth.contract(address=Web3.toChecksumAddress(contract_address), abi=abi)
        logging.info(f"Contract initialized at address: {contract_address}")
        return contract
    except ValueError as ve:
        logging.error(f"Invalid contract address: {contract_address}")
        raise ve
    except Exception as e:
        logging.error(f"Error initializing contract: {e}")
        raise

def fetch_erc1155_balances(contract, wallet_address, token_ids):
    """Retrieve balances of ERC1155 tokens for a wallet."""
    wallet_address = Web3.toChecksumAddress(wallet_address)
    balances = []
    
    for token_id in token_ids:
        try:
            balance = contract.functions.balanceOf(wallet_address, token_id).call()
            if balance > 0:
                balances.append((token_id, balance))
                logging.info(f"Wallet {wallet_address}: Token ID {token_id}, Balance {balance}")
        except Exception as e:
            logging.error(f"Error fetching balance for Token ID {token_id}: {e}")
    
    return balances

def read_wallet_addresses(file_path):
    """Read wallet addresses from a file."""
    if not os.path.isfile(file_path):
        logging.error(f"Wallet addresses file not found: {file_path}")
        raise FileNotFoundError(f"Wallet addresses file not found: {file_path}")
    
    try:
        with open(file_path, "r") as file:
            addresses = [line.strip() for line in file if line.strip()]
        logging.info(f"Loaded {len(addresses)} wallet addresses.")
        return addresses
    except Exception as e:
        logging.error(f"Error reading wallet addresses: {e}")
        raise

def write_results(output_file, wallet, tokens):
    """Write token balances to the output file."""
    try:
        with open(output_file, "a") as file:
            file.write(f"Wallet: {wallet}\n")
            if tokens:
                for token_id, balance in tokens:
                    file.write(f"  Token ID {token_id}: {balance}\n")
            else:
                file.write("  No tokens found.\n")
        logging.info(f"Results written for wallet {wallet}.")
    except Exception as e:
        logging.error(f"Error writing to output file: {e}")
        raise

def main(contract_address, token_ids, wallets_file, output_file, abi_path):
    """Main function to retrieve ERC1155 balances."""
    try:
        w3 = connect_to_polygon()
        abi = load_abi(abi_path)
        contract = get_contract(w3, contract_address, abi)
        wallet_addresses = read_wallet_addresses(wallets_file)

        for wallet in wallet_addresses:
            tokens = fetch_erc1155_balances(contract, wallet, token_ids)
            write_results(output_file, wallet, tokens)
        
        logging.info("Token retrieval completed successfully.")
    except Exception as e:
        logging.error(f"Execution failed: {e}")
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch ERC1155 token balances for a list of wallet addresses.")
    parser.add_argument("--contract", required=True, help="The ERC1155 contract address.")
    parser.add_argument("--tokens", type=int, nargs="+", required=True, help="List of token IDs to check.")
    parser.add_argument("--wallets", default="wallet_addresses.txt", help="Path to the wallet addresses file.")
    parser.add_argument("--output", default="wallet_tokens.txt", help="Path to the output file.")
    parser.add_argument("--abi", required=True, help="Path to the ABI JSON file.")

    args = parser.parse_args()

    try:
        main(args.contract, args.tokens, args.wallets, args.output, args.abi)
    except Exception as e:
        logging.error(f"Script terminated with error: {e}")
        exit(1)
