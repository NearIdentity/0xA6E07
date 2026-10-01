# 0xA6E07 -- Company-Website Q&A Agent

A command-line chatbot that ingests a company website and answers questions
about it using only what the site says.

Built on **LangChain** (retrieval + prompting), **Ollama** (local chat and
embedding models) and **Pydantic** (validated config and structured LLM output).
No hosted APIs or keys are needed.

## How it works

```
 root URL
    │  src/crawler.py      headless-Chromium crawl, same-host, in-scope URLs only
    ▼
 URL list ──► src/stage.py ──► src/url_to_md.py   render page → Markdown
    │
    ▼
 data/<host>/*.md        one file per page, with a `source:` URL header
    │  src/agent.py
    ▼
 split into chunks ─► embed (Ollama) ─► in-memory vector store
    │
 question ─► [rewrite with chat history] ─► top-k chunks ─► chat model (Ollama)
                                                     │
                                  AgentAnswer (Pydantic) + source URLs
```

| File | Role |
|---|---|
| `src/crawler.py` | Existing tool. Breadth-first crawl; yields every URL at or under the root. |
| `src/url_to_md.py` | Existing tool. Renders a URL in headless Chromium and returns Markdown. |
| `src/models.py` | Pydantic models: `StageConfig`, `StagedPage`, `AgentConfig`, `AgentAnswer`. |
| `src/stage.py` | Ingest step: runs the two tools above and writes `data/<host>/*.md`. |
| `src/agent.py` | `SiteAgent`: chunking, embeddings, retrieval, answering, chat memory. |
| `src/cli.py` | The command-line entry point. |

Design points:

- **Relevant URLs.** The crawler stays on the root's host and path. `stage.py`
  also drops non-page assets (PDFs, images, CSS/JS, ...) and caps the crawl at
  `--max-pages` (default 50).
- **Crawl first, convert second.** `crawl()` is a generator that holds a
  Playwright sync session open, and that API can't be nested. `stage.py`
  therefore finishes the crawl before calling `url_to_markdown`. This means
  each page is fetched twice (once to find links, once to convert), which is
  simple and fine for a company site.
- **Grounded answers.** The model must return an `AgentAnswer` (`answer`,
  `found_in_context`), parsed and validated by Pydantic. If the context
  doesn't contain the answer it says so rather than guessing.
- **Real citations.** The cited URLs come from the retrieved chunks' metadata,
  not from the model, so it can't invent a source. Sources are shown only when
  `found_in_context` is true.
- **Untrusted web content.** The prompt tells the model to treat page text as
  data and ignore instructions inside it (basic prompt-injection hygiene; it
  is not a guarantee).
- **Follow-ups.** The last few turns are kept (`history_turns`, default 3). A
  follow-up like "and pricing?" is first rewritten into a standalone question
  so retrieval works.
- **Index.** The vector store is in memory and rebuilt at each `chat` start.
  Embedding a small site takes seconds; for large sites swap in a persistent
  store in `agent.py`.

## Setup

```bash
bash venv-setup.sh                 # optional: creates ./venv
. venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Ollama (https://ollama.com) must be running locally, with two models pulled:
ollama pull llama3.1               # chat model (must support structured output)
ollama pull nomic-embed-text       # embedding model
```

### Additional Notes -- Ollama Installation Workaround

Original instructions for AMD64 Linux:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```


Alternative for AMD64 Linux:

```bash
wget -O ollama.tar.zst https://github.com/ollama/ollama/releases/download/v0.35.0/ollama-linux-amd64.tar.zst
file ollama.tar.zst
sha256sum ./ollama.tar.zst
mkdir -p ~/.local
tar --zstd -xf ./ollama.tar.zst -C ~/.local
export PATH=$HOME/.local/bin:$PATH
ollama pull llama3.1
ollama run llama3.1
ollama pull nomic-embed-text
```

### Additional Notes -- Ollama Run

```bashh
ollama serve
```

## Usage

```bash
python src/cli.py run    example.com   # ingest, then chat
python src/cli.py ingest example.com   # crawl + stage only  -> data/example.com/*.md
python src/cli.py chat   example.com   # chat over already-staged pages
```

Options (all subcommands): `--data-dir data`, `--max-pages 50`,
`--model llama3.1`, `--embed-model nomic-embed-text`,
`--ollama-url http://localhost:11434`.

Example session:

```
you> When was the company founded?

agent> Acme was founded in 1999.
sources:
  - https://example.com/about

you> What do they sell?
```

Enter a blank line or press Ctrl-D to quit. Staged pages live under `data/`,
which is git-ignored. 

## Testing status

The ingest path (crawl → Markdown files) and the retrieval/answer pipeline were
run end to end against a local test site, using stand-in fake models in place
of Ollama (no Ollama server was available in the build environment). The live
Ollama calls (`ChatOllama`, `OllamaEmbeddings`) have **not** been exercised, so
the first real run is the check that your chosen models support structured
output. If a model doesn't, pick another chat model with `--model`.

## Limitations

- Only crawls what it can reach by following links; pages behind logins or
  not linked from the root are not found.
- No `robots.txt` handling or rate limiting; only crawl sites you may crawl.
- Answers are limited to the staged snapshot; re-run `ingest` to refresh.


## Some Sources of Inspiration

1. [' Build an AI Agent From Scratch With Python' by Mike Chambers / AWS Developers](https://www.youtube.com/watch?v=jcpZU2vyrf4&t=98s), ['Build Your First AI Agent with AWS Free Tier' by Mike Chamb ers](https://builder.aws.com/content/3Bkf36kIFji2DTJoJSBeFQO55Zn/), and ['agent-quest' on _GitHub_](https://github.com/mikegc-aws/agent-quest/).

2. [' Build an AI Agent From Scratch in Python - Tutorial for Beginners' from Tech by Tim](https://www.youtube.com/watch?v=bTMPwUgLZf0) and ['PythonAIAgentFromScratch' on _GitHub_](https://github.com/techwithtim/PythonAIAgentFromScratch).

3. ['I Built an AI Data Agent Which Can Query Data and Answer Business Questions. Here’s How.' by Jiayan Yin](https://towardsdatascience.com/i-built-an-ai-data-agent-which-can-query-data-and-answer-business-questions-heres-how/) and ['Avocado-Sales-Analytics-Agent' on _GitHub_](https://github.com/jyin-simba/Avocado-Sales-Analytics-Agent)
