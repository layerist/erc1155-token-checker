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

# Disable SSL warnings globally
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
        "DEBUG": "\033[37m",   # Gray
        "INFO": "\033[36m",    # Cyan
        "WARNING": "\033[33m", # Yellow
        "ERROR": "\033[31m",   # Red
        "CRITICAL": "\033[41m" # Red background
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.RESET)
        message = super().format(record)
        return f"{color}{message}{self.RESET}"


def setup_logger(verbose: bool = False) -> logging.Logger:
    """Set up a colored logger with configurable verbosity."""
    logger = logging.getLogger("ProxyChecker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter("%(asctime)s | %(levelname)-8s | %(message)s"))
    if not logger.hasHandlers():
        logger.addHandler(handler)
    return logger


# ------------------- I/O -------------------
def read_proxies(file_path: str, logger: logging.Logger) -> List[str]:
    """Read and deduplicate proxy list from file."""
    path = Path(file_path)
    if not path.is_file():
        logger.error(f"File not found: {file_path}")
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            proxies = sorted(set(line.strip() for line in f if line.strip()))
        logger.info(f"Loaded {len(proxies)} unique proxies from '{file_path}'")
        return proxies
    except Exception as e:
        logger.exception(f"Failed to read proxies: {e}")
        return []


def write_proxies(file_path: str, proxies: List[str], logger: logging.Logger) -> None:
    """Save valid proxies to file."""
    try:
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(proxies))
        logger.info(f"Saved {len(proxies)} working proxies to '{file_path}'")
    except Exception as e:
        logger.exception(f"Failed to write proxies: {e}")


# ------------------- Proxy Handling -------------------
def parse_proxy(proxy: str) -> Dict[str, str]:
    """Normalize and return proxy dictionary for requests."""
    if "://" not in proxy:
        # Auto-detect protocol
        if proxy.startswith("socks5"):
            proxy = "socks5h://" + proxy.split("socks5")[-1].lstrip("://")
        elif proxy.startswith("socks4"):
            proxy = "socks4://" + proxy.split("socks4")[-1].lstrip("://")
        else:
            proxy = "http://" + proxy
    return {"http": proxy, "https": proxy}


# ------------------- Proxy Checking -------------------
def check_proxy(
    proxy: str,
    test_url: str,
    retries: int,
    timeout: int,
    logger: logging.Logger,
    delay: float,
) -> Optional[str]:
    """Test a single proxy for availability and responsiveness."""
    proxy_conf = parse_proxy(proxy)

    for attempt in range(1, retries + 1):
        try:
            start = time.perf_counter()
            resp = requests.get(
                test_url,
                proxies=proxy_conf,
                headers=HEADERS,
                timeout=timeout,
                verify=False,
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                logger.info(f"✔ OK: {proxy} | {elapsed:.2f}s")
                return proxy
            else:
                logger.debug(f"[{attempt}/{retries}] Bad response {resp.status_code} | {proxy}")
        except requests.RequestException as e:
            logger.debug(f"[{attempt}/{retries}] Failed {proxy}: {e}")
        # Exponential backoff + random jitter
        time.sleep(delay * (attempt ** 1.2) * (1 + random.random() * 0.3))
    return None


def validate_proxies(
    proxies: List[str],
    test_url: str,
    max_workers: int,
    retries: int,
    timeout: int,
    logger: logging.Logger,
    delay: float = 0.3,
) -> List[str]:
    """Validate proxies concurrently using ThreadPoolExecutor."""
    valid = []
    total = len(proxies)

    logger.info(f"Starting validation of {total} proxies with {max_workers} threads")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_proxy = {
            executor.submit(check_proxy, proxy, test_url, retries, timeout, logger, delay): proxy
            for proxy in proxies
        }

        with tqdm(total=total, desc="Checking proxies", ncols=100, unit="proxy") as pbar:
            for future in as_completed(future_to_proxy):
                proxy = future_to_proxy[future]
                try:
                    result = future.result()
                    if result:
                        valid.append(result)
                    pbar.set_postfix_str(f"Valid: {len(valid)}/{total}")
                except Exception as e:
                    logger.debug(f"Unhandled error for {proxy}: {e}")
                finally:
                    pbar.update(1)

    logger.info(f"Validation complete: {len(valid)} valid proxies")
    return valid


# ------------------- Main -------------------
def main():
    parser = argparse.ArgumentParser(description="Fast multithreaded proxy checker")
    parser.add_argument("input_file", help="File with proxy list (one per line)")
    parser.add_argument("output_file", help="Destination file for valid proxies")
    parser.add_argument("--test_url", default="http://httpbin.org/ip", help="URL to test proxies against")
    parser.add_argument("--max_workers", type=int, default=50, help="Number of concurrent threads")
    parser.add_argument("--retries", type=int, default=3, help="Number of retry attempts per proxy")
    parser.add_argument("--timeout", type=int, default=5, help="Request timeout in seconds")
    parser.add_argument("--delay", type=float, default=0.3, help="Base delay between retries")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logging")

    args = parser.parse_args()
    logger = setup_logger(args.verbose)

    start_time = time.perf_counter()
    try:
        proxies = read_proxies(args.input_file, logger)
        if not proxies:
            logger.warning("No proxies found. Exiting.")
            return

        valid = validate_proxies(
            proxies, args.test_url, args.max_workers, args.retries, args.timeout, logger, args.delay
        )

        write_proxies(args.output_file, valid, logger)

        duration = time.perf_counter() - start_time
        logger.info(f"Done in {duration:.2f}s | {len(valid)} valid of {len(proxies)} total")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
