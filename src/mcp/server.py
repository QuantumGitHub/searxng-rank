"""Streamable-HTTP MCP server exposing the LLM-free search core as three tools.

Run with:
    uv run searxng-rank-mcp            # console script -> main()
    uv run python -m src.mcp.server   # same, via module

Environment:
    MCP_HOST        bind host  (default 0.0.0.0)
    MCP_PORT        bind port  (default 8000)
    SEARXNG_BASE_URL / SEARXNG_SERVICE_HOST / SEARXNG_SERVICE_PORT
                    how to find SearXNG (see src/search/discovery.py)
    QUALITY_THRESHOLD rerank pass threshold (default 0.7)

No LLM calls happen here. The host model decides when results are sufficient and
what to search next; this server just runs the deterministic retrieval pipeline.
"""

import logging
import os
import sys
from typing import Optional

from pydantic import BaseModel

from mcp.server import MCPServer
from src.pipeline import RankedPassage, SearchService

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("searxng-rank.mcp")

mcp = MCPServer(
    "searxng-rank",
    instructions=(
        "Self-hosted web search over any reachable SearXNG. "
        "Use `search` to gather ranked, extracted results; `fetch` to read a specific URL; "
        "`rerank` to re-score passages you already have. "
        "Each search result carries a stable `[n]` marker -- cite sources as `[n]` "
        "and/or markdown links in your answers."
    ),
)

_SERVICE: Optional[SearchService] = None


def _service() -> SearchService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SearchService()
    return _SERVICE


class Passage(BaseModel):
    """A single passage to rerank. ``id`` is an opaque key (often a URL)."""

    id: str
    title: str = ""
    text: str


@mcp.tool()
def search(query: str, max_results: int = 8, extract: bool = True, widen: bool = True) -> str:
    """Search the web through SearXNG and return ranked, deduplicated results.

    Runs a deterministic pipeline: multi-engine SearXNG query -> page-text
    extraction -> cross-encoder rerank -> dedup, plus a bounded widen/retry when
    results look weak. No LLM is involved; you (the host) decide whether the
    results are sufficient and, if not, call again with a refined query.

    Args:
        query: The standalone search query to run.
        max_results: How many results to return (default 8).
        extract: Also fetch and extract page text for the top results (default true).
        widen: Allow one automatic extra search pass when results look weak (default true).

    Returns:
        Text with one block per result formatted as `[n] Title — URL`, a short
        per-result excerpt, and a final `Signals:` block (score stats, threshold,
        domain diversity) to help judge coverage.
    """
    outcome = _service().search(
        query,
        max_results=max_results,
        extract=extract,
        widen=widen,
    )
    return outcome.to_text()


@mcp.tool()
def fetch(url: str, max_chars: int = 5000) -> str:
    """Fetch a single URL and return its main extracted text (Trafilatura).

    A thin escape hatch for when you already have a URL and just want its content
    (e.g. to read the top result from `search` in full).

    Args:
        url: The absolute URL to fetch.
        max_chars: Maximum number of characters of extracted text to return.

    Returns:
        The extracted page text, truncated to `max_chars`, or a note if nothing
        could be extracted.
    """
    return _service().fetch(url, max_chars=max_chars)


@mcp.tool()
def rerank(query: str, passages: list[Passage]) -> str:
    """Rerank passages you already have, against a query, with the cross-encoder.

    A thin escape hatch for hosts that manage their own candidate set and only
    need re-ranking. If the cross-encoder model is unavailable, passages are
    returned in their original order with ordinal scores and a note.

    Args:
        query: The query to rank the passages against.
        passages: The passages to rank, each an `{id, title, text}` object.

    Returns:
        Text listing each passage as `[n] title — id (score)`, best first.
    """
    pseudo = [RankedPassage(id=p.id, title=p.title, text=p.text) for p in passages]
    ranked, mode = _service().rerank(query, pseudo)
    if not ranked:
        return "No passages to rank."
    lines = [
        f"[{i}] {p.title or p.id} — {p.id}  (score: {p.score:.3f})"
        for i, p in enumerate(ranked, 1)
    ]
    note = (
        "\n(notes: FlashRank unavailable; original order, ordinal scores)"
        if mode == "fallback"
        else ""
    )
    return "\n".join(lines) + note


def main() -> None:
    from dotenv import load_dotenv

    # No-op when no .env is present (e.g. inside the container, where env comes
    # from docker-compose); reads ./.env on a host run of `uv run searxng-mcp`.
    load_dotenv()

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    logger.info("Starting searxng-rank MCP server on %s:%s (Streamable HTTP, /mcp/)", host, port)
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
