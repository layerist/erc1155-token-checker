import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Dict

import requests
import urllib3
from tqdm import tqdm

# Suppress SSL warnings for test environments
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logger = logging.getLogger("ProxyChecker")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
with logging.StreamHandler() as console_handler:
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

def read_proxies(file_path: str) -> List[str]:
    """Read proxies from a file, one per line."""
    path = Path(file_path)
    if not path.exists():
        logger.error(f"Input file does not exist: {file_path}")
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            proxies = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(proxies)} proxies from {file_path}")
        return proxies
    except Exception as e:
        logger.exception(f"Failed to read proxies from {file_path}: {e}")
        return []

def parse_proxy(proxy: str) -> Optional[Dict[str, str]]:
    """Parse a proxy string into requests-compatible format."""
    parts = proxy.strip().split(":")
    try:
        if len(parts) == 2:
            ip, port = parts
            proxy_url = f"http://{ip}:{port}"
        elif len(parts) == 4:
            ip, port, user, pwd = parts
            proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
        else:
            logger.warning(f"Invalid proxy format: {proxy}")
            return None
        return {"http": proxy_url, "https": proxy_url}
    except Exception as e:
        logger.debug(f"Error parsing proxy '{proxy}': {e}")
        return None

def check_proxy(proxy: str, test_url: str, retries: int, timeout: int, retry_delay: float = 0.5) -> Optional[str]:
    """Check if a proxy is valid by making an HTTP GET request."""
    proxy_dict = parse_proxy(proxy)
    if not proxy_dict:
        return None
    for attempt in range(1, retries + 1):
        try:
            start = time.time()
            response = requests.get(test_url, proxies=proxy_dict, timeout=timeout, verify=False)
            elapsed = time.time() - start
            if response.status_code == 200:
                ip_info = response.json().get("origin", "Unknown")
                logger.info(f"✓ Valid proxy: {proxy} (IP: {ip_info}) [{elapsed:.2f}s]")
                return proxy
            else:
                logger.debug(f"Attempt {attempt}/{retries} failed with status {response.status_code} for proxy {proxy}")
        except requests.RequestException as e:
            logger.debug(f"Attempt {attempt}/{retries} failed for proxy {proxy}: {e}")
        time.sleep(retry_delay)
    logger.debug(f"All attempts failed for proxy: {proxy}")
    return None

def write_proxies(file_path: str, proxies: List[str]) -> None:
    """Write valid proxies to a file."""
    try:
        with Path(file_path).open("w", encoding="utf-8") as f:
            f.writelines(f"{proxy}\n" for proxy in proxies)
        logger.info(f"Saved {len(proxies)} valid proxies to {file_path}")
    except Exception as e:
        logger.exception(f"Failed to write to {file_path}: {e}")

def validate_proxies(proxies: List[str], test_url: str, max_workers: int, retries: int, timeout: int) -> List[str]:
    """Validate a list of proxies concurrently."""
    valid_proxies = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_proxy, proxy, test_url, retries, timeout): proxy for proxy in proxies}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Checking proxies"):
            try:
                result = future.result()
                if result:
                    valid_proxies.append(result)
            except Exception as e:
                logger.debug(f"Unexpected error in proxy check: {e}")
    return valid_proxies

def main():
    parser = argparse.ArgumentParser(description="Multithreaded proxy checker.")
    parser.add_argument("input_file", help="Path to input proxy list.")
    parser.add_argument("output_file", help="Path to save valid proxies.")
    parser.add_argument("--test_url", default="http://httpbin.org/ip", help="URL to test proxies.")
    parser.add_argument("--max_workers", type=int, default=10, help="Number of concurrent threads (default: 10).")
    parser.add_argument("--retries", type=int, default=3, help="Retry attempts per proxy (default: 3).")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout in seconds for requests (default: 5).")
    args = parser.parse_args()

    if args.max_workers < 1:
        logger.error("max_workers must be at least 1.")
        return

    start_time = time.time()
    proxies = read_proxies(args.input_file)
    if not proxies:
        logger.error("No proxies to check. Exiting.")
        return

    valid_proxies = validate_proxies(proxies, args.test_url, args.max_workers, args.retries, args.timeout)
    write_proxies(args.output_file, valid_proxies)
    elapsed = time.time() - start_time
    logger.info(f"Finished in {elapsed:.2f}s — Valid proxies: {len(valid_proxies)} / {len(proxies)}")

if __name__ == "__main__":
    main()
