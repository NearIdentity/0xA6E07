"""Crawl a site from a root URL and yield every URL nested under that root.

The crawler obeys the site's robots.txt (RFC 9309): it will not visit URLs the
file disallows for this crawler, and it waits as long as the file's Crawl-delay
or Request-rate asks. It identifies itself with the product token
`DEFAULT_USER_AGENT`, so site owners can address it in robots.txt.

Setup:
    pip install playwright protego
    playwright install chromium
"""

import logging
import os
import sys
import time
from collections import deque
from typing import Iterator
from urllib.parse import urldefrag, urljoin, urlparse

from playwright.sync_api import sync_playwright
from protego import Protego

log = logging.getLogger("crawler")

# Product token announced in the User-Agent header and matched against the
# `User-agent:` groups of robots.txt. A group naming it takes precedence over `*`.
DEFAULT_USER_AGENT = "0xA6E07-crawler"


def _normalize(url: str) -> str:
    """Drop the fragment and any trailing slash (except for the bare root)."""
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(path=path).geturl()


class RobotsPolicy:
    """What a site's robots.txt permits for one user agent.

    Built from the HTTP result of fetching /robots.txt, following RFC 9309:
      * 2xx          -> parse the rules (wildcards `*` and `$` are supported)
      * 4xx          -> no robots.txt exists: everything is allowed
                        (429 Too Many Requests counts as "unavailable", below)
      * 5xx, 429, other statuses, or no response at all -> the file is
                        "unreachable": assume the site is off limits, so nothing is crawled
    """

    def __init__(self, parser: Protego, user_agent: str, description: str):
        self._parser = parser
        self._ua = user_agent
        self.description = description  # human-readable summary, for log messages

    @classmethod
    def from_response(cls, status: int | None, body: str, user_agent: str) -> "RobotsPolicy":
        if status is not None and 200 <= status < 300:
            return cls(Protego.parse(body), user_agent, "rules loaded from robots.txt")
        if status is not None and 400 <= status < 500 and status != 429:
            return cls(Protego.parse(""), user_agent, f"no robots.txt (HTTP {status}); everything allowed")
        return cls(Protego.parse("User-agent: *\nDisallow: /"), user_agent,
                   f"robots.txt unavailable ({'no response' if status is None else f'HTTP {status}'}); "
                   "assuming the site is off limits")

    def allowed(self, url: str) -> bool:
        return self._parser.can_fetch(url, self._ua)

    @property
    def delay(self) -> float:
        """Seconds to wait between requests: Crawl-delay or Request-rate, whichever is larger."""
        delay = self._parser.crawl_delay(self._ua) or 0.0
        rate = self._parser.request_rate(self._ua)
        if rate and rate.requests:
            delay = max(delay, rate.seconds / rate.requests)
        return float(delay)


def _load_robots(context, root_url: str, user_agent: str, timeout_ms: int) -> RobotsPolicy:
    """Fetch <root origin>/robots.txt with the browser context's HTTP client.

    This uses the context's request API, not page navigation: navigating to an
    empty-bodied 404 makes Chromium raise an error, which would wrongly look like
    "robots.txt unreachable". The request API returns the status normally, and
    still shares the context's user agent and proxy/TLS settings.
    """
    robots_url = urlparse(root_url)._replace(path="/robots.txt", params="", query="", fragment="").geturl()
    try:
        response = context.request.get(robots_url, timeout=timeout_ms)
        status = response.status
        body = response.text() if 200 <= status < 300 else ""
    except Exception as exc:  # DNS failure, timeout, connection refused, ...
        log.warning("Could not fetch %s: %s", robots_url, exc)
        status, body = None, ""
    policy = RobotsPolicy.from_response(status, body, user_agent)
    log.info("robots.txt for %s: %s", urlparse(root_url).netloc, policy.description)
    return policy


def crawl(
    root_url: str,
    max_pages: int = 500,
    timeout_ms: int = 30_000,
    respect_robots: bool = True,
    user_agent: str = DEFAULT_USER_AGENT,
) -> Iterator[str]:
    """Breadth-first crawl starting at `root_url`, yielding each distinct URL
    on the same host whose path is at or below the root's path.

    A headless browser is used so links added by JavaScript are discovered too.

    With `respect_robots` (the default) URLs disallowed for `user_agent` by the
    site's robots.txt are never visited or yielded, and requests are spaced by
    its Crawl-delay / Request-rate. Pass `respect_robots=False` only for sites
    you own or have permission to crawl.
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
    robots_skipped: list[str] = []

    with sync_playwright() as p:
        launch_args = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            launch_args["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = p.chromium.launch(**launch_args)
        try:
            # Announce ourselves: keep the browser's own UA string and append our token.
            probe = browser.new_page()
            browser_ua = probe.evaluate("navigator.userAgent")
            probe.close()
            context = browser.new_context(user_agent=f"{browser_ua} {user_agent}/1.0")
            page = context.new_page()

            policy = _load_robots(context, root_url, user_agent, timeout_ms) if respect_robots else None
            delay = policy.delay if policy else 0.0
            if delay:
                log.info("Honouring robots.txt request spacing of %.1f s", delay)
            last_request = time.monotonic()  # the robots.txt fetch counts as a request

            def allowed(url: str) -> bool:
                return policy is None or policy.allowed(url)

            if not allowed(start):
                log.warning("robots.txt does not allow crawling %s; nothing to do", start)
                return

            while queue and count < max_pages:
                url = queue.popleft()
                if delay:  # space requests as robots.txt asks
                    wait = delay - (time.monotonic() - last_request)
                    if wait > 0:
                        time.sleep(wait)
                try:
                    response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except Exception:
                    continue  # unreachable page: skip it
                finally:
                    last_request = time.monotonic()
                if response is None or response.status >= 400:
                    continue
                # A redirect may have landed on a URL robots.txt forbids.
                if not allowed(page.url):
                    log.info("Skipping %s: redirected to %s, disallowed by robots.txt", url, page.url)
                    robots_skipped.append(page.url)
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
                        if allowed(link):
                            queue.append(link)
                        else:
                            robots_skipped.append(link)
        finally:
            browser.close()
            if robots_skipped:
                log.warning("Skipped %d URL(s) disallowed by robots.txt (first: %s)",
                            len(robots_skipped), robots_skipped[0])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <root-url>")
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # notices go to stderr
    for found in crawl(sys.argv[1]):
        print(found)
