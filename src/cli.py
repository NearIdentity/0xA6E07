"""Command-line interface: ingest a company website, then chat about it.

    python src/cli.py ingest example.com          # crawl + stage Markdown
    python src/cli.py chat    example.com         # chat over staged pages
    python src/cli.py run     example.com         # ingest, then chat
"""

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from pydantic import ValidationError

try:  # enables line editing/history in input(); not available on every platform
    import readline  # noqa: F401
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False

from agent import SiteAgent
from models import AgentConfig, StageConfig
from stage import stage_site


# ANSI colours: the user's prompt is yellow and the agent's reply is cyan.
# Colour is skipped when output isn't a terminal (e.g. piped to a file) or when
# the NO_COLOR environment variable is set (https://no-color.org).
_USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
YELLOW, CYAN, RESET = "\033[33m", "\033[36m", "\033[0m"


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if _USE_COLOR else text


def _read_user_input() -> str:
    """Prompt in yellow; the text the user types stays yellow too."""
    if not _USE_COLOR:
        return input("you> ").strip()
    # With readline active, \001...\002 mark the escape codes as zero-width so
    # cursor positioning and line editing stay correct. Without readline those
    # markers would print as stray control characters, so only add them then.
    start, end = ("\001", "\002") if _HAS_READLINE else ("", "")
    try:
        return input(f"{start}{YELLOW}{end}you> ").strip()
    finally:
        sys.stdout.write(RESET)  # always restore the terminal colour
        sys.stdout.flush()


def _host(url: str) -> str:
    return urlparse(url if "://" in url else "https://" + url).netloc


def do_ingest(args) -> None:
    cfg = StageConfig(root_url=args.url, data_dir=args.data_dir, max_pages=args.max_pages)
    print(f"Crawling {cfg.root_url} (up to {cfg.max_pages} pages)...")
    pages = stage_site(cfg)
    print(f"Staged {len(pages)} page(s) in {cfg.data_dir / _host(cfg.root_url)}")
    if not pages:
        sys.exit("Nothing was staged; check the URL and network access.")


def do_chat(args) -> None:
    # Only this site's pages are indexed, even if other sites are staged too.
    cfg = AgentConfig(
        data_dir=args.data_dir / _host(args.url),
        chat_model=args.model,
        embed_model=args.embed_model,
        ollama_url=args.ollama_url,
    )
    print("Indexing staged pages (embedding via Ollama)...")
    agent = SiteAgent(cfg)
    print(f"Ready: {len(agent.chunks)} chunks indexed. Ask a question (blank line or Ctrl-D to quit).\n")
    while True:
        try:
            question = _read_user_input()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        result, sources = agent.ask(question)
        reply = f"agent> {result.answer}"
        if sources:
            reply += "\nsources:\n" + "\n".join(f"  - {s}" for s in sources)
        print("\n" + _paint(reply, CYAN) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("ingest", "chat", "run"):
        s = sub.add_parser(name)
        s.add_argument("url", help="company website root, e.g. example.com")
        s.add_argument("--data-dir", type=Path, default=Path("data"))
        s.add_argument("--max-pages", type=int, default=50)
        s.add_argument("--model", default="llama3.1", help="Ollama chat model")
        s.add_argument("--embed-model", default="nomic-embed-text", help="Ollama embedding model")
        s.add_argument("--ollama-url", default="http://localhost:11434")
    args = p.parse_args()

    try:
        if args.command in ("ingest", "run"):
            do_ingest(args)
        if args.command in ("chat", "run"):
            do_chat(args)
    except (ValidationError, RuntimeError) as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
