#!/usr/bin/env python3
"""
Ultra-fast multithreaded proxy checker (optimized v2)
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
from requests.adapters import HTTPAdapter
from tqdm import tqdm

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

MAX_GLOBAL_WORKERS = 500
BATCH_SIZE = 1000

# multiple endpoints reduces bans / rate limits
TEST_URLS = [
    "https://httpbin.org/ip",
    "https://api.ipify.org?format=json",
    "https://icanhazip.com",
]


# --------------------------------------------------
# Logging
# --------------------------------------------------

def setup_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("proxy_checker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        logger.addHandler(handler)

    logger.propagate = False
    return logger


# --------------------------------------------------
# Thread-local session (IMPORTANT for speed)
# --------------------------------------------------

_thread_local = threading.local()


def get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.verify = False
        session.headers.update(DEFAULT_HEADERS)

        adapter = HTTPAdapter(
            pool_connections=500,
            pool_maxsize=500,
            max_retries=0,
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        _thread_local.session = session

    return _thread_local.session


# --------------------------------------------------
# Proxy utils
# --------------------------------------------------

def normalize_proxy(proxy: str, https_only: bool) -> Optional[Dict[str, str]]:
    proxy = proxy.strip()
    if not proxy:
        return None

    if "://" not in proxy:
        proxy = "http://" + proxy

    if https_only and not proxy.startswith("https://"):
        return None

    return {"http": proxy, "https": proxy}


def read_proxies(path: str, logger: logging.Logger) -> List[str]:
    file = Path(path)

    if not file.exists():
        logger.error("File not found: %s", path)
        return []

    with file.open("r", encoding="utf-8", errors="ignore") as f:
        proxies = list({line.strip() for line in f if line.strip()})

    logger.info("Loaded %d unique proxies", len(proxies))
    return proxies


def write_proxies(path: str, proxies: List[str], logger: logging.Logger):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(proxies), encoding="utf-8")

    logger.info("Saved %d proxies → %s", len(proxies), path)


# --------------------------------------------------
# Proxy check (FAST PATH OPTIMIZED)
# --------------------------------------------------

def check_proxy(
    proxy: str,
    retries: int,
    timeout: float,
    delay: float,
    https_only: bool,
    max_latency: Optional[float],
    validate_ip: bool,
) -> Optional[str]:

    proxy_cfg = normalize_proxy(proxy, https_only)
    if not proxy_cfg:
        return None

    session = get_session()
    url = random.choice(TEST_URLS)

    for attempt in range(retries):

        try:
            start = time.perf_counter()

            r = session.get(
                url,
                proxies=proxy_cfg,
                timeout=(timeout, timeout * 1.5),  # connect/read split
                stream=False,
            )

            latency = time.perf_counter() - start

            if r.status_code != 200:
                continue

            # latency filter
            if max_latency and latency > max_latency:
                return None

            # optional IP validation (detect fake proxies)
            if validate_ip:
                text = r.text.strip()
                if not text or len(text) > 100:
                    return None

            return proxy

        except (
            requests.ConnectTimeout,
            requests.ReadTimeout,
            requests.ProxyError,
            requests.SSLError,
        ):
            # hard fail → retry
            pass

        except requests.RequestException:
            # unknown error → skip retries faster
            return None

        # exponential backoff (fast)
        time.sleep(delay * (2 ** attempt) * random.uniform(0.8, 1.2))

    return None


# --------------------------------------------------
# Validation
# --------------------------------------------------

def validate_proxies(
    proxies: List[str],
    workers: int,
    retries: int,
    timeout: float,
    delay: float,
    https_only: bool,
    max_latency: Optional[float],
    validate_ip: bool,
) -> List[str]:

    total = len(proxies)
    workers = min(workers, total, MAX_GLOBAL_WORKERS)

    valid: List[str] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        with tqdm(total=total, unit="proxy", ncols=100) as bar:

            for i in range(0, total, BATCH_SIZE):
                batch = proxies[i:i + BATCH_SIZE]

                futures = [
                    executor.submit(
                        check_proxy,
                        proxy,
                        retries,
                        timeout,
                        delay,
                        https_only,
                        max_latency,
                        validate_ip,
                    )
                    for proxy in batch
                ]

                for future in as_completed(futures):
                    try:
                        res = future.result()
                        if res:
                            valid.append(res)
                    except Exception:
                        pass

                    bar.update(1)

    return valid


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ultra-fast proxy checker v2")

    parser.add_argument("input_file")
    parser.add_argument("output_file")

    parser.add_argument("--workers", type=int, default=300)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--https_only", action="store_true")
    parser.add_argument("--max_latency", type=float)
    parser.add_argument("--validate_ip", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    logger = setup_logger(args.verbose)

    start = time.perf_counter()

    proxies = read_proxies(args.input_file, logger)
    if not proxies:
        return

    valid = validate_proxies(
        proxies,
        args.workers,
        args.retries,
        args.timeout,
        args.delay,
        args.https_only,
        args.max_latency,
        args.validate_ip,
    )

    write_proxies(args.output_file, valid, logger)

    logger.info("Finished in %.2fs", time.perf_counter() - start)


if __name__ == "__main__":
    main()
