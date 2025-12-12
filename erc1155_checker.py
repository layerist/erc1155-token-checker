import argparse
import logging
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Dict

import requests
import urllib3
from tqdm import tqdm

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/115.0.0.0 Safari/537.36"
    )
}

# ------------------- Logging -------------------
class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[37m",
        "INFO": "\033[36m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[41m",
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        message = super().format(record)
        return f"{color}{message}{self.RESET}"

def setup_logger(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("ProxyChecker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter("%(asctime)s | %(levelname)-8s | %(message)s"))

    if not logger.handlers:
        logger.addHandler(handler)
    logger.propagate = False

    return logger

# ------------------- Proxy Utilities -------------------
def normalize_proxy(proxy: str) -> Optional[Dict[str, str]]:
    """Normalize proxy into requests format. Returns None if malformed."""
    proxy = proxy.strip()

    if not proxy:
        return None

    if "://" not in proxy:
        if proxy.startswith(("socks5", "socks5h")):
            proxy = "socks5h://" + proxy.split("socks5")[-1].lstrip(":/")
        elif proxy.startswith("socks4"):
            proxy = "socks4://" + proxy.split("socks4")[-1].lstrip(":/")
        else:
            proxy = "http://" + proxy

    return {"http": proxy, "https": proxy}

def read_proxies(file_path: str, logger: logging.Logger) -> List[str]:
    """Reads proxies from file and normalizes them."""
    path = Path(file_path)
    if not path.is_file():
        logger.error(f"File not found: {file_path}")
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            proxies = sorted(set(
                line.strip() for line in f if line.strip() and normalize_proxy(line.strip())
            ))
        logger.info(f"Loaded {len(proxies)} unique proxies")
        return proxies
    except Exception as e:
        logger.exception(f"Failed to read proxies: {e}")
        return []

def write_proxies(file_path: str, proxies: List[str], logger: logging.Logger) -> None:
    """Write valid proxies to file."""
    try:
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(proxies))
        logger.info(f"Saved {len(proxies)} proxies to '{file_path}'")
    except Exception as e:
        logger.exception(f"Failed to write proxies: {e}")

# ------------------- Proxy Checking -------------------
_SESSION = requests.Session()
_SESSION.verify = False
_SESSION.headers.update(HEADERS)

def check_proxy(
    proxy: str,
    test_url: str,
    retries: int,
    timeout: int,
    delay: float,
    logger: logging.Logger,
) -> Optional[str]:
    """Test proxy with retry logic and exponential backoff."""
    proxy_conf = normalize_proxy(proxy)
    if not proxy_conf:
        return None

    for attempt in range(1, retries + 1):
        try:
            start = time.perf_counter()
            resp = _SESSION.get(test_url, proxies=proxy_conf, timeout=timeout)
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                logger.debug(f"OK: {proxy} | {elapsed:.2f}s")
                return proxy

            logger.debug(f"[{attempt}/{retries}] Bad status {resp.status_code} | {proxy}")
        except Exception as e:
            logger.debug(f"[{attempt}/{retries}] Fail {proxy}: {e}")

        # Backoff + jitter
        backoff = delay * (attempt ** 1.5) * (1 + random.random() * 0.35)
        time.sleep(backoff)

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
    """Validate proxies in parallel with adaptive concurrency."""
    valid = []
    total = len(proxies)
    failures = 0

    logger.info(f"Validating {total} proxies with {max_workers} threads...")

    if total > 200:
        max_workers = min(max_workers, 150)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(check_proxy, proxy, test_url, retries, timeout, delay, logger): proxy
            for proxy in proxies
        }

        with tqdm(total=total, desc="Checking proxies", ncols=100, unit="proxy") as bar:
            for future in as_completed(futures):
                result = future.result()
                if result:
                    valid.append(result)
                else:
                    failures += 1

                bar.set_postfix(valid=len(valid))
                bar.update(1)

                if failures > total * 0.85 and logger.level > logging.DEBUG:
                    logger.setLevel(logging.WARNING)

    logger.info(f"Complete: {len(valid)} valid of {total}")
    return valid

# ------------------- Main -------------------
def main():
    parser = argparse.ArgumentParser(description="Fast multithreaded proxy checker")
    parser.add_argument("input_file", help="File with proxies")
    parser.add_argument("output_file", help="Where to save valid proxies")
    parser.add_argument("--test_url", default="http://httpbin.org/ip", help="URL to test against")
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
            logger.warning("No proxies found.")
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

        logger.info(f"Finished in {time.perf_counter() - start:.2f}s")
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
