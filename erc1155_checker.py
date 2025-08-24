import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Dict

import requests
import urllib3
from tqdm import tqdm

# Suppress SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logger
logger = logging.getLogger("ProxyChecker")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logger.addHandler(handler)

# Default request headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
}


def read_proxies(file_path: str) -> List[str]:
    """Load proxies from file, remove duplicates and empty lines."""
    path = Path(file_path)
    if not path.is_file():
        logger.error(f"File not found: {file_path}")
        return []

    try:
        with path.open("r", encoding="utf-8") as file:
            proxies = list({line.strip() for line in file if line.strip()})
        logger.info(f"Loaded {len(proxies)} unique proxies from '{file_path}'")
        return proxies
    except Exception as e:
        logger.exception(f"Failed to read proxies: {e}")
        return []


def parse_proxy(proxy: str) -> Optional[Dict[str, str]]:
    """Parse proxy string into requests-compatible format."""
    try:
        parts = proxy.split(":")
        if len(parts) == 2:  # ip:port
            ip, port = parts
            url = f"http://{ip}:{port}"
        elif len(parts) == 4:  # ip:port:user:pass
            ip, port, user, pwd = parts
            url = f"http://{user}:{pwd}@{ip}:{port}"
        else:
            logger.warning(f"Invalid proxy format: {proxy}")
            return None
        return {"http": url, "https": url}
    except Exception as e:
        logger.debug(f"Error parsing proxy '{proxy}': {e}")
        return None


def check_proxy(proxy: str, test_url: str, retries: int, timeout: int, delay: float = 0.5) -> Optional[str]:
    """Check if a single proxy is functional."""
    proxy_conf = parse_proxy(proxy)
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
                ip = response.json().get("origin", "unknown")
                logger.info(f"✔ Proxy OK: {proxy} | IP: {ip} | Time: {duration:.2f}s")
                return proxy
            else:
                logger.debug(f"[{attempt}/{retries}] Bad response {response.status_code} from {proxy}")
        except requests.RequestException as e:
            logger.debug(f"[{attempt}/{retries}] Failed: {proxy} | Error: {e}")
        time.sleep(delay)
    return None


def write_proxies(file_path: str, proxies: List[str]) -> None:
    """Write working proxies to a file."""
    try:
        with Path(file_path).open("w", encoding="utf-8") as file:
            for p in proxies:
                file.write(p + "\n")
        logger.info(f"Saved {len(proxies)} valid proxies to '{file_path}'")
    except Exception as e:
        logger.exception(f"Failed to save proxies: {e}")


def validate_proxies(
    proxies: List[str],
    test_url: str,
    max_workers: int,
    retries: int,
    timeout: int,
) -> List[str]:
    """Check proxies concurrently using a thread pool."""
    valid = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(check_proxy, proxy, test_url, retries, timeout): proxy
            for proxy in proxies
        }
        for future in tqdm(as_completed(future_map), total=len(future_map), desc="Validating", ncols=100):
            try:
                result = future.result()
                if result:
                    valid.append(result)
            except Exception as e:
                logger.debug(f"Unhandled exception for {future_map[future]} | Error: {e}")
    return valid


def main():
    parser = argparse.ArgumentParser(description="Fast multithreaded proxy checker")
    parser.add_argument("input_file", help="File with proxies (one per line)")
    parser.add_argument("output_file", help="File to save valid proxies")
    parser.add_argument("--test_url", default="http://httpbin.org/ip", help="Test URL (default: http://httpbin.org/ip)")
    parser.add_argument("--max_workers", type=int, default=20, help="Number of threads (default: 20)")
    parser.add_argument("--retries", type=int, default=3, help="Retries per proxy (default: 3)")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout per request in seconds (default: 5)")

    args = parser.parse_args()

    if args.max_workers < 1:
        logger.error("max_workers must be >= 1")
        return

    start = time.perf_counter()

    try:
        proxies = read_proxies(args.input_file)
        if not proxies:
            logger.warning("No proxies to check.")
            return

        valid = validate_proxies(proxies, args.test_url, args.max_workers, args.retries, args.timeout)
        write_proxies(args.output_file, valid)

        elapsed = time.perf_counter() - start
        logger.info(f"Completed in {elapsed:.2f}s | {len(valid)} valid out of {len(proxies)}")
    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")


if __name__ == "__main__":
    main()
