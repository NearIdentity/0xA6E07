"""Pydantic data models shared by the staging pipeline and the chat agent.

Pydantic gives us validated, typed configuration and a schema we can hand to
the LLM (via LangChain's `with_structured_output`) so its replies are parsed
and checked rather than free-form text.
"""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class StageConfig(BaseModel):
    """Settings for the ingest step (crawl -> Markdown on disk)."""

    root_url: str
    data_dir: Path = Path("data")          # /data/ is git-ignored in this repo
    max_pages: int = Field(50, ge=1, le=5000)
    timeout_ms: int = Field(30_000, ge=1_000)
    # Skip non-HTML assets: they are not "relevant" pages and can't become Markdown.
    skip_extensions: tuple[str, ...] = (
        ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
        ".zip", ".gz", ".mp4", ".mp3", ".css", ".js", ".xml", ".json",
    )

    @field_validator("root_url")
    @classmethod
    def _add_scheme(cls, v: str) -> str:
        # Accept bare "example.com" like the crawler does.
        v = v.strip()
        return v if "://" in v else "https://" + v


class StagedPage(BaseModel):
    """One crawled page that has been written to disk as Markdown."""

    url: str
    title: str = ""
    path: Path


class AgentConfig(BaseModel):
    """Settings for the chat agent."""

    data_dir: Path = Path("data")
    chat_model: str = "llama3.1"            # any chat model pulled in Ollama
    embed_model: str = "nomic-embed-text"   # any embedding model pulled in Ollama
    ollama_url: str = "http://localhost:11434"
    chunk_size: int = Field(1000, ge=100)
    chunk_overlap: int = Field(150, ge=0)
    top_k: int = Field(4, ge=1, le=20)      # chunks retrieved per question
    history_turns: int = Field(3, ge=0)     # past Q/A pairs given to the model


class AgentAnswer(BaseModel):
    """Structured reply the LLM must produce (validated by Pydantic)."""

    answer: str = Field(description="Answer to the question, based only on the provided context.")
    found_in_context: bool = Field(
        description="True only if the context actually contains the answer."
    )
