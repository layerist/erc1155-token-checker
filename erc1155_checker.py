#!/usr/bin/env python3
"""
Ultra-fast multithreaded proxy checker (improved)
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
BATCH_SIZE = 1000  # prevents huge memory spikes


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
# Thread-local session
# --------------------------------------------------

_thread_local = threading.local()


def get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.verify = False
        session.headers.update(DEFAULT_HEADERS)

        adapter = HTTPAdapter(
            pool_connections=300,
            pool_maxsize=300,
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

    # auto-detect scheme
    if "://" not in proxy:
        proxy = "http://" + proxy

    if https_only and not proxy.startswith("https://"):
        return None

    return {
        "http": proxy,
        "https": proxy,
    }


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
# Proxy check
# --------------------------------------------------

def check_proxy(
    proxy: str,
    url: str,
    retries: int,
    timeout: int,
    delay: float,
    https_only: bool,
    max_latency: Optional[float],
) -> Optional[str]:

    proxy_cfg = normalize_proxy(proxy, https_only)
    if not proxy_cfg:
        return None

    session = get_session()

    for attempt in range(retries):

        try:
            start = time.perf_counter()

            # Try HEAD first (faster)
            r = session.head(
                url,
                proxies=proxy_cfg,
                timeout=(timeout, timeout),
                allow_redirects=False,
            )

            # fallback to GET if HEAD fails
            if r.status_code >= 400:
                r = session.get(
                    url,
                    proxies=proxy_cfg,
                    timeout=(timeout, timeout),
                    stream=False,
                )

            latency = time.perf_counter() - start

            if r.status_code == 200:
                if max_latency and latency > max_latency:
                    return None
                return proxy

        except requests.RequestException:
            pass

        # smarter backoff
        sleep_time = delay * (2 ** attempt) * random.uniform(0.7, 1.3)
        time.sleep(sleep_time)

    return None


# --------------------------------------------------
# Validation (batched for performance)
# --------------------------------------------------

def validate_proxies(
    proxies: List[str],
    url: str,
    workers: int,
    retries: int,
    timeout: int,
    delay: float,
    https_only: bool,
    max_latency: Optional[float],
) -> List[str]:

    total = len(proxies)
    workers = min(workers, total, MAX_GLOBAL_WORKERS)

    valid: List[str] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        with tqdm(total=total, unit="proxy", ncols=100) as bar:

            # process in batches (important!)
            for i in range(0, total, BATCH_SIZE):
                batch = proxies[i:i + BATCH_SIZE]

                futures = [
                    executor.submit(
                        check_proxy,
                        proxy,
                        url,
                        retries,
                        timeout,
                        delay,
                        https_only,
                        max_latency,
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
    parser = argparse.ArgumentParser(description="Ultra-fast proxy checker")

    parser.add_argument("input_file")
    parser.add_argument("output_file")

    parser.add_argument("--test_url", default="https://httpbin.org/ip")
    parser.add_argument("--workers", type=int, default=300)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--https_only", action="store_true")
    parser.add_argument("--max_latency", type=float)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    logger = setup_logger(args.verbose)

    start = time.perf_counter()

    proxies = read_proxies(args.input_file, logger)
    if not proxies:
        return

    valid = validate_proxies(
        proxies,
        args.test_url,
        args.workers,
        args.retries,
        args.timeout,
        args.delay,
        args.https_only,
        args.max_latency,
    )

    write_proxies(args.output_file, valid, logger)

    logger.info("Finished in %.2fs", time.perf_counter() - start)


if __name__ == "__main__":
    main()
