import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from pathlib import Path
import time
import urllib3
from typing import List, Optional, Dict

# Suppress warnings from urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging with timestamps, levels, and improved formatting
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

def read_proxies(file_path: str) -> List[str]:
    """
    Load proxy list from a file.
    Args:
        file_path (str): Path to the proxy list file.
    Returns:
        List[str]: A list of proxy strings.
    """
    try:
        path = Path(file_path)
        if not path.is_file():
            logging.error(f"File not found: {file_path}")
            return []

        with path.open("r", encoding="utf-8") as file:
            proxies = [line.strip() for line in file if line.strip()]
        logging.info(f"Loaded {len(proxies)} proxies from {file_path}")
        return proxies
    except Exception as e:
        logging.exception(f"Failed to read proxies from {file_path}: {e}")
        return []

def parse_proxy(proxy: str) -> Optional[Dict[str, str]]:
    """
    Parse proxy into a dictionary compatible with requests.
    Args:
        proxy (str): Proxy string in ip:port[:username:password] format.
    Returns:
        Optional[Dict[str, str]]: A dictionary for requests or None if invalid.
    """
    parts = proxy.split(":")
    if len(parts) == 2:
        ip, port = parts
        proxy_url = f"http://{ip}:{port}"
    elif len(parts) == 4:
        ip, port, username, password = parts
        proxy_url = f"http://{username}:{password}@{ip}:{port}"
    else:
        logging.warning(f"Invalid proxy format: {proxy}")
        return None

    return {"http": proxy_url, "https": proxy_url}

def check_proxy(proxy: str, retries: int = 3, timeout: int = 5) -> Optional[str]:
    """
    Test if a proxy is functional.
    Args:
        proxy (str): Proxy string in ip:port[:username:password] format.
        retries (int): Number of retry attempts.
        timeout (int): Request timeout in seconds.
    Returns:
        Optional[str]: The proxy string if valid, None otherwise.
    """
    proxies = parse_proxy(proxy)
    if not proxies:
        return None

    test_url = "http://httpbin.org/ip"
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(test_url, proxies=proxies, timeout=timeout, verify=False)
            if response.status_code == 200:
                logging.info(f"Valid proxy: {proxy} (IP: {response.json().get('origin')})")
                return proxy
        except requests.RequestException as e:
            logging.debug(f"Proxy {proxy} failed on attempt {attempt}/{retries}: {e}")

    logging.info(f"Invalid proxy: {proxy}")
    return None

def write_proxies(file_path: str, proxies: List[str]) -> None:
    """
    Save working proxies to a file.
    Args:
        file_path (str): Destination file path.
        proxies (List[str]): List of functional proxies.
    """
    try:
        path = Path(file_path)
        with path.open("w", encoding="utf-8") as file:
            file.writelines(f"{proxy}\n" for proxy in proxies)
        logging.info(f"Saved {len(proxies)} working proxies to {file_path}")
    except Exception as e:
        logging.exception(f"Failed to write proxies to {file_path}: {e}")

def main(input_file: str, output_file: str, max_workers: int = 10, retries: int = 3, timeout: int = 5) -> None:
    """
    Main workflow: load, check, and save proxies.
    Args:
        input_file (str): Input file path.
        output_file (str): Output file path.
        max_workers (int): Number of parallel threads.
        retries (int): Retry attempts for each proxy.
        timeout (int): Request timeout per proxy in seconds.
    """
    start_time = time.time()

    proxies = read_proxies(input_file)
    if not proxies:
        logging.error("No proxies to process. Exiting.")
        return

    working_proxies = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_proxy = {executor.submit(check_proxy, proxy, retries, timeout): proxy for proxy in proxies}

        for future in as_completed(future_to_proxy):
            try:
                result = future.result()
                if result:
                    working_proxies.append(result)
            except Exception as e:
                logging.error(f"Unexpected error during proxy validation: {e}")

    write_proxies(output_file, working_proxies)
    elapsed_time = time.time() - start_time
    logging.info(f"Completed in {elapsed_time:.2f} seconds. Valid proxies: {len(working_proxies)}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Proxy checker and filter.")
    parser.add_argument("input_file", help="Path to input file containing proxies.")
    parser.add_argument("output_file", help="Path to output file for valid proxies.")
    parser.add_argument("--max_workers", type=int, default=10, help="Number of parallel threads (default: 10).")
    parser.add_argument("--retries", type=int, default=3, help="Retry attempts per proxy (default: 3).")
    parser.add_argument("--timeout", type=int, default=5, help="Request timeout in seconds (default: 5).")

    args = parser.parse_args()

    if args.max_workers < 1:
        logging.error("Max workers must be at least 1. Exiting.")
    else:
        main(args.input_file, args.output_file, args.max_workers, args.retries, args.timeout)
