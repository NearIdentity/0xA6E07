"""Ingest step: crawl a company website and stage each page as a Markdown file.

Uses the repo's own tools unchanged:
    crawler.crawl            -> yields in-scope URLs (headless Chromium, so
                                JavaScript-added links are found)
    url_to_md.url_to_markdown -> renders one URL and converts it to Markdown
"""

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from crawler import crawl
from models import StageConfig, StagedPage
from url_to_md import url_to_markdown


def _filename(url: str) -> str:
    """Readable, collision-free file name for a URL: <slug>-<hash>.md"""
    p = urlparse(url)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (p.path or "/").strip("/")).strip("-") or "index"
    digest = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{slug[:60]}-{digest}.md"


def stage_site(cfg: StageConfig, log=print) -> list[StagedPage]:
    """Crawl `cfg.root_url` and write each page to <data_dir>/<host>/*.md.

    Each file starts with a small front-matter block recording the source URL;
    the agent reads it back so answers can cite where they came from.
    """
    host = urlparse(cfg.root_url).netloc
    out_dir = cfg.data_dir / host
    out_dir.mkdir(parents=True, exist_ok=True)

    # Drain the crawler fully before converting. `crawl` is a generator that keeps
    # a Playwright sync session open between yields, and Playwright's sync API
    # can't be nested, so url_to_markdown must not run while it is still active.
    urls = [
        u for u in crawl(cfg.root_url, max_pages=cfg.max_pages, timeout_ms=cfg.timeout_ms)
        if not urlparse(u).path.lower().endswith(cfg.skip_extensions)  # not content pages
    ]
    log(f"Found {len(urls)} page(s); converting to Markdown...")

    staged: list[StagedPage] = []
    for url in urls:
        try:
            markdown = url_to_markdown(url, timeout_ms=cfg.timeout_ms)
        except Exception as exc:  # one bad page shouldn't abort the whole ingest
            log(f"  ! skipped {url}: {exc}")
            continue
        if not markdown.strip():
            continue

        title = markdown.splitlines()[0].lstrip("# ").strip() if markdown.startswith("# ") else ""
        path = out_dir / _filename(url)
        path.write_text(f"---\nsource: {url}\n---\n\n{markdown}", encoding="utf-8")
        staged.append(StagedPage(url=url, title=title, path=path))
        log(f"  + {url}")

    return staged
