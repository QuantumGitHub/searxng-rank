# searxng-rank

Self-hosted web search that gets you closer to "Perplexity-quality" answers
**without a proprietary index**. It runs the classic research loop — retrieve,
extract, rerank, evaluate, iterate — over **any reachable
[SearXNG](https://docs.searxng.org/)** instance, and exposes the result two
different ways:

- **An orchestrator service** that owns the iterative search/evaluate loop and
  calls any OpenAI-compatible LLM directly.
- **A thin, LLM-free MCP tool surface** (`search` / `fetch` / `rerank`) for
  agent hosts (Open WebUI, OpenCode, Claude, a raw vLLM + tool-calling model)
  that want to drive their own loop.

The two surfaces share one deterministic core: SearXNG retrieval → page-text
extraction (Trafilatura) → cross-encoder rerank (FlashRank) → dedup, plus
bounded "widen" retries when results look weak.

> **Inspiration, not affiliation.** The *shape* of this tool is inspired by the
> [Exa](https://exa.ai/) search API (a search call that returns ranked results
> *plus* a grounded, cited answer), and its architecture draws on
> [Vane](https://github.com/ItzCrazyKns/Vane) (the open-source Perplexity).
> `searxng-rank` is our own name for reimplementing that contract self-hosted
> over SearXNG — it is not affiliated with, or a reference to, Exa.

## Why SearXNG?

SearXNG is a self-hostable metasearch aggregator over hundreds of engines.
Pointing the pipeline at "any reachable SearXNG" means no API keys, no
per-query cost, and a stable endpoint you control. The project degrades
gracefully: if SearXNG is unreachable it tells you so instead of crashing, and
if the reranker model is missing it falls back to SearXNG's own ordering.

## Quickstart

### Docker (recommended)

`docker compose up -d` brings up the whole stack together:

- **SearXNG** (`searxng/searxng:latest`) on `:8888`, configured from
  [`settings/settings.yml`](settings/settings.yml) (JSON format on, limiter off).
- **the MCP server** (`searxng-rank-mcp`) on `:8000`.
- **Qdrant** on `:6333` (vector store; the Phase-2 cache).

The MCP server finds SearXNG automatically by compose service name, so nothing
else to configure. Then point a host at
`http://<host>:8000/mcp/` (see [MCP integration](docs/mcp-integration.md)).

### Dev (uv)

```bash
uv sync                              # install dependencies
uv run searxng-rank "your query"      # run the orchestrator end-to-end
uv run searxng-rank-mcp               # run the MCP tool server (:8000)
uv run pytest                        # tests
uv run ruff check .                  # lint
```

Run a SearXNG separately (bundled image or your own) and set
`SEARXNG_BASE_URL=http://<host>:<port>` for the MCP to find it — discovery
tries an explicit URL first, then the same-network service name, then common
local defaults (see below).

## How it works

```
User query
  → (any reachable) SearXNG            retrieve, multi-engine
  → Trafilatura                        extract clean page text
  → FlashRank cross-encoder            rerank (quality threshold ~0.7)
  → dedup + bounded widen               retry / widen when results look weak
  → [orchestrator] LLM evaluate        "is this enough? what's the next query?"
  → loop (refine) or synthesize answer with [n] citations
```

The **deterministic** part (everything above the LLM) is shared by both
surfaces and is LLM-free. The **semantic** part (is this enough? what's the
next query? what's the answer?) lives in the host LLM — the orchestrator calls
it directly; the MCP surface lets *your* host model own it.

Results carry a stable `[n] Title — URL` marker plus a short `Signals:` block
(score stats, threshold, domain diversity) so an LLM can judge coverage and
cite sources as `[n]` / markdown links.

## Configuration

All configuration is via environment variables (see
[`.env.example`](.env.example)):

| Variable | Purpose | Default |
|----------|---------|---------|
| `SEARXNG_BASE_URL` | Explicit SearXNG endpoint (highest precedence) | — |
| `SEARXNG_SERVICE_HOST` / `SEARXNG_SERVICE_PORT` | Same-network service to probe | `searxng` / `8080` |
| `SEARXNG_SECRET_KEY` | SearXNG instance secret (compose only) | — |
| `MCP_HOST` / `MCP_PORT` | MCP bind address | `0.0.0.0` / `8000` |
| `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` | LLM for the orchestrator (OpenAI-compatible) | Ollama / `llama3.1:8b` |
| `QDRANT_URL` | Vector store | `http://localhost:6333` |
| `FLASHRANK_MODEL` | Cross-encoder model | `ms-marco-TinyBERT-L-2-v2` |
| `MAX_ITERATIONS` | Orchestrator loop budget | `3` |
| `QUALITY_THRESHOLD` | Rerank pass threshold | `0.7` |

**SearXNG discovery** (see `src/search/discovery.py`): an explicit
`SEARXNG_BASE_URL` wins; otherwise the same-network service name is tried;
then common local defaults. Every candidate is probed with a cheap
`GET /search?format=json`; the first that answers is used. A *healthy* result
is cached and sticky; an *unhealthy* one is **re-probed** on the next call, so a
late-starting SearXNG recovers without a restart.

## MCP integration

The MCP surface is **Streamable HTTP** (the transport Open WebUI supports
natively). One server hosts many clients; the host model drives the loop and
cites results. See [docs/mcp-integration.md](docs/mcp-integration.md) for
per-host setup (Open WebUI, OpenCode, raw vLLM) and how citations render.

## Project layout

```
searxng-rank/
├── src/
│   ├── main.py                 CLI entry point (orchestrator)
│   ├── pipeline.py             LLM-free SearchService (search/fetch/rerank)
│   ├── models.py               shared dataclasses
│   ├── search/                 SearXNG client + endpoint discovery
│   ├── extraction/             Trafilatura page-text extraction
│   ├── ranking/                FlashRank cross-encoder reranker
│   ├── orchestration/          LLM client + iterative search/evaluate loop
│   └── mcp/                   Streamable-HTTP MCP tool surface
├── tests/                       unit + in-memory-MCP + re-probe tests
├── settings/settings.yml         SearXNG config (JSON on, limiter off)
├── docker-compose.yml            SearXNG + MCP + Qdrant
├── Dockerfile                    the MCP server image
└── docs/                        architecture, MCP integration, prior art, embedding
```

## Status

A working Phase-1 search: any-reachable-SearXNG provider, cross-encoder
rerank with quality threshold, iterative orchestrator loop, and the LLM-free
MCP tool surface. The persistent collaborative content/embedding cache
(Phase 4) is design-only — see [docs/embedding.md](docs/embedding.md) and
[docs/prior-art.md](docs/prior-art.md).

## License

MIT — see [LICENSE](LICENSE).
