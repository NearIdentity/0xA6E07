"""Tests for robots.txt handling in src/crawler.py.

The RobotsPolicy tests are pure Python. The crawl tests drive the real crawler
in headless Chromium against a local HTTP server that records every request,
so they can assert that disallowed URLs were never even fetched. They are
skipped if Chromium can't be launched (set CHROMIUM_PATH to point at one).

    python -m unittest tests/test_crawler.py
"""

import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crawler import DEFAULT_USER_AGENT, RobotsPolicy, crawl

BASE = "http://127.0.0.1"

# path -> (content type, body)
PAGES = {
    "/": ("text/html", '<a href="/a.html">a</a> <a href="/private/x.html">x</a> '
                       '<a href="/files/doc.pdf">pdf</a> <a href="/search?session=1">s</a> '
                       '<a href="/redir">r</a> <a href="/ok/deep.html">d</a>'),
    "/a.html": ("text/html", '<a href="/ok/deep.html">deep</a>'),
    "/private/x.html": ("text/html", "private x"),
    "/private/y.html": ("text/html", "private y"),
    "/ok/deep.html": ("text/html", "deep"),
    "/files/doc.pdf": ("application/pdf", "%PDF-1.4 fake"),
    "/search": ("text/html", "search results"),
}


class SiteHandler(BaseHTTPRequestHandler):
    robots = (200, "")           # set per test: (status, body) for /robots.txt
    log: list = []                # (path, user_agent, monotonic time)

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).log.append((self.path, self.headers.get("User-Agent", ""), time.monotonic()))
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            status, body = type(self).robots
            return self._send(status, "text/plain", body)
        if path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/private/y.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in PAGES:
            ctype, body = PAGES[path]
            return self._send(200, ctype, body)
        self._send(404, "text/plain", "not found")

    def _send(self, status, ctype, body):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class RobotsPolicyTests(unittest.TestCase):
    def policy(self, status, body=""):
        return RobotsPolicy.from_response(status, body, DEFAULT_USER_AGENT)

    def test_rules_with_wildcards(self):
        p = self.policy(200, "User-agent: *\nDisallow: /private/\nDisallow: /*.pdf$\nDisallow: /*?session=\n"
                             "Allow: /private/open/\n")
        self.assertFalse(p.allowed("https://x.test/private/a"))
        self.assertTrue(p.allowed("https://x.test/private/open/a"))   # longer Allow wins
        self.assertFalse(p.allowed("https://x.test/f.pdf"))
        self.assertTrue(p.allowed("https://x.test/f.pdf?x=1"))        # `$` anchors the end
        self.assertFalse(p.allowed("https://x.test/s?session=1"))
        self.assertTrue(p.allowed("https://x.test/about"))

    def test_our_own_group_beats_wildcard_group(self):
        p = self.policy(200, f"User-agent: *\nDisallow: /\n\nUser-agent: {DEFAULT_USER_AGENT}\nDisallow: /secret\n")
        self.assertTrue(p.allowed("https://x.test/about"))
        self.assertFalse(p.allowed("https://x.test/secret/x"))

    def test_missing_file_allows_everything(self):
        for status in (404, 410, 401, 403):                           # RFC 9309: 4xx = no robots.txt
            self.assertTrue(self.policy(status).allowed("https://x.test/anything"), status)

    def test_unavailable_file_blocks_everything(self):
        for status in (500, 503, 429, 302, None):                     # 5xx, 429, odd status, no response
            self.assertFalse(self.policy(status).allowed("https://x.test/"), status)

    def test_delay_from_crawl_delay_and_request_rate(self):
        self.assertEqual(self.policy(200, "User-agent: *\nCrawl-delay: 2\n").delay, 2.0)
        self.assertEqual(self.policy(200, "User-agent: *\nRequest-rate: 1/5\n").delay, 5.0)
        self.assertEqual(self.policy(200, "User-agent: *\nCrawl-delay: 3\nRequest-rate: 1/5\n").delay, 5.0)
        self.assertEqual(self.policy(200, "User-agent: *\nDisallow: /x\n").delay, 0.0)

    def test_html_error_page_served_as_robots_is_harmless(self):
        self.assertTrue(self.policy(200, "<html><body>Not found</body></html>").allowed("https://x.test/a"))


def chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        import os
        args = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            args["executable_path"] = os.environ["CHROMIUM_PATH"]
        with sync_playwright() as p:
            p.chromium.launch(**args).close()
        return True
    except Exception:
        return False


@unittest.skipUnless(chromium_available(), "Chromium is not available")
class CrawlRobotsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
        cls.root = f"{BASE}:{cls.server.server_port}/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_crawl(self, robots_status, robots_body="", **kwargs):
        SiteHandler.robots = (robots_status, robots_body)
        SiteHandler.log = []
        urls = list(crawl(self.root, **kwargs))
        paths = sorted(u.replace(self.root.rstrip("/"), "") or "/" for u in urls)
        fetched = self.fetched()
        return paths, fetched

    @staticmethod
    def fetched():
        # Chromium itself asks for /favicon.ico after page loads; that is browser
        # behaviour, not a URL the crawler chose, so leave it out of the comparison.
        return [p for p, _, _ in SiteHandler.log if p != "/favicon.ico"]

    def test_disallowed_urls_are_neither_fetched_nor_yielded(self):
        paths, fetched = self.run_crawl(200, "User-agent: *\nDisallow: /private/\nDisallow: /*.pdf$\n"
                                             "Disallow: /*?session=\n")
        self.assertEqual(paths, ["/", "/a.html", "/ok/deep.html"])
        for bad in ("/private/x.html", "/files/doc.pdf", "/search?session=1"):
            self.assertNotIn(bad, fetched)
        self.assertEqual(fetched[0], "/robots.txt")                  # fetched before anything else

    def test_no_robots_txt_means_crawl_everything(self):
        paths, _ = self.run_crawl(404)
        self.assertIn("/private/x.html", paths)
        self.assertIn("/files/doc.pdf", paths)

    def test_empty_bodied_404_for_robots_txt_still_means_allowed(self):
        # Regression: Chromium raises on navigation to an empty 404, which once
        # made a missing robots.txt look "unreachable" and blocked the whole crawl.
        paths, _ = self.run_crawl(404, "")
        self.assertIn("/a.html", paths)

    def test_unreachable_robots_txt_crawls_nothing(self):
        paths, fetched = self.run_crawl(500)
        self.assertEqual(paths, [])
        self.assertEqual(fetched, ["/robots.txt"])

    def test_disallowed_root_crawls_nothing(self):
        paths, fetched = self.run_crawl(200, "User-agent: *\nDisallow: /\n")
        self.assertEqual(paths, [])
        self.assertEqual(fetched, ["/robots.txt"])

    def test_own_user_agent_group_applies(self):
        paths, fetched = self.run_crawl(
            200, f"User-agent: *\nDisallow: /\n\nUser-agent: {DEFAULT_USER_AGENT}\nDisallow: /a.html\n")
        self.assertIn("/", paths)
        self.assertNotIn("/a.html", paths)
        self.assertNotIn("/a.html", fetched)

    def test_redirect_into_disallowed_area_is_not_yielded(self):
        paths, _ = self.run_crawl(200, "User-agent: *\nDisallow: /private/\n")
        self.assertNotIn("/redir", paths)
        self.assertNotIn("/private/y.html", paths)

    def test_crawl_delay_spaces_requests(self):
        _, _ = self.run_crawl(200, "User-agent: *\nCrawl-delay: 1\n", max_pages=3)
        times = [t for p, _, t in SiteHandler.log if p != "/favicon.ico"]
        self.assertGreaterEqual(len(times), 3)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertTrue(all(g >= 0.9 for g in gaps), gaps)

    def test_requests_identify_the_crawler(self):
        self.run_crawl(404, max_pages=2)
        agents = [ua for p, ua, _ in SiteHandler.log if p != "/favicon.ico"]
        self.assertGreaterEqual(len(agents), 3)  # robots.txt plus two pages
        self.assertTrue(all(f"{DEFAULT_USER_AGENT}/1.0" in ua for ua in agents), agents)

    def test_respect_robots_false_ignores_the_file_entirely(self):
        paths, fetched = self.run_crawl(200, "User-agent: *\nDisallow: /\n", respect_robots=False)
        self.assertIn("/private/x.html", paths)
        self.assertNotIn("/robots.txt", fetched)


if __name__ == "__main__":
    unittest.main()
