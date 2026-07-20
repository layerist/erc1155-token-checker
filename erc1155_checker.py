#!/usr/bin/env python3
"""
Reliable ERC-1155 balance checker.

Key features:
- Web3.py 6/7 compatible
- Polygon by default, configurable chain ID and multiple RPC endpoints
- Built-in minimal ERC-1155 ABI (external ABI remains optional)
- RPC failover with exponential backoff and jitter
- Batch calls with adaptive chunk splitting before single-call fallback
- Optional fixed block for a consistent snapshot
- JSONL or text output
- Resume support for JSONL output
- Invalid-wallet reporting, progress logs, and deterministic token ordering
- No private keys or transactions: read-only eth_call requests only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

T = TypeVar("T")
LOG = logging.getLogger("erc1155_checker")

DEFAULT_RPC = "https://polygon-rpc.com/"
DEFAULT_CHAIN_ID = 137
DEFAULT_CHUNK_SIZE = 200
DEFAULT_RETRIES = 3
DEFAULT_RETRY_SLEEP = 0.5
DEFAULT_TIMEOUT = 20

MINIMAL_ERC1155_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "uint256", "name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address[]", "name": "accounts", "type": "address[]"},
            {"internalType": "uint256[]", "name": "ids", "type": "uint256[]"},
        ],
        "name": "balanceOfBatch",
        "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass(frozen=True)
class RpcEndpoint:
    url: str
    w3: Web3


class RpcPool:
    def __init__(
        self,
        urls: Sequence[str],
        *,
        timeout: int,
        expected_chain_id: int | None,
        retries: int,
        retry_sleep: float,
    ) -> None:
        self.retries = retries
        self.retry_sleep = retry_sleep
        self._endpoints: list[RpcEndpoint] = []
        self._index = 0

        for url in unique_preserving_order(urls):
            provider = Web3.HTTPProvider(url, request_kwargs={"timeout": timeout})
            w3 = Web3(provider)
            try:
                if not is_connected(w3):
                    raise ConnectionError("is_connected returned false")
                chain_id = int(w3.eth.chain_id)
                if expected_chain_id is not None and chain_id != expected_chain_id:
                    raise ValueError(
                        f"unexpected chain ID {chain_id}, expected {expected_chain_id}"
                    )
                block = int(w3.eth.block_number)
                self._endpoints.append(RpcEndpoint(url=url, w3=w3))
                LOG.info("RPC ready: %s | chain=%s | block=%s", redact_url(url), chain_id, block)
            except Exception as exc:
                LOG.warning("RPC unavailable: %s | %s", redact_url(url), exc)

        if not self._endpoints:
            raise ConnectionError("No usable RPC endpoints")

    @property
    def current(self) -> RpcEndpoint:
        return self._endpoints[self._index]

    def call(self, fn: Callable[[Web3], T], description: str) -> T:
        last_error: Exception | None = None
        attempts = max(1, self.retries) * len(self._endpoints)

        for attempt in range(1, attempts + 1):
            endpoint = self.current
            try:
                return fn(endpoint.w3)
            except Exception as exc:
                last_error = exc
                LOG.warning(
                    "%s failed via %s (%s/%s): %s",
                    description,
                    redact_url(endpoint.url),
                    attempt,
                    attempts,
                    compact_error(exc),
                )
                self._index = (self._index + 1) % len(self._endpoints)
                if attempt < attempts:
                    exponent = min((attempt - 1) // len(self._endpoints), 6)
                    delay = self.retry_sleep * (2**exponent) + random.uniform(0, 0.25)
                    time.sleep(delay)

        raise RuntimeError(f"{description} failed: {compact_error(last_error)}") from last_error


def setup_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), None)
    if not isinstance(numeric, int):
        raise ValueError(f"Invalid log level: {level}")
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def is_connected(w3: Web3) -> bool:
    method = getattr(w3, "is_connected", None) or getattr(w3, "isConnected", None)
    if method is None:
        raise RuntimeError("Unsupported Web3.py version")
    return bool(method())


def to_checksum_address(value: str) -> str:
    method = getattr(Web3, "to_checksum_address", None) or getattr(Web3, "toChecksumAddress", None)
    if method is None:
        raise RuntimeError("Unsupported Web3.py version")
    return str(method(value))


def validate_address(value: str, label: str) -> str:
    value = value.strip()
    if not Web3.is_address(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return to_checksum_address(value)


def redact_url(url: str) -> str:
    # Avoid leaking API keys embedded in RPC URLs into logs.
    if "?" in url:
        return url.split("?", 1)[0] + "?…"
    parts = url.rsplit("/", 1)
    if len(parts) == 2 and len(parts[1]) > 24:
        return parts[0] + "/…"
    return url


def compact_error(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    text = " ".join(str(exc).split())
    return text[:500]


def unique_preserving_order(values: Iterable[T]) -> list[T]:
    result: list[T] = []
    seen: set[T] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def iter_chunks(items: Sequence[int], size: int) -> Iterator[list[int]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def normalize_token_ids(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for raw in values:
        token_id = int(raw)
        if token_id < 0:
            raise ValueError(f"Token ID cannot be negative: {token_id}")
        if token_id not in seen:
            seen.add(token_id)
            result.append(token_id)
    return sorted(result)


def parse_token_file(path_value: str | None) -> list[int]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"Token file not found: {path}")
    values: list[int] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        clean = line.split("#", 1)[0].strip()
        if not clean:
            continue
        for part in clean.replace(",", " ").split():
            try:
                values.append(int(part, 0))
            except ValueError as exc:
                raise ValueError(f"Invalid token ID at {path}:{line_number}: {part!r}") from exc
    return values


def load_abi(path_value: str | None) -> list[dict[str, Any]]:
    if not path_value:
        return MINIMAL_ERC1155_ABI
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"ABI file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid ABI JSON: {path}: {exc}") from exc
    # Some explorers return {"abi": [...]}.
    if isinstance(data, dict) and isinstance(data.get("abi"), list):
        data = data["abi"]
    if not isinstance(data, list):
        raise ValueError("ABI must be a JSON list or an object containing an 'abi' list")
    return data


def read_wallets(path_value: str, *, strict: bool) -> tuple[list[str], list[dict[str, Any]]]:
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"Wallet file not found: {path}")

    wallets: list[str] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()

    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        clean = line.split("#", 1)[0].strip()
        if not clean:
            continue
        # Accept either one address per line or the first CSV/whitespace field.
        raw = clean.replace(",", " ").split()[0]
        try:
            wallet = validate_address(raw, "wallet address")
        except ValueError as exc:
            item = {"line": line_number, "value": raw, "error": str(exc)}
            errors.append(item)
            if strict:
                raise ValueError(f"Invalid wallet at {path}:{line_number}: {raw!r}") from exc
            LOG.warning("Skipping invalid wallet at line %s: %s", line_number, raw)
            continue
        key = wallet.lower()
        if key not in seen:
            seen.add(key)
            wallets.append(wallet)

    return wallets, errors


def completed_wallets_from_jsonl(path: Path) -> set[str]:
    completed: set[str] = set()
    if not path.exists():
        return completed
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            LOG.warning("Ignoring malformed JSONL line %s while resuming", line_number)
            continue
        wallet = item.get("wallet")
        if isinstance(wallet, str) and item.get("ok") is True:
            completed.add(wallet.lower())
    return completed


class ResultWriter:
    def __init__(self, path_value: str, output_format: str, *, flush: bool) -> None:
        self.path = Path(path_value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.format = output_format
        self.handle = self.path.open("a", encoding="utf-8", buffering=1 if flush else -1)

    def close(self) -> None:
        self.handle.close()

    def __enter__(self) -> "ResultWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def write(self, result: dict[str, Any]) -> None:
        if self.format == "jsonl":
            self.handle.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
            return

        wallet = result["wallet"]
        self.handle.write(f"Address: {wallet}\n")
        if not result.get("ok"):
            self.handle.write(f"  ERROR: {result.get('error', 'unknown error')}\n\n")
            return
        tokens = result.get("tokens", [])
        if tokens:
            for token in tokens:
                self.handle.write(
                    f"  Token ID: {token['token_id']}, Balance: {token['balance']}\n"
                )
        else:
            self.handle.write("  No ERC1155 tokens found.\n")
        self.handle.write("\n")


def ensure_contract(pool: RpcPool, address: str) -> None:
    code = pool.call(lambda w3: w3.eth.get_code(address), "eth_getCode")
    if not code or bytes(code) == b"":
        raise ValueError(f"No contract bytecode at {address}")


def make_contract(w3: Web3, address: str, abi: list[dict[str, Any]]):
    return w3.eth.contract(address=address, abi=abi)


def fetch_batch(
    pool: RpcPool,
    contract_address: str,
    abi: list[dict[str, Any]],
    wallet: str,
    token_ids: Sequence[int],
    block_identifier: int | str,
) -> list[int]:
    addresses = [wallet] * len(token_ids)

    def invoke(w3: Web3) -> list[int]:
        contract = make_contract(w3, contract_address, abi)
        values = contract.functions.balanceOfBatch(addresses, list(token_ids)).call(
            block_identifier=block_identifier
        )
        if len(values) != len(token_ids):
            raise ValueError(f"balanceOfBatch returned {len(values)} values for {len(token_ids)} IDs")
        return [int(value) for value in values]

    return pool.call(invoke, f"balanceOfBatch wallet={wallet} ids={len(token_ids)}")


def fetch_single(
    pool: RpcPool,
    contract_address: str,
    abi: list[dict[str, Any]],
    wallet: str,
    token_id: int,
    block_identifier: int | str,
) -> int:
    def invoke(w3: Web3) -> int:
        contract = make_contract(w3, contract_address, abi)
        return int(
            contract.functions.balanceOf(wallet, token_id).call(
                block_identifier=block_identifier
            )
        )

    return pool.call(invoke, f"balanceOf wallet={wallet} token={token_id}")


def fetch_chunk_adaptive(
    pool: RpcPool,
    contract_address: str,
    abi: list[dict[str, Any]],
    wallet: str,
    token_ids: Sequence[int],
    block_identifier: int | str,
    *,
    allow_batch: bool,
) -> list[tuple[int, int]]:
    if not token_ids:
        return []

    if allow_batch and len(token_ids) > 1:
        try:
            balances = fetch_batch(
                pool, contract_address, abi, wallet, token_ids, block_identifier
            )
            return [
                (token_id, balance)
                for token_id, balance in zip(token_ids, balances)
                if balance > 0
            ]
        except Exception as exc:
            midpoint = len(token_ids) // 2
            LOG.warning(
                "Batch of %s IDs failed; splitting into %s + %s: %s",
                len(token_ids),
                midpoint,
                len(token_ids) - midpoint,
                compact_error(exc),
            )
            left = fetch_chunk_adaptive(
                pool,
                contract_address,
                abi,
                wallet,
                token_ids[:midpoint],
                block_identifier,
                allow_batch=True,
            )
            right = fetch_chunk_adaptive(
                pool,
                contract_address,
                abi,
                wallet,
                token_ids[midpoint:],
                block_identifier,
                allow_batch=True,
            )
            return left + right

    token_id = int(token_ids[0])
    balance = fetch_single(
        pool, contract_address, abi, wallet, token_id, block_identifier
    )
    return [(token_id, balance)] if balance > 0 else []


def scan_wallet(
    pool: RpcPool,
    contract_address: str,
    abi: list[dict[str, Any]],
    wallet: str,
    token_ids: Sequence[int],
    block_identifier: int | str,
    *,
    chunk_size: int,
    no_batch: bool,
) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for chunk in iter_chunks(token_ids, chunk_size):
        found.extend(
            fetch_chunk_adaptive(
                pool,
                contract_address,
                abi,
                wallet,
                chunk,
                block_identifier,
                allow_batch=not no_batch,
            )
        )
    return sorted(found)


def resolve_rpc_urls(cli_urls: Sequence[str] | None) -> list[str]:
    values: list[str] = []
    if cli_urls:
        values.extend(cli_urls)
    env_value = os.getenv("POLYGON_RPC_URLS") or os.getenv("POLYGON_RPC_URL")
    if env_value:
        values.extend(part.strip() for part in env_value.split(",") if part.strip())
    if not values:
        values.append(DEFAULT_RPC)
    return values


def process(args: argparse.Namespace) -> None:
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be greater than zero")
    if args.retries <= 0:
        raise ValueError("--retries must be greater than zero")
    if args.retry_sleep < 0:
        raise ValueError("--retry-sleep cannot be negative")
    if args.request_timeout <= 0:
        raise ValueError("--request-timeout must be greater than zero")

    contract_address = validate_address(args.contract, "contract address")
    token_ids = normalize_token_ids((args.tokens or []) + parse_token_file(args.token_file))
    if not token_ids:
        raise ValueError("Provide token IDs through --tokens and/or --token-file")

    abi = load_abi(args.abi)
    wallets, invalid_wallets = read_wallets(args.wallets, strict=args.strict_wallets)
    if not wallets:
        raise ValueError("No valid wallet addresses found")

    output_path = Path(args.output)
    if args.clear_output:
        output_path.unlink(missing_ok=True)
    if args.resume and args.format != "jsonl":
        raise ValueError("--resume is supported only with --format jsonl")

    completed = completed_wallets_from_jsonl(output_path) if args.resume else set()
    wallets_to_scan = [wallet for wallet in wallets if wallet.lower() not in completed]

    pool = RpcPool(
        resolve_rpc_urls(args.rpc),
        timeout=args.request_timeout,
        expected_chain_id=None if args.chain_id < 0 else args.chain_id,
        retries=args.retries,
        retry_sleep=args.retry_sleep,
    )
    ensure_contract(pool, contract_address)

    if args.block == "latest":
        block_identifier: int | str = "latest"
    elif args.block == "snapshot":
        block_identifier = pool.call(lambda w3: int(w3.eth.block_number), "get snapshot block")
    else:
        block_identifier = int(args.block, 0)

    LOG.info(
        "Scan start | wallets=%s pending=%s tokens=%s chunk=%s block=%s invalid_wallets=%s",
        len(wallets),
        len(wallets_to_scan),
        len(token_ids),
        args.chunk_size,
        block_identifier,
        len(invalid_wallets),
    )

    started = time.monotonic()
    successful = failed = wallets_with_tokens = positive_balances = 0

    with ResultWriter(args.output, args.format, flush=not args.no_flush) as writer:
        for index, wallet in enumerate(wallets_to_scan, 1):
            wallet_started = time.monotonic()
            try:
                tokens = scan_wallet(
                    pool,
                    contract_address,
                    abi,
                    wallet,
                    token_ids,
                    block_identifier,
                    chunk_size=args.chunk_size,
                    no_batch=args.no_batch,
                )
                successful += 1
                if tokens:
                    wallets_with_tokens += 1
                    positive_balances += len(tokens)
                result = {
                    "wallet": wallet,
                    "ok": True,
                    "contract": contract_address,
                    "chain_id": None if args.chain_id < 0 else args.chain_id,
                    "block": block_identifier,
                    "checked_token_count": len(token_ids),
                    "positive_balance_count": len(tokens),
                    "tokens": [
                        {"token_id": token_id, "balance": balance}
                        for token_id, balance in tokens
                    ],
                    "elapsed_ms": round((time.monotonic() - wallet_started) * 1000),
                    "timestamp": int(time.time()),
                }
            except Exception as exc:
                failed += 1
                LOG.error("Wallet failed: %s | %s", wallet, compact_error(exc))
                result = {
                    "wallet": wallet,
                    "ok": False,
                    "contract": contract_address,
                    "chain_id": None if args.chain_id < 0 else args.chain_id,
                    "block": block_identifier,
                    "checked_token_count": len(token_ids),
                    "tokens": [],
                    "error": compact_error(exc),
                    "elapsed_ms": round((time.monotonic() - wallet_started) * 1000),
                    "timestamp": int(time.time()),
                }
            writer.write(result)
            LOG.info(
                "Progress %s/%s | %s | found=%s | %.2fs",
                index,
                len(wallets_to_scan),
                wallet,
                result.get("positive_balance_count", 0),
                time.monotonic() - wallet_started,
            )

    LOG.info(
        "Completed | success=%s failed=%s skipped_resume=%s wallets_with_tokens=%s "
        "positive_balances=%s elapsed=%.2fs",
        successful,
        failed,
        len(completed),
        wallets_with_tokens,
        positive_balances,
        time.monotonic() - started,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check ERC-1155 balances for many wallets and token IDs."
    )
    parser.add_argument("--contract", required=True, help="ERC-1155 contract address")
    parser.add_argument("--tokens", type=int, nargs="*", help="Token IDs")
    parser.add_argument("--token-file", help="Text file containing token IDs")
    parser.add_argument("--wallets", default="wallet_addresses.txt", help="Wallet file")
    parser.add_argument("--output", default="wallet_tokens.jsonl", help="Output path")
    parser.add_argument(
        "--abi",
        help="Optional ABI JSON. The built-in minimal ERC-1155 ABI is used by default",
    )
    parser.add_argument(
        "--rpc",
        action="append",
        help="RPC URL; repeat for failover. Env: POLYGON_RPC_URLS or POLYGON_RPC_URL",
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.getenv("CHAIN_ID", str(DEFAULT_CHAIN_ID))),
        help="Expected chain ID; use -1 to disable validation",
    )
    parser.add_argument(
        "--block",
        default="snapshot",
        help="snapshot (default), latest, or an integer block number",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=int(os.getenv("CHUNK_SIZE", str(DEFAULT_CHUNK_SIZE))),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=int(os.getenv("RETRIES", str(DEFAULT_RETRIES))),
    )
    parser.add_argument(
        "--retry-sleep",
        type=float,
        default=float(os.getenv("RETRY_SLEEP", str(DEFAULT_RETRY_SLEEP))),
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=int(os.getenv("REQUEST_TIMEOUT", str(DEFAULT_TIMEOUT))),
    )
    parser.add_argument("--format", choices=("jsonl", "text"), default="jsonl")
    parser.add_argument("--no-batch", action="store_true", help="Use balanceOf only")
    parser.add_argument("--resume", action="store_true", help="Skip successful wallets in existing JSONL")
    parser.add_argument("--clear-output", action="store_true", help="Delete output before scan")
    parser.add_argument("--strict-wallets", action="store_true", help="Fail on first invalid wallet")
    parser.add_argument("--no-flush", action="store_true", help="Do not line-flush output")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        setup_logging(args.log_level)
        if args.resume and args.clear_output:
            raise ValueError("--resume and --clear-output cannot be used together")
        process(args)
        return 0
    except KeyboardInterrupt:
        LOG.warning("Interrupted by user")
        return 130
    except Exception as exc:
        if logging.getLogger().handlers:
            LOG.exception("Fatal error: %s", compact_error(exc))
        else:
            print(f"Fatal error: {compact_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
