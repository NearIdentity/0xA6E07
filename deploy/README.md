# Deploying the Q&A web app (Docker + Kubernetes)

This directory and the root `Dockerfile` package the Flask app (`src/web.py`,
served through `src/wsgi.py`) for Kubernetes.

```
                         ┌──────────────── namespace: site-qa ────────────────┐
 browser ─► Ingress ───► │ site-qa-web  (gunicorn + Flask)  ──►  ollama        │
 (basic auth, TLS)       │      ▲ reads /data (read-only)        ▲  models PVC │
                         │      │                                │            │
                         │ site-qa-data PVC ◄── site-ingest      │            │
                         │      (Markdown)      CronJob          ollama-pull   │
                         │                      (Chromium)       Job           │
                         └─────────────────────────────────────────────────────┘
```

| Piece | What it does |
|---|---|
| `Dockerfile` target `web` | Small image: Flask + gunicorn + LangChain. No browser. |
| `Dockerfile` target `ingest` | Crawler image with Playwright/Chromium; runs `cli.py ingest`. |
| `src/wsgi.py` | Production entry point; reads its settings from environment variables and adds `/healthz`. |
| `deploy/k8s/web.yaml` | The web Deployment and Service. Waits (init containers) for staged pages and for Ollama's models. |
| `deploy/k8s/ingest-cronjob.yaml` | Weekly crawl that refreshes the Markdown on the data volume. |
| `deploy/k8s/ollama.yaml`, `ollama-pull-job.yaml` | Ollama server (optionally on a GPU) and a Job that downloads the models. |
| `deploy/k8s/ingress.yaml` | ingress-nginx Ingress with HTTP basic auth and long timeouts. |
| `deploy/k8s/networkpolicy.yaml` | Only the web pod and the pull job may reach Ollama. |
| `deploy/k8s/{namespace,configmap,pvc,kustomization}.yaml` | Namespace, settings, storage, and the `kubectl apply -k` entry point. |

## Prerequisites

- A Kubernetes cluster (1.25+), `kubectl`, and a container registry you can push to.
- The **ingress-nginx** controller (or adapt `ingress.yaml` to your controller).
- A default StorageClass (or set `storageClassName` in `pvc.yaml`).
- Optional: the NVIDIA device plugin / GPU operator, if Ollama should use a GPU.
- Outbound internet from the cluster: the ingest job crawls your site, and the
  pull job downloads models from ollama.com.

## Deploy

**1. Build and push the images** (from the repo root):

```bash
REGISTRY=registry.example.com            # your registry
docker build --target web    -t $REGISTRY/site-qa-web:0.1.0    .
docker build --target ingest -t $REGISTRY/site-qa-ingest:0.1.0 .
docker push $REGISTRY/site-qa-web:0.1.0
docker push $REGISTRY/site-qa-ingest:0.1.0
```

**2. Edit the settings** marked `>>> EDIT`:

- `deploy/k8s/kustomization.yaml`: your registry in `images:`.
- `deploy/k8s/configmap.yaml`: `SITE_URL` (the site to ingest) and, if you like, the models.
- `deploy/k8s/ingress.yaml`: your host name (twice).
- `deploy/k8s/ollama.yaml`: uncomment `nvidia.com/gpu: 1` if you have GPU nodes.

**3. Create the secrets.** They are never stored in the repo:

```bash
kubectl apply -f deploy/k8s/namespace.yaml

# Signs session cookies. Keep it stable so sessions survive restarts.
kubectl -n site-qa create secret generic site-qa-secrets \
  --from-literal=flask-secret-key="$(openssl rand -hex 32)"

# Login for the Ingress (HTTP basic auth). Pick your own user name.
printf 'admin:%s\n' "$(openssl passwd -apr1)" > auth      # prompts for a password
kubectl -n site-qa create secret generic site-qa-basic-auth --from-file=auth
rm auth
```

**4. Apply everything:**

```bash
kubectl apply -k deploy/k8s
```

**5. Load the data.** The CronJob only fires weekly, so run the first crawl now:

```bash
kubectl -n site-qa create job --from=cronjob/site-ingest site-ingest-first
kubectl -n site-qa logs -f job/site-ingest-first
```

The web pod stays in `Init` until staged pages exist and Ollama has both
models, then indexes the site and turns ready (a few minutes for a large site).

**6. Check it:**

```bash
kubectl -n site-qa get pods
kubectl -n site-qa logs deploy/site-qa-web
kubectl -n site-qa port-forward svc/site-qa-web 8080:80     # then open http://localhost:8080
```

## Day-2 operations

- **Refresh the site content:** run the ingest job again (command in step 5), then
  `kubectl -n site-qa rollout restart deployment/site-qa-web` so the app re-indexes.
  The app builds its index once at start-up and does not notice new files.
- **Change models:** edit `CHAT_MODEL` / `EMBED_MODEL` in the ConfigMap, then
  `kubectl -n site-qa delete job ollama-pull`, `kubectl apply -k deploy/k8s`, and
  restart the web Deployment. Changing the embedding model requires a restart
  because the index is rebuilt with it.
- **Use an external Ollama server (or none in-cluster):** set `OLLAMA_URL` in the
  ConfigMap and remove `ollama.yaml`, `ollama-pull-job.yaml` and `networkpolicy.yaml`
  from `kustomization.yaml`. The web pod's `wait-for-ollama` step still expects the
  models to be present on that server.
- **Upgrade the app:** push new image tags, update `newTag` in `kustomization.yaml`,
  `kubectl apply -k deploy/k8s`. The Deployment uses `Recreate`, so there is a short gap.

## Things to know before going live

- **No login in the app itself.** Basic auth at the Ingress is the only protection.
  Anyone who gets past it can use your model server. Keep TLS on (uncomment the
  cert-manager annotation or supply your own certificate), or put an SSO proxy in front.
- **One replica.** Chat histories and the vector index live in the web process's
  memory (that is why gunicorn runs `--workers 1 --threads 8`). Restarts clear
  conversations. To run several replicas you would need sticky sessions
  (`affinity: cookie` is in `ingress.yaml`, commented out), a ReadWriteMany data
  volume (then drop the `podAffinity` block in the CronJob), and you would still
  lose histories when a pod restarts. Moving histories to Redis would remove that
  limit but needs code changes.
- **Stale pages stay.** A re-crawl overwrites pages it finds again but does not
  delete files for pages that disappeared from the site. To start clean, delete
  `*.md` under `/data/<host>/` in the data volume before re-running ingest.
- **Resources.** Ollama's memory request assumes an 8B model (~5 GB) plus headroom.
  CPU-only inference works but is slow; the ingress timeout is 300 s.
- **Crawling etiquette.** The ingest job obeys the target site's `robots.txt` and its
  `Crawl-delay`, and identifies itself as `0xA6E07-crawler/1.0`. A site that disallows
  the crawler (or whose `robots.txt` is unreachable) yields zero staged pages, so the
  web pod will keep waiting in `Init`; check the ingest job's logs for the reason.
  A large `Crawl-delay` makes the crawl slow, and the CronJob stops it after one hour
  (`activeDeadlineSeconds`). Only point it at sites you are allowed to crawl.
- **Image versions.** Python dependencies are unpinned in `requirements*.txt`, so each
  build picks up the latest releases. Pin them (for example from a tested
  `pip freeze`) for reproducible production images. The Ollama image tag is pinned to
  `0.35.0` in `kustomization.yaml`, `ollama.yaml` and the pull job; check the tag
  exists and change all of them together.
- **Ollama runs as root** inside its pod (the upstream image expects it). It has no
  extra privileges or capabilities, and the NetworkPolicy limits who can reach it.

## Without Kubernetes (single machine)

```bash
docker network create siteqa
docker run -d --name ollama --network siteqa -v ollama:/root/.ollama ollama/ollama:0.35.0   # add --gpus all for NVIDIA
docker exec ollama ollama pull llama3.1
docker exec ollama ollama pull nomic-embed-text

docker run --rm --network siteqa --shm-size=1g -v siteqa-data:/data \
  site-qa-ingest:0.1.0 ingest https://www.example.com/ --data-dir /data

docker run -d --name web --network siteqa -p 127.0.0.1:8000:8000 -v siteqa-data:/data:ro \
  -e SITE_URL=https://www.example.com/ -e OLLAMA_URL=http://ollama:11434 \
  -e FLASK_SECRET_KEY="$(openssl rand -hex 32)" site-qa-web:0.1.0
# then open http://127.0.0.1:8000
```

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Web pod stuck in `Init:0/2` | No staged pages yet. Run the ingest job and check its logs. |
| Web pod stuck in `Init:1/2` | Ollama is down or the models are not pulled. Check `kubectl -n site-qa logs job/ollama-pull`. |
| Web pod `CreateContainerConfigError` | The `site-qa-secrets` secret is missing (step 3). |
| Web pod `CrashLoopBackOff` | `kubectl logs` shows why: usually Ollama unreachable, or `SITE_URL`'s host differs from what was ingested (data is stored under `/data/<host>/`). |
| Ingest pod `Pending` | The data volume is ReadWriteOnce and the web pod is on another node, or no web pod exists yet (the affinity rule needs one). Use a ReadWriteMany class, or apply the whole kustomization first. |
| Ingest job crashes in Chromium | Memory limit too low (raise `limits.memory`) or `/dev/shm` too small (see the `shm` volume). |
| Browser gets 401 | Ingress basic auth is on: use the user and password you chose in step 3. |
| Answers cut off at 60 s | You are not using ingress-nginx, or the `proxy-read-timeout` annotation is not applied. |

## What has and has not been tested

Tested in the build environment: the `wsgi.py` + gunicorn start-up and command-line
flags (including running as an unprivileged user), `/healthz`, chat requests and
follow-ups through the real `langchain-ollama` clients against a mock Ollama server,
the fail-fast behaviour when data or `SITE_URL` is missing, and schema validation of
every manifest against Kubernetes 1.30.

**Not** tested: building the images (no Docker daemon was available), applying the
manifests to a cluster, kustomize rendering, the Ingress and NetworkPolicy behaviour,
GPU scheduling, and a real Ollama server. Expect to fix small things on the first real
deploy, and read `kubectl describe` output for any pod that does not start.
