import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Dict

import requests
import urllib3
from tqdm import tqdm

# Disable SSL verification warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Setup logging
logger = logging.getLogger("ProxyChecker")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logger.addHandler(handler)


def read_proxies(file_path: str) -> List[str]:
    """Reads proxy list from a file."""
    path = Path(file_path)
    if not path.is_file():
        logger.error(f"Input file not found: {file_path}")
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            proxies = list({line.strip() for line in f if line.strip()})
        logger.info(f"Loaded {len(proxies)} unique proxies from {file_path}")
        return proxies
    except Exception as e:
        logger.exception(f"Failed to read proxies: {e}")
        return []


def parse_proxy(proxy: str) -> Optional[Dict[str, str]]:
    """Parses a proxy string into requests format."""
    parts = proxy.split(":")
    try:
        if len(parts) == 2:
            ip, port = parts
            url = f"http://{ip}:{port}"
        elif len(parts) == 4:
            ip, port, user, pwd = parts
            url = f"http://{user}:{pwd}@{ip}:{port}"
        else:
            logger.warning(f"Invalid proxy format: {proxy}")
            return None
        return {"http": url, "https": url}
    except Exception as e:
        logger.debug(f"Failed to parse proxy: {proxy} | Error: {e}")
        return None


def check_proxy(proxy: str, test_url: str, retries: int, timeout: int, delay: float = 0.5) -> Optional[str]:
    """Tests a single proxy for connectivity."""
    proxy_config = parse_proxy(proxy)
    if not proxy_config:
        return None

    for attempt in range(1, retries + 1):
        try:
            start_time = time.time()
            response = requests.get(test_url, proxies=proxy_config, timeout=timeout, verify=False)
            elapsed = time.time() - start_time
            if response.status_code == 200:
                ip = response.json().get("origin", "Unknown")
                logger.info(f"✔ Valid: {proxy} | IP: {ip} | Time: {elapsed:.2f}s")
                return proxy
            else:
                logger.debug(f"[{attempt}/{retries}] Bad status: {response.status_code} for {proxy}")
        except requests.RequestException as e:
            logger.debug(f"[{attempt}/{retries}] Request failed for {proxy}: {e}")
        time.sleep(delay)
    return None


def write_proxies(file_path: str, proxies: List[str]) -> None:
    """Writes valid proxies to a file."""
    try:
        with Path(file_path).open("w", encoding="utf-8") as f:
            f.writelines(f"{p}\n" for p in proxies)
        logger.info(f"Saved {len(proxies)} valid proxies to {file_path}")
    except Exception as e:
        logger.exception(f"Failed to write output file: {e}")


def validate_proxies(proxies: List[str], test_url: str, workers: int, retries: int, timeout: int) -> List[str]:
    """Validates proxies using a thread pool."""
    valid = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(check_proxy, p, test_url, retries, timeout): p for p in proxies}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Validating proxies", ncols=80):
            try:
                result = future.result()
                if result:
                    valid.append(result)
            except Exception as e:
                logger.debug(f"Error in future: {e}")
    return valid


def main():
    parser = argparse.ArgumentParser(description="Multithreaded Proxy Checker")
    parser.add_argument("input_file", help="Path to proxy list")
    parser.add_argument("output_file", help="Path to save valid proxies")
    parser.add_argument("--test_url", default="http://httpbin.org/ip", help="URL to test against")
    parser.add_argument("--max_workers", type=int, default=20, help="Threads (default: 20)")
    parser.add_argument("--retries", type=int, default=3, help="Retries per proxy (default: 3)")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout in seconds (default: 5)")
    args = parser.parse_args()

    if args.max_workers < 1:
        logger.error("max_workers must be at least 1")
        return

    start = time.time()

    try:
        proxies = read_proxies(args.input_file)
        if not proxies:
            logger.warning("No proxies to validate.")
            return

        valid = validate_proxies(proxies, args.test_url, args.max_workers, args.retries, args.timeout)
        write_proxies(args.output_file, valid)

        elapsed = time.time() - start
        logger.info(f"Completed in {elapsed:.2f}s | {len(valid)} valid of {len(proxies)} checked")
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")


if __name__ == "__main__":
    main()
