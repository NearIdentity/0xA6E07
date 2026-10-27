# syntax=docker/dockerfile:1
#
# Two images from one file (pick one with --target):
#
#   web     (default, last stage)  Flask app under gunicorn. Small: no browser.
#   ingest                         crawler + Playwright/Chromium; fills the data volume.
#
#   docker build --target web    -t site-qa-web:0.1.0    .
#   docker build --target ingest -t site-qa-ingest:0.1.0 .

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- ingest ----
FROM python:${PYTHON_VERSION}-slim AS ingest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first so this layer is cached until requirements.txt changes.
COPY requirements.txt .
# `--with-deps` installs Chromium's system libraries (needs root, so it runs
# before USER below). The browser lands in /ms-playwright, readable by everyone.
RUN pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY src/ src/

# Unprivileged user. /data is where staged Markdown is written (a volume in k8s).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && mkdir /data && chown 10001 /data
USER 10001

# Usage: docker run -v data:/data site-qa-ingest ingest https://example.com --data-dir /data
ENTRYPOINT ["python", "src/cli.py"]
CMD ["--help"]

# ------------------------------------------------------------------- web ----
FROM python:${PYTHON_VERSION}-slim AS web

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

COPY deploy/requirements-web.txt .
RUN pip install -r requirements-web.txt

# Only what the web app imports: the crawler and its dependencies stay out.
COPY src/agent.py src/models.py src/web.py src/wsgi.py src/
COPY src/templates/ src/templates/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8000

# SITE_URL (required), OLLAMA_URL, CHAT_MODEL, EMBED_MODEL, FLASK_SECRET_KEY are
# supplied at run time; see src/wsgi.py.
#
# One worker + threads: the index and chat histories are in-process (see wsgi.py).
# --timeout is generous because a local LLM can take minutes to answer.
# --worker-tmp-dir /dev/shm keeps gunicorn's heartbeat file off the (possibly
# read-only) container filesystem. --no-control-socket stops gunicorn trying to
# create a socket under a home directory this unprivileged user doesn't have.
CMD ["gunicorn", "--chdir", "src", "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--threads", "8", "--timeout", "300", \
     "--worker-tmp-dir", "/dev/shm", "--no-control-socket", \
     "--access-logfile", "-", "wsgi:app"]
