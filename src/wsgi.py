"""WSGI entry point for production servers such as gunicorn.

`web.py` is for local use: it takes the site as a CLI argument and runs Flask's
development server. In a container the site and model settings come from
environment variables instead, so this module builds the app at import time:

    gunicorn --chdir src wsgi:app

Environment variables:
    SITE_URL     required  site that was ingested, e.g. https://www.example.com/
    DATA_DIR     optional  parent of the staged pages (default: data) -> <DATA_DIR>/<host>/*.md
    CHAT_MODEL   optional  Ollama chat model       (default: llama3.1)
    EMBED_MODEL  optional  Ollama embedding model  (default: nomic-embed-text)
    OLLAMA_URL   optional  Ollama server           (default: http://localhost:11434)
    FLASK_SECRET_KEY       signs session cookies (see web.create_app)

If the staged pages or the model server are missing, importing this module
raises, gunicorn reports a failed worker boot and the process exits, so the
orchestrator restarts it rather than serving a broken app.

Run gunicorn with ONE worker process and several threads (--workers 1
--threads N): the vector index and the per-session chat histories live in this
process's memory, so extra worker processes would each re-embed the site and
would not share conversations.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

from agent import SiteAgent
from models import AgentConfig
from web import create_app

_site_url = os.environ.get("SITE_URL")
if not _site_url:
    raise RuntimeError("SITE_URL is not set (e.g. https://www.example.com/)")
_host = urlparse(_site_url if "://" in _site_url else "https://" + _site_url).netloc

_cfg = AgentConfig(
    data_dir=Path(os.environ.get("DATA_DIR", "data")) / _host,
    chat_model=os.environ.get("CHAT_MODEL", "llama3.1"),
    embed_model=os.environ.get("EMBED_MODEL", "nomic-embed-text"),
    ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
)

_agent = SiteAgent(_cfg)  # embeds every staged page; can take a while on first start
app = create_app(_agent, site_name=_host)


@app.get("/healthz")
def healthz():
    """Liveness/readiness probe target. Only answers once the index is built."""
    return {"status": "ok", "chunks": len(_agent.chunks)}
