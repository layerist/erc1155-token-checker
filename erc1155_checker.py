import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Dict

import requests
import urllib3
from tqdm import tqdm

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Default request headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/115.0.0.0 Safari/537.36"
    )
}


# ------------------- Logging -------------------
def setup_logger(verbose: bool = False) -> logging.Logger:
    """Configure and return a logger instance."""
    logger = logging.getLogger("ProxyChecker")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))
    if not logger.hasHandlers():
        logger.addHandler(handler)
    return logger


# ------------------- I/O -------------------
def read_proxies(file_path: str, logger: logging.Logger) -> List[str]:
    """Load proxies from file and remove duplicates."""
    path = Path(file_path)
    if not path.is_file():
        logger.error(f"File not found: {file_path}")
        return []

    try:
        with path.open("r", encoding="utf-8") as f:
            proxies = list({line.strip() for line in f if line.strip()})
        logger.info(f"Loaded {len(proxies)} unique proxies from '{file_path}'")
        return proxies
    except Exception as e:
        logger.exception(f"Failed to read proxies: {e}")
        return []


def write_proxies(file_path: str, proxies: List[str], logger: logging.Logger) -> None:
    """Write valid proxies to a file."""
    try:
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(proxies))
        logger.info(f"Saved {len(proxies)} working proxies to '{file_path}'")
    except Exception as e:
        logger.exception(f"Failed to save proxies: {e}")


# ------------------- Proxy Parsing -------------------
def parse_proxy(proxy: str, logger: logging.Logger) -> Optional[Dict[str, str]]:
    """
    Convert a proxy string into a format usable by requests.
    Supports both HTTP and SOCKS (socks4/socks5).
    """
    try:
        if "://" not in proxy:
            proxy = "http://" + proxy

        # Validate structure (requests will handle socks if installed)
        return {"http": proxy, "https": proxy}
    except Exception as e:
        logger.debug(f"Error parsing proxy '{proxy}': {e}")
        return None


# ------------------- Proxy Checking -------------------
def check_proxy(
    proxy: str,
    test_url: str,
    retries: int,
    timeout: int,
    logger: logging.Logger,
    delay: float = 0.3,
) -> Optional[str]:
    """Check if a single proxy is functional, with retries and timing."""
    proxy_conf = parse_proxy(proxy, logger)
    if not proxy_conf:
        return None

    for attempt in range(1, retries + 1):
        try:
            start = time.perf_counter()
            response = requests.get(
                test_url,
                proxies=proxy_conf,
                headers=HEADERS,
                timeout=timeout,
                verify=False,
            )
            duration = time.perf_counter() - start

            if response.status_code == 200:
                logger.info(f"✔ OK: {proxy} | {duration:.2f}s")
                return proxy
            else:
                logger.debug(f"[{attempt}/{retries}] Bad response {response.status_code} | {proxy}")
        except requests.RequestException as e:
            logger.debug(f"[{attempt}/{retries}] Failed: {proxy} | {e}")
        # Exponential backoff for retries
        time.sleep(delay * (attempt ** 1.2))
    return None


def validate_proxies(
    proxies: List[str],
    test_url: str,
    max_workers: int,
    retries: int,
    timeout: int,
    logger: logging.Logger,
) -> List[str]:
    """Validate proxies concurrently with a progress bar."""
    valid = []
    total = len(proxies)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_proxy, proxy, test_url, retries, timeout, logger): proxy for proxy in proxies}

        with tqdm(total=total, desc="Checking proxies", ncols=100, unit="proxy") as pbar:
            for future in as_completed(futures):
                proxy = futures[future]
                try:
                    result = future.result()
                    if result:
                        valid.append(result)
                    pbar.set_postfix_str(f"Valid: {len(valid)}/{total}")
                except Exception as e:
                    logger.debug(f"Unhandled error for {proxy}: {e}")
                finally:
                    pbar.update(1)

    return valid


# ------------------- Main -------------------
def main():
    parser = argparse.ArgumentParser(description="High-performance multithreaded proxy checker.")
    parser.add_argument("input_file", help="File containing proxies (one per line)")
    parser.add_argument("output_file", help="File to save valid proxies")
    parser.add_argument("--test_url", default="http://httpbin.org/ip", help="URL used for testing proxies")
    parser.add_argument("--max_workers", type=int, default=50, help="Number of threads (default: 50)")
    parser.add_argument("--retries", type=int, default=3, help="Retries per proxy (default: 3)")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout for each request (default: 5)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    logger = setup_logger(args.verbose)

    if args.max_workers < 1:
        logger.error("max_workers must be >= 1")
        return

    start = time.perf_counter()
    try:
        proxies = read_proxies(args.input_file, logger)
        if not proxies:
            logger.warning("No proxies to validate.")
            return

        valid_proxies = validate_proxies(
            proxies, args.test_url, args.max_workers, args.retries, args.timeout, logger
        )

        write_proxies(args.output_file, valid_proxies, logger)

        elapsed = time.perf_counter() - start
        logger.info(f"Completed in {elapsed:.2f}s | {len(valid_proxies)} valid out of {len(proxies)}")
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
