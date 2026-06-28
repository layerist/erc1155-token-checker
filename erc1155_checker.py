#!/usr/bin/env python3
"""
ERC1155 Balance Checker for Polygon

Features:
- Web3.py v6/v7 compatible API
- Address validation and checksum normalization
- Fast ERC1155 balanceOfBatch calls
- Fallback to balanceOf if balanceOfBatch is unavailable/fails
- Retry with exponential backoff
- Chunked requests to avoid RPC/provider limits
- JSONL output by default
- Optional text output
- Clean logging and CLI config
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

load_dotenv()

DEFAULT_POLYGON_RPC = "https://polygon-rpc.com/"
DEFAULT_CHUNK_SIZE = 100
DEFAULT_RETRIES = 3
DEFAULT_RETRY_SLEEP = 0.5


# ============================================================
# LOGGING
# ============================================================

def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


# ============================================================
# HELPERS
# ============================================================

def is_connected(w3: Web3) -> bool:
    """
    Web3.py compatibility helper.
    New versions use is_connected().
    Older versions used isConnected().
    """
    if hasattr(w3, "is_connected"):
        return bool(w3.is_connected())
    return bool(w3.isConnected())


def to_checksum_address(address: str) -> str:
    """
    Web3.py compatibility helper.
    New versions use to_checksum_address().
    Older versions used toChecksumAddress().
    """
    if hasattr(Web3, "to_checksum_address"):
        return Web3.to_checksum_address(address)
    return Web3.toChecksumAddress(address)


def validate_address(address: str, label: str = "address") -> str:
    address = address.strip()

    if not Web3.is_address(address):
        raise ValueError(f"Invalid {label}: {address}")

    return to_checksum_address(address)


def chunks(items: Sequence[int], size: int) -> Iterable[List[int]]:
    for i in range(0, len(items), size):
        yield list(items[i:i + size])


def call_with_retries(fn, *, retries: int, retry_sleep: float, description: str):
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e

            if attempt >= retries:
                break

            sleep_for = retry_sleep * (2 ** (attempt - 1))
            logging.warning(
                "%s failed, attempt %s/%s: %s. Retrying in %.2fs",
                description,
                attempt,
                retries,
                e,
                sleep_for,
            )
            time.sleep(sleep_for)

    raise RuntimeError(f"{description} failed after {retries} attempts: {last_error}") from last_error


# ============================================================
# IO
# ============================================================

def load_abi(abi_path: str) -> List[Dict[str, Any]]:
    path = Path(abi_path)

    if not path.exists():
        raise FileNotFoundError(f"ABI file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as f:
            abi = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid ABI JSON: {path}") from e

    if not isinstance(abi, list):
        raise ValueError("ABI JSON must be a list")

    logging.info("ABI loaded: %s", path)
    return abi


def read_wallet_addresses(file_path: str) -> List[str]:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Wallet file not found: {path}")

    raw_wallets: List[str] = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            # allows comments in wallet file
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw_wallets.append(line)

    wallets: List[str] = []
    seen = set()

    for raw in raw_wallets:
        try:
            wallet = validate_address(raw, "wallet address")
        except ValueError as e:
            logging.warning("Skipping invalid wallet: %s", e)
            continue

        if wallet.lower() in seen:
            continue

        seen.add(wallet.lower())
        wallets.append(wallet)

    logging.info("Loaded %s valid unique wallet addresses", len(wallets))
    return wallets


def write_jsonl_result(output_path: str, result: Dict[str, Any]) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")


def write_text_result(output_path: str, wallet: str, tokens: List[Tuple[int, int]]) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:
        f.write(f"Address: {wallet}\n")

        if tokens:
            for token_id, balance in tokens:
                f.write(f"  Token ID: {token_id}, Balance: {balance}\n")
        else:
            f.write("  No ERC1155 tokens found.\n")

        f.write("\n")


# ============================================================
# WEB3 / CONTRACT
# ============================================================

def connect_rpc(rpc_url: str, request_timeout: int) -> Web3:
    provider = Web3.HTTPProvider(
        rpc_url,
        request_kwargs={"timeout": request_timeout},
    )
    w3 = Web3(provider)

    if not is_connected(w3):
        raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")

    chain_id = w3.eth.chain_id
    block_number = w3.eth.block_number

    logging.info("Connected to RPC")
    logging.info("Chain ID: %s", chain_id)
    logging.info("Latest block: %s", block_number)

    return w3


def get_contract(w3: Web3, contract_address: str, abi: List[Dict[str, Any]]):
    checksum_address = validate_address(contract_address, "contract address")
    contract = w3.eth.contract(address=checksum_address, abi=abi)

    logging.info("Contract loaded: %s", checksum_address)
    return contract


def contract_supports_function(contract, function_name: str) -> bool:
    try:
        getattr(contract.functions, function_name)
        return True
    except AttributeError:
        return False


# ============================================================
# BALANCE FETCHING
# ============================================================

def get_balances_batch(
    contract,
    wallet: str,
    token_ids: Sequence[int],
    *,
    retries: int,
    retry_sleep: float,
) -> List[Tuple[int, int]]:
    """
    ERC1155 balanceOfBatch(addresses[], ids[]).
    For one wallet and many token IDs, addresses array is repeated wallet.
    """
    addresses = [wallet] * len(token_ids)

    def _call():
        return contract.functions.balanceOfBatch(addresses, list(token_ids)).call()

    balances = call_with_retries(
        _call,
        retries=retries,
        retry_sleep=retry_sleep,
        description=f"balanceOfBatch wallet={wallet}",
    )

    result: List[Tuple[int, int]] = []

    for token_id, balance in zip(token_ids, balances):
        balance_int = int(balance)
        if balance_int > 0:
            result.append((int(token_id), balance_int))

    return result


def get_balances_single(
    contract,
    wallet: str,
    token_ids: Sequence[int],
    *,
    retries: int,
    retry_sleep: float,
) -> List[Tuple[int, int]]:
    """
    Fallback path: ERC1155 balanceOf(address, id) one by one.
    """
    result: List[Tuple[int, int]] = []

    for token_id in token_ids:
        def _call(tid=token_id):
            return contract.functions.balanceOf(wallet, int(tid)).call()

        try:
            balance = call_with_retries(
                _call,
                retries=retries,
                retry_sleep=retry_sleep,
                description=f"balanceOf wallet={wallet} token_id={token_id}",
            )
        except Exception as e:
            logging.error("Failed token_id=%s wallet=%s: %s", token_id, wallet, e)
            continue

        balance_int = int(balance)

        if balance_int > 0:
            result.append((int(token_id), balance_int))

    return result


def get_erc1155_tokens(
    contract,
    wallet: str,
    token_ids: Sequence[int],
    *,
    chunk_size: int,
    retries: int,
    retry_sleep: float,
    prefer_batch: bool = True,
) -> List[Tuple[int, int]]:
    all_tokens: List[Tuple[int, int]] = []

    can_batch = prefer_batch and contract_supports_function(contract, "balanceOfBatch")

    for token_chunk in chunks(list(token_ids), chunk_size):
        if can_batch:
            try:
                found = get_balances_batch(
                    contract,
                    wallet,
                    token_chunk,
                    retries=retries,
                    retry_sleep=retry_sleep,
                )
                all_tokens.extend(found)
                continue

            except (BadFunctionCallOutput, ContractLogicError, RuntimeError, ValueError) as e:
                logging.warning(
                    "balanceOfBatch failed for wallet=%s, chunk_size=%s. Falling back to balanceOf. Error: %s",
                    wallet,
                    len(token_chunk),
                    e,
                )

        found = get_balances_single(
            contract,
            wallet,
            token_chunk,
            retries=retries,
            retry_sleep=retry_sleep,
        )
        all_tokens.extend(found)

    return all_tokens


# ============================================================
# MAIN
# ============================================================

def normalize_token_ids(token_ids: Sequence[int]) -> List[int]:
    clean: List[int] = []
    seen = set()

    for token_id in token_ids:
        token_id = int(token_id)

        if token_id < 0:
            raise ValueError(f"Token ID cannot be negative: {token_id}")

        if token_id in seen:
            continue

        seen.add(token_id)
        clean.append(token_id)

    return clean


def process(
    *,
    contract_address: str,
    token_ids: Sequence[int],
    wallet_addresses_file: str,
    output_file: str,
    abi_path: str,
    rpc_url: str,
    chunk_size: int,
    retries: int,
    retry_sleep: float,
    request_timeout: int,
    output_format: str,
    no_batch: bool,
    clear_output: bool,
) -> None:
    token_ids = normalize_token_ids(token_ids)

    if not token_ids:
        raise ValueError("No token IDs provided")

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    if clear_output:
        Path(output_file).unlink(missing_ok=True)

    w3 = connect_rpc(rpc_url, request_timeout)
    abi = load_abi(abi_path)
    contract = get_contract(w3, contract_address, abi)
    wallets = read_wallet_addresses(wallet_addresses_file)

    if not wallets:
        raise ValueError("No valid wallet addresses found")

    total_wallets = len(wallets)
    wallets_with_tokens = 0
    total_positive_balances = 0

    logging.info("Starting scan")
    logging.info("Wallets: %s", total_wallets)
    logging.info("Token IDs: %s", len(token_ids))
    logging.info("Chunk size: %s", chunk_size)
    logging.info("Output: %s", output_file)

    started_at = time.time()

    for index, wallet in enumerate(wallets, start=1):
        logging.info("Processing wallet %s/%s: %s", index, total_wallets, wallet)

        try:
            tokens = get_erc1155_tokens(
                contract,
                wallet,
                token_ids,
                chunk_size=chunk_size,
                retries=retries,
                retry_sleep=retry_sleep,
                prefer_batch=not no_batch,
            )
        except Exception as e:
            logging.error("Wallet failed: %s | %s", wallet, e)

            error_result = {
                "wallet": wallet,
                "ok": False,
                "error": str(e),
                "tokens": [],
                "checked_token_count": len(token_ids),
                "ts": int(time.time()),
            }

            if output_format == "jsonl":
                write_jsonl_result(output_file, error_result)
            else:
                write_text_result(output_file, wallet, [])

            continue

        if tokens:
            wallets_with_tokens += 1
            total_positive_balances += len(tokens)

        result = {
            "wallet": wallet,
            "ok": True,
            "tokens": [
                {"token_id": token_id, "balance": balance}
                for token_id, balance in tokens
            ],
            "checked_token_count": len(token_ids),
            "positive_balance_count": len(tokens),
            "ts": int(time.time()),
        }

        if output_format == "jsonl":
            write_jsonl_result(output_file, result)
        else:
            write_text_result(output_file, wallet, tokens)

    elapsed = time.time() - started_at

    logging.info("Processing completed")
    logging.info("Wallets checked: %s", total_wallets)
    logging.info("Wallets with tokens: %s", wallets_with_tokens)
    logging.info("Positive balances found: %s", total_positive_balances)
    logging.info("Elapsed: %.2fs", elapsed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch ERC1155 token balances for a list of wallet addresses."
    )

    parser.add_argument(
        "--contract",
        required=True,
        help="ERC1155 contract address.",
    )

    parser.add_argument(
        "--tokens",
        type=int,
        nargs="+",
        required=True,
        help="Token IDs to check.",
    )

    parser.add_argument(
        "--wallets",
        default="wallet_addresses.txt",
        help="Path to file with wallet addresses.",
    )

    parser.add_argument(
        "--output",
        default="wallet_tokens.jsonl",
        help="Output file path.",
    )

    parser.add_argument(
        "--abi",
        required=True,
        help="Path to ERC1155 ABI JSON file.",
    )

    parser.add_argument(
        "--rpc",
        default=os.getenv("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC),
        help="Polygon RPC URL. Default: env POLYGON_RPC_URL or public RPC.",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=int(os.getenv("CHUNK_SIZE", DEFAULT_CHUNK_SIZE)),
        help="Token IDs per balanceOfBatch call.",
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=int(os.getenv("RETRIES", DEFAULT_RETRIES)),
        help="Retries per RPC call.",
    )

    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=float(os.getenv("RETRY_SLEEP", DEFAULT_RETRY_SLEEP)),
        help="Base sleep between retries.",
    )

    parser.add_argument(
        "--request-timeout",
        type=int,
        default=int(os.getenv("REQUEST_TIMEOUT", "20")),
        help="HTTP request timeout in seconds.",
    )

    parser.add_argument(
        "--format",
        choices=["jsonl", "text"],
        default="jsonl",
        help="Output format.",
    )

    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="Disable balanceOfBatch and use balanceOf one by one.",
    )

    parser.add_argument(
        "--clear-output",
        action="store_true",
        help="Delete output file before running.",
    )

    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Logging level: DEBUG, INFO, WARNING, ERROR.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.log_level)

    try:
        process(
            contract_address=args.contract,
            token_ids=args.tokens,
            wallet_addresses_file=args.wallets,
            output_file=args.output,
            abi_path=args.abi,
            rpc_url=args.rpc,
            chunk_size=args.chunk_size,
            retries=args.retries,
            retry_sleep=args.retry_sleep,
            request_timeout=args.request_timeout,
            output_format=args.format,
            no_batch=args.no_batch,
            clear_output=args.clear_output,
        )
        return 0

    except KeyboardInterrupt:
        logging.warning("Interrupted by user")
        return 130

    except Exception as e:
        logging.exception("Fatal error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
