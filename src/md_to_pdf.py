"""Convert a Markdown file to a PDF.

Setup:
    pip install markdown playwright
    playwright install chromium
"""

import os
import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

CSS = """
body { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
       line-height: 1.55; color: #222; font-size: 11pt; }
h1, h2 { border-bottom: 1px solid #ddd; padding-bottom: .25em; }
code { background: #f4f4f4; padding: .1em .3em; border-radius: 3px; font-size: 90%; }
pre { background: #f4f4f4; padding: .8em; border-radius: 4px; overflow-x: auto;
      white-space: pre-wrap; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #ddd; margin-left: 0; padding-left: 1em; color: #555; }
table { border-collapse: collapse; }
th, td { border: 1px solid #ccc; padding: .35em .7em; }
img { max-width: 100%; }
"""


def markdown_to_pdf(md_path: str, pdf_path: str | None = None) -> str:
    """Render the Markdown file at `md_path` to a PDF and return the PDF path.

    `pdf_path` defaults to the input path with a .pdf extension. Relative image
    paths in the Markdown resolve against the Markdown file's directory.
    """
    src = Path(md_path)
    out = Path(pdf_path) if pdf_path else src.with_suffix(".pdf")

    body = markdown.markdown(
        src.read_text(encoding="utf-8"),
        extensions=["extra", "fenced_code", "tables", "sane_lists", "toc"],
    )
    html = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )

    # Write next to the source so relative links/images work when rendered.
    tmp = src.parent / f".{src.stem}.render.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        with sync_playwright() as p:
            launch_args = {"headless": True}
            if os.environ.get("CHROMIUM_PATH"):
                launch_args["executable_path"] = os.environ["CHROMIUM_PATH"]
            browser = p.chromium.launch(**launch_args)
            try:
                page = browser.new_page()
                page.goto(tmp.resolve().as_uri(), wait_until="load")
                page.pdf(
                    path=str(out),
                    format="A4",
                    print_background=True,
                    margin={"top": "20mm", "bottom": "20mm", "left": "18mm", "right": "18mm"},
                )
            finally:
                browser.close()
    finally:
        tmp.unlink(missing_ok=True)
    return str(out)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(f"Usage: {sys.argv[0]} <input.md> [output.pdf]")
    print(markdown_to_pdf(*sys.argv[1:]))
