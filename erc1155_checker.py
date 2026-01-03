#!/usr/bin/env python3
"""
High-performance multithreaded proxy checker.

Improvements:
- Thread-local sessions (safe + faster)
- Stronger proxy normalization
- Adaptive backoff with jitter
- Clearer logging and structure
"""

from __future__ import annotations

import argparse
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

import requests
import urllib3
from tqdm import tqdm

# ------------------- Global Settings -------------------
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ------------------- Logging -------------------
class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[37m",
        logging.INFO: "\033[36m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[41m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        return f"{color}{super().format(record)}{self.RESET}"


def setup_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("proxy_checker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            ColorFormatter("%(asctime)s | %(levelname)-8s | %(message)s")
        )
        logger.addHandler(handler)

    logger.propagate = False
    return logger


# ------------------- Thread-local Session -------------------
_thread_local = threading.local()


def get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.verify = False
        session.headers.update(DEFAULT_HEADERS)
        _thread_local.session = session
    return _thread_local.session


# ------------------- Proxy Utilities -------------------
def normalize_proxy(proxy: str) -> Optional[Dict[str, str]]:
    proxy = proxy.strip()
    if not proxy:
        return None

    if "://" not in proxy:
        if proxy.startswith(("socks5h", "socks5")):
            proxy = f"socks5h://{proxy.split('socks5')[-1].lstrip(':/')}"
        elif proxy.startswith("socks4"):
            proxy = f"socks4://{proxy.split('socks4')[-1].lstrip(':/')}"
        else:
            proxy = f"http://{proxy}"

    return {"http": proxy, "https": proxy}


def read_proxies(path: str, logger: logging.Logger) -> List[str]:
    file = Path(path)
    if not file.is_file():
        logger.error("Proxy file not found: %s", path)
        return []

    proxies: set[str] = set()
    with file.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.strip()
            if p and normalize_proxy(p):
                proxies.add(p)

    logger.info("Loaded %d unique proxies", len(proxies))
    return sorted(proxies)


def write_proxies(path: str, proxies: List[str], logger: logging.Logger) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(proxies), encoding="utf-8")
    logger.info("Saved %d proxies → %s", len(proxies), path)


# ------------------- Proxy Checking -------------------
def check_proxy(
    proxy: str,
    test_url: str,
    retries: int,
    timeout: int,
    base_delay: float,
    logger: logging.Logger,
) -> Optional[str]:
    proxy_cfg = normalize_proxy(proxy)
    if not proxy_cfg:
        return None

    session = get_session()

    for attempt in range(1, retries + 1):
        try:
            start = time.perf_counter()
            r = session.get(
                test_url,
                proxies=proxy_cfg,
                timeout=timeout,
            )
            elapsed = time.perf_counter() - start

            if r.status_code == 200:
                logger.debug("OK %s | %.2fs", proxy, elapsed)
                return proxy

            logger.debug(
                "[%d/%d] HTTP %d | %s",
                attempt,
                retries,
                r.status_code,
                proxy,
            )
        except Exception as e:
            logger.debug("[%d/%d] FAIL %s | %s", attempt, retries, proxy, e)

        # Exponential backoff + jitter
        delay = base_delay * (2 ** (attempt - 1))
        delay *= 1 + random.random() * 0.4
        time.sleep(delay)

    return None


def validate_proxies(
    proxies: List[str],
    test_url: str,
    max_workers: int,
    retries: int,
    timeout: int,
    delay: float,
    logger: logging.Logger,
) -> List[str]:
    total = len(proxies)
    valid: List[str] = []

    if total == 0:
        return valid

    if total > 200:
        max_workers = min(max_workers, 150)

    logger.info("Checking %d proxies using %d threads", total, max_workers)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                check_proxy,
                proxy,
                test_url,
                retries,
                timeout,
                delay,
                logger,
            )
            for proxy in proxies
        ]

        with tqdm(total=total, unit="proxy", ncols=100) as bar:
            for future in as_completed(futures):
                result = future.result()
                if result:
                    valid.append(result)

                bar.set_postfix(valid=len(valid))
                bar.update(1)

    logger.info("Done: %d / %d valid", len(valid), total)
    return valid


# ------------------- Main -------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Fast multithreaded proxy checker")
    parser.add_argument("input_file")
    parser.add_argument("output_file")
    parser.add_argument("--test_url", default="https://httpbin.org/ip")
    parser.add_argument("--max_workers", type=int, default=50)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    logger = setup_logger(args.verbose)

    start = time.perf_counter()

    try:
        proxies = read_proxies(args.input_file, logger)
        if not proxies:
            logger.warning("No proxies to check.")
            return

        valid = validate_proxies(
            proxies,
            args.test_url,
            args.max_workers,
            args.retries,
            args.timeout,
            args.delay,
            logger,
        )

        write_proxies(args.output_file, valid, logger)

        logger.info("Finished in %.2fs", time.perf_counter() - start)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")


if __name__ == "__main__":
    main()
