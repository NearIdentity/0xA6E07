"""Render a web page in a headless browser and convert its HTML to Markdown.

Setup:
    pip install playwright markdownify beautifulsoup4
    playwright install chromium
"""

import sys

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.sync_api import sync_playwright


def url_to_markdown(url: str, timeout_ms: int = 30_000) -> str:
    """Fetch `url` with headless Chromium (so JS-rendered content is included)
    and return the page as a Markdown string."""
    with sync_playwright() as p:
        launch_args = {"headless": True}
        # Honour a pre-configured executable if Playwright's own isn't installed.
        import os
        if os.environ.get("CHROMIUM_PATH"):
            launch_args["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = p.chromium.launch(**launch_args)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            html = page.content()
        finally:
            browser.close()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    root = soup.body or soup
    title = soup.title.get_text(strip=True) if soup.title else ""
    markdown = md(str(root), heading_style="ATX", bullets="-")

    # Collapse runs of blank lines.
    lines, blank = [], 0
    for line in markdown.splitlines():
        line = line.rstrip()
        blank = blank + 1 if not line else 0
        if blank <= 1:
            lines.append(line)
    body = "\n".join(lines).strip()

    return f"# {title}\n\n{body}\n" if title else body + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <url>")
    print(url_to_markdown(sys.argv[1]))
