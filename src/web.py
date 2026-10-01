"""Flask web UI for the company-website Q&A agent.

    python src/web.py example.com                  # serve on http://127.0.0.1:5000
    python src/web.py example.com --port 8080 --model llama3.1

Run `python src/cli.py ingest example.com` first so there are staged pages.

Design:
  * The vector index is built ONCE at start-up (embedding is the slow part).
  * Each browser session gets its own conversation: a shallow copy of the
    SiteAgent that shares the index/models but has a private `history` list.
    No changes to agent.py are needed.
  * Browser <-> server traffic is JSON; the page itself is src/templates/index.html.
"""

import argparse
import copy
import os
import secrets
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, session

from agent import SiteAgent
from models import AgentConfig

MAX_MESSAGE_CHARS = 2000   # reject absurdly long questions
MAX_SESSIONS = 100         # oldest idle conversations are dropped beyond this


class ConversationStore:
    """Per-browser-session conversations built from one shared, indexed agent."""

    def __init__(self, base_agent: SiteAgent, max_sessions: int = MAX_SESSIONS):
        self._base = base_agent
        self._max = max_sessions
        # sid -> (agent copy, lock). OrderedDict gives us least-recently-used eviction.
        self._items: OrderedDict[str, tuple[SiteAgent, threading.Lock]] = OrderedDict()
        self._guard = threading.Lock()

    def get(self, sid: str) -> tuple[SiteAgent, threading.Lock]:
        with self._guard:
            if sid not in self._items:
                convo = copy.copy(self._base)  # shares index, models and chains...
                convo.history = []             # ...but not the conversation memory
                self._items[sid] = (convo, threading.Lock())
                while len(self._items) > self._max:
                    self._items.popitem(last=False)
            self._items.move_to_end(sid)
            return self._items[sid]

    def reset(self, sid: str) -> None:
        with self._guard:
            self._items.pop(sid, None)


def create_app(agent: SiteAgent, site_name: str = "") -> Flask:
    """Build the Flask app around an already-indexed agent (injectable for tests)."""
    app = Flask(__name__)
    # Signs the session cookie. Set FLASK_SECRET_KEY to keep sessions valid across
    # restarts; otherwise a random key is used and conversations reset on restart.
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    store = ConversationStore(agent)

    def current_sid() -> str:
        if "sid" not in session:
            session["sid"] = secrets.token_hex(16)
        return session["sid"]

    @app.get("/")
    def index():
        current_sid()
        return render_template("index.html", site_name=site_name, chunks=len(agent.chunks))

    @app.post("/api/chat")
    def chat():
        # get_json() only parses application/json bodies. Requiring that content
        # type also blocks simple cross-site form posts (basic CSRF protection).
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("message"), str):
            return jsonify(error='Send JSON like {"message": "..."}.'), 400
        message = data["message"].strip()
        if not message:
            return jsonify(error="Message is empty."), 400
        if len(message) > MAX_MESSAGE_CHARS:
            return jsonify(error=f"Message is too long (max {MAX_MESSAGE_CHARS} characters)."), 413

        convo, lock = store.get(current_sid())
        try:
            with lock:  # one question at a time per conversation keeps history ordered
                result, sources = convo.ask(message)
        except Exception:
            # Log details server-side; don't leak internals to the browser.
            app.logger.exception("Agent call failed")
            return jsonify(error="The language model request failed. Is the model server running?"), 502
        return jsonify(
            answer=result.answer,
            found_in_context=result.found_in_context,
            sources=sources,
        )

    @app.post("/api/reset")
    def reset():
        store.reset(current_sid())
        return jsonify(ok=True)

    return app


def _host(url: str) -> str:
    return urlparse(url if "://" in url else "https://" + url).netloc


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", help="company website root that was ingested, e.g. example.com")
    p.add_argument("--data-dir", type=Path, default=Path("data"))
    p.add_argument("--model", default="llama3.1", help="Ollama chat model")
    p.add_argument("--embed-model", default="nomic-embed-text", help="Ollama embedding model")
    p.add_argument("--ollama-url", default="http://localhost:11434")
    # Localhost by default: the app has no login, so don't expose it unintentionally.
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5000)
    args = p.parse_args()

    host = _host(args.url)
    cfg = AgentConfig(
        data_dir=args.data_dir / host,
        chat_model=args.model,
        embed_model=args.embed_model,
        ollama_url=args.ollama_url,
    )
    print("Indexing staged pages (embedding via Ollama)...")
    try:
        agent = SiteAgent(cfg)
    except RuntimeError as exc:
        sys.exit(f"Error: {exc}")
    print(f"Ready: {len(agent.chunks)} chunks indexed. Serving on http://{args.host}:{args.port}")

    # Flask's built-in server (debugger off) is fine for local use; for shared
    # deployments run create_app() under a production server such as waitress.
    create_app(agent, site_name=host).run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
