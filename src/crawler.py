"""Crawl a site from a root URL and yield every URL nested under that root.

Setup:
    pip install playwright
    playwright install chromium
"""

import os
import sys
from collections import deque
from typing import Iterator
from urllib.parse import urldefrag, urljoin, urlparse

from playwright.sync_api import sync_playwright


def _normalize(url: str) -> str:
    """Drop the fragment and any trailing slash (except for the bare root)."""
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(path=path).geturl()


def crawl(root_url: str, max_pages: int = 500, timeout_ms: int = 1000) -> Iterator[str]:
    """Breadth-first crawl starting at `root_url`, yielding each distinct URL
    on the same host whose path is at or below the root's path.

    A headless browser is used so links added by JavaScript are discovered too.
    """
    if "://" not in root_url:
        root_url = "https://" + root_url
    root = urlparse(_normalize(root_url))
    root_prefix = root.path.rstrip("/")

    def in_scope(url: str) -> bool:
        u = urlparse(url)
        return (
            u.scheme in ("http", "https")
            and u.netloc == root.netloc
            and (u.path == root_prefix or u.path.startswith(root_prefix + "/"))
        )

    start = _normalize(root_url)
    seen = {start}
    queue = deque([start])
    count = 0

    with sync_playwright() as p:
        launch_args = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            launch_args["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page()
        try:
            while queue and count < max_pages:
                url = queue.popleft()
                try:
                    response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except Exception:
                    continue  # unreachable page: skip it
                if response is None or response.status >= 400:
                    continue
                count += 1
                yield url

                # Only parse HTML pages for further links.
                if "html" not in (response.headers.get("content-type") or ""):
                    continue
                hrefs = page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.getAttribute('href'))"
                )
                for href in hrefs:
                    link = _normalize(urljoin(page.url, href))
                    if link not in seen and in_scope(link):
                        seen.add(link)
                        queue.append(link)
        finally:
            browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <root-url>")
    for found in crawl(sys.argv[1]):
        print(found)
