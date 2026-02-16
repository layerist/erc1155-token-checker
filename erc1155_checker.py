#!/usr/bin/env python3
"""
High-performance multithreaded proxy checker.

Improvements:
- Safe future handling (no hidden crashes)
- Graceful cancellation on Ctrl+C
- HTTPS-only mode
- Optional max latency filter
- Smarter worker scaling
- Better proxy normalization (auth support)
- Clean structure & strong typing
"""

from __future__ import annotations

import argparse
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
import urllib3
from requests.adapters import HTTPAdapter
from tqdm import tqdm

# --------------------------------------------------
# Global Settings
# --------------------------------------------------

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

MAX_GLOBAL_WORKERS = 200


# --------------------------------------------------
# Logging
# --------------------------------------------------

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


# --------------------------------------------------
# Thread-local Session
# --------------------------------------------------

_thread_local = threading.local()


def get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.verify = False
        session.headers.update(DEFAULT_HEADERS)

        adapter = HTTPAdapter(
            pool_connections=100,
            pool_maxsize=100,
            max_retries=0,
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        _thread_local.session = session

    return _thread_local.session


# --------------------------------------------------
# Proxy Utilities
# --------------------------------------------------

def normalize_proxy(proxy: str, https_only: bool = False) -> Optional[Dict[str, str]]:
    proxy = proxy.strip()
    if not proxy:
        return None

    if "://" not in proxy:
        proxy = f"http://{proxy}"

    if https_only and not proxy.startswith("https://"):
        return None

    return {
        "http": proxy,
        "https": proxy,
    }


def read_proxies(path: str, logger: logging.Logger) -> List[str]:
    file = Path(path)
    if not file.is_file():
        logger.error("Proxy file not found: %s", path)
        return []

    proxies: Set[str] = set()

    with file.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            proxy = line.strip()
            if proxy:
                proxies.add(proxy)

    logger.info("Loaded %d unique proxies", len(proxies))
    return sorted(proxies)


def write_proxies(path: str, proxies: List[str], logger: logging.Logger) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(proxies), encoding="utf-8")
    logger.info("Saved %d proxies → %s", len(proxies), path)


# --------------------------------------------------
# Proxy Checking
# --------------------------------------------------

def check_proxy(
    proxy: str,
    test_url: str,
    retries: int,
    timeout: int,
    base_delay: float,
    https_only: bool,
    max_latency: Optional[float],
    logger: logging.Logger,
) -> Optional[str]:

    proxy_cfg = normalize_proxy(proxy, https_only=https_only)
    if not proxy_cfg:
        return None

    session = get_session()

    for attempt in range(1, retries + 1):
        try:
            start = time.perf_counter()

            response = session.get(
                test_url,
                proxies=proxy_cfg,
                timeout=timeout,
            )

            elapsed = time.perf_counter() - start

            if response.status_code == 200:
                if max_latency and elapsed > max_latency:
                    logger.debug("SLOW %.2fs | %s", elapsed, proxy)
                    return None

                logger.debug("OK %.2fs | %s", elapsed, proxy)
                return proxy

        except requests.RequestException as e:
            logger.debug("[%d/%d] FAIL %s | %s", attempt, retries, proxy, e)

        delay = base_delay * (2 ** (attempt - 1))
        delay *= 1 + random.random() * 0.3
        time.sleep(delay)

    return None


def validate_proxies(
    proxies: List[str],
    test_url: str,
    max_workers: int,
    retries: int,
    timeout: int,
    delay: float,
    https_only: bool,
    max_latency: Optional[float],
    logger: logging.Logger,
) -> List[str]:

    if not proxies:
        return []

    total = len(proxies)
    workers = min(max_workers, total, MAX_GLOBAL_WORKERS)

    logger.info("Checking %d proxies using %d threads", total, workers)

    valid: List[str] = []
    stop_event = threading.Event()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures: List[Future] = [
            executor.submit(
                check_proxy,
                proxy,
                test_url,
                retries,
                timeout,
                delay,
                https_only,
                max_latency,
                logger,
            )
            for proxy in proxies
        ]

        try:
            with tqdm(total=total, unit="proxy", ncols=100) as bar:
                for future in as_completed(futures):
                    if stop_event.is_set():
                        break

                    try:
                        result = future.result()
                        if result:
                            valid.append(result)
                    except Exception as e:
                        logger.debug("Worker crashed: %s", e)

                    bar.set_postfix(valid=len(valid))
                    bar.update(1)

        except KeyboardInterrupt:
            stop_event.set()
            logger.warning("Interrupted by user. Cancelling tasks...")
            for f in futures:
                f.cancel()

    logger.info("Done: %d / %d valid", len(valid), total)
    return valid


# --------------------------------------------------
# Main
# --------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Fast multithreaded proxy checker")
    parser.add_argument("input_file")
    parser.add_argument("output_file")
    parser.add_argument("--test_url", default="https://httpbin.org/ip")
    parser.add_argument("--max_workers", type=int, default=100)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--https_only", action="store_true")
    parser.add_argument("--max_latency", type=float)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    logger = setup_logger(args.verbose)

    start = time.perf_counter()

    proxies = read_proxies(args.input_file, logger)
    if not proxies:
        logger.warning("No proxies to check.")
        return

    valid = validate_proxies(
        proxies=proxies,
        test_url=args.test_url,
        max_workers=args.max_workers,
        retries=args.retries,
        timeout=args.timeout,
        delay=args.delay,
        https_only=args.https_only,
        max_latency=args.max_latency,
        logger=logger,
    )

    write_proxies(args.output_file, valid, logger)

    logger.info("Finished in %.2fs", time.perf_counter() - start)


if __name__ == "__main__":
    main()
