# AGENTS.md

## Project intent

Self-hosted search that replicates Perplexity's quality (hybrid retrieval + multi-stage reranking + iterative LLM evaluation loop) without a proprietary index. The system has two separable surfaces:

- **Orchestrator service** — owns the iterative search/evaluate loop and calls LLM APIs directly.
- **Thin MCP tool surface** — stateless tools (`search` / `fetch` / `rerank`) for agent hosts that drive their own loop. No LLM calls inside MCP: sampling is deprecated as of MCP protocol `2026-07-28`.

Live search is provided by **any reachable SearXNG** instance. A persistent, collaborative content/embedding cache is Phase 4 (deferred).

## Current state

Runnable prototype:

- `src/search/searxng.py` — SearXNG JSON client
- `src/search/discovery.py` — auto-resolves a reachable SearXNG (env override > same-network service name > local probes)
- `src/extraction/trafilatura.py` — page text extraction
- `src/ranking/flashrank.py` — FlashRank cross-encoder reranker (optional; degrades to SearXNG order if the model is unavailable)
- `src/pipeline.py` — LLM-free `SearchService` (search / fetch / rerank) shared by both surfaces
- `src/mcp/server.py` — Streamable-HTTP MCP tool surface (`search` / `fetch` / `rerank`), v2 `MCPServer`
- `src/orchestration/llm.py` — OpenAI-compatible LLM client (evaluate + synthesize)
- `src/orchestration/loop.py` — iterative search/evaluate/refine loop
- `src/main.py` — CLI entry point
- `tests/` — unit + in-memory-MCP-client tests

Scaffolded only (config/env, no client code): Qdrant, embeddings, primary-source filter.
Design only (Phase 4): IPFS/OrbitDB/Filecoin, collaborative URL registry.
This is the `searxng-rank` repo (self-contained; prior art is not vendored here). Prior art lives outside it: `../Vane/` (a clone of Vane/Perplexica) and the original exploration in `docs/notes/convo-gemma4-exa-replacement.md`.

## Architecture summary

```
User Query → Planner (classify + rewrite + multi-query)
  → (optional) vector-cache lookup
  → any reachable SearXNG → Trafilatura/Crawl4AI extraction
  → primary source filter → edge embed + index (Phase 2+)
  → FlashRank rerank → LLM evaluate (sufficient?)
  → loop (refine query) or synthesize answer
```

Two surfaces, one core:

- **Orchestrator** owns the loop and makes LLM calls directly (any OpenAI-compatible API).
- **MCP surface** exposes **LLM-free** tools; the host agent owns the semantic loop. `search` is a high-level retrieval tool that runs the orchestrator's deterministic pipeline underneath (multi-query fan-out, extraction, rerank, dedup, cache) and returns ranked results plus sufficiency signals; `fetch`/`rerank` are thin escape hatches for hosts that want fine-grained control.

Key layers:

- **Planning layer**: query classification, standalone rewrite, multi-query generation (`orchestration/planner.py`)
- **Search layer**: any reachable SearXNG (live) + optional Qdrant cache (Phase 2)
- **Extraction layer**: Trafilatura (text) + Crawl4AI (JS-rendered pages)
- **Ranking layer**: FlashRank cross-encoder reranker + quality threshold (~0.7) with re-query
- **Orchestration layer**: LLM-driven iterative loop — search → extract → evaluate → refine → repeat
- **Storage layer (Phase 4)**: IPFS for content, OrbitDB for URL→CID registry, vector index, Filecoin cold archive
- **Collaborative layer (Phase 4)**: replicated URL registry, user-report staleness detection, primary-source filtering

## Design decisions (honor these)

- **Primary source filter**: Only cache content that contains references, citations, or original data. Two-tier: domain heuristics (Tier 1, ~1ms) + LLM judge that verifies cited sources actually exist and support the claims (Tier 2). Blocks LLM-generated slop without verifiable sources. The extra latency is worth it.
- **Embedding collections**: Vectors belong to *collections*, not one global database. A collection is identified by its full vector-space manifest — `(model, weights_cid, version, dim, pooling, normalization, metric)` — hashed into a `collection_id`. One model+version per collection; multiple collections may run concurrently. **Embedding happens at the edge**: the client loads the model locally and sends raw vectors; the store never embeds. Popular collections are reinforced; re-embedding is a deduplicated background job keyed on `(CID, collection, model_version)`. Default is a self-hostable model (`bge-m3` or `nomic-embed-text`). Commercial embedding APIs are permitted for private single-tenant collections only. See `docs/embedding.md`.
- **Freshness**: Not managed with TTLs. The cache is naturally self-freshening: every SearXNG search consults the live web AND cached embeddings in parallel. Stale content is passively replaced as newly crawled versions get stored.
- **LLM layer**: Any OpenAI-compatible API with the user's own endpoint/key. LiteLLM is the optional default gateway — it covers 100+ providers so we do not implement per-provider support. The orchestrator integrates directly with LLM APIs; LLM-calling MCP servers (sampling) are deprecated and are not used. The search API owns its **own** LLM connection chain (planning/sufficiency/synthesis), configured independently (`LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL`); point it at a shared LiteLLM gateway so provider keys live in one place. An optional per-request model override lets a host match the model it is already using (nice-to-have, not Phase 1).
- **Query planning**: In-house `orchestration/planner.py` — classify (skip search? which sources?) → rewrite to a standalone question → generate 2–4 targeted queries → refine from evaluation. Modeled on `dzhng/deep-research` and Vane's classifier/researcher. No LangChain/LlamaIndex dependency.
- **Tool granularity (MCP)**: MCP tools are LLM-free but not trivial. `search` is a "fat" retrieval tool: it runs a bounded, deterministic retrieval loop (widen/retry when rerank scores are weak) and returns ranked, deduped results plus sufficiency signals (score stats, source diversity, coverage). The host LLM owns only the semantic decision (enough? next query?), keeping a single LLM in the loop. Server-controlled *semantic* iteration needs an LLM and therefore lives in the orchestrator service (plain HTTP API), never inside MCP.
- **Collaborative trust** (Phase 4): URL registry is shared — likely via OrbitDB (preferred) or simpler pubsub/gossip. Users report page staleness. Majority + domain reputation weighting resolves conflicts.
- **Cold storage** (Phase 4): LRU-evicted content goes to Filecoin deals. CID + deal ID are announced on the registry so the network knows where to find them.
- **No single point of failure**: Search works with no cache (pure SearXNG) and with no orchestrator. The cache improves quality but is not required.

## MCP surface and host integration

**Terminology**: *MCP* is a protocol for exposing tools to an LLM host (OpenCode, Open WebUI, Claude); it is only one way to reach our core. The *search API* is the actual HTTP service that performs a search request (the orchestrator pipeline). One core, two interfaces: MCP tools and a plain HTTP/OpenAPI search endpoint. Hosts pick whichever they support.

The MCP surface is **Streamable HTTP** (not stdio/SSE) — the only transport Open WebUI supports natively (v0.6.31+). One server hosts many clients; the host agent owns the loop.

**Open WebUI**
- Add under **Settings → Admin → Integrations → External Tool Servers**, Type = **MCP (Streamable HTTP)**, URL e.g. `http://searxng-rank-mcp:8000/mcp/`.
- Adding MCP servers is **admin-only**; users get access via Access Control. Use Auth = **None** on a local Docker network and set `WEBUI_SECRET_KEY`.
- In Docker, an MCP server on the host is reached via `http://host.docker.internal:<port>`.
- Set the model's function calling to **Native**; MCP tools require a model with reliable native tool calling.
- stdio/SSE-only servers can be bridged with `mcpo` (MCP → OpenAPI).

**Sources / citations**
- MCP tools return *text content*; the **host LLM writes the final answer**, so citations are whatever that model emits. Open WebUI has no automatic citation-chip channel for external MCP tool output.
- Reliable path: each tool result carries a stable `[n] Title — URL` header, the tool description asks the model to cite `[n]` and to use markdown links `[Title](url)`. Open WebUI renders markdown links everywhere.
- The favicon **source chips** are produced by Open WebUI's own RAG/web search, not by external tools. If chips are required, an Open WebUI Filter function can post-process the answer into that shape.

**Chip-friendly engine seam (Phase 2+)**
- Exa/Tavily/Brave/etc. are **not MCP** — they are built-in *Web Search Engine* providers in Open WebUI's native search pipeline, which is why they get chips.
- The generic equivalent is the **External** provider: Admin → Web Search → Engine = `external`, with a `POST {query, count}` endpoint returning `[{link, title, snippet}]`. Because it occupies the engine slot, it inherits the native result/citation pipeline.
- Exposing that same contract from our core (rerank behind it, missing sources returned as `link/title/snippet`) is the chip-friendly Open WebUI path, alongside MCP for other hosts. Deferred; chips are not a Phase 1 requirement.

## Prior art

**Vane (formerly Perplexica)** — `ItzCrazyKns/Vane`, 36.7k★, MIT — is the reference open-source Perplexity and the closest prior art. Its architecture already implements much of our pipeline in TypeScript/Next.js:

- Question classification + standalone query rewrite.
- An LLM tool-calling research loop with mode-based iteration budgets (`speed`=2, `balanced`=6, `quality`=25).
- SearXNG JSON client; multi-source (web/academic/social/uploads).
- Bi-encoder reranking: embed query + each result, cosine similarity, threshold filter, in-memory dedup.
- Quality mode: LLM result picker → Playwright/Chromium + Readability scraper → chunking → LLM fact extractor.
- URL-level dedup/merge and a citation-writing prompt.
- Provider abstraction (`BaseLLM`/`BaseEmbedding`) over OpenAI, Anthropic, Gemini, Groq, Ollama, LM Studio, Lemonade, and **local `transformers.js` embeddings**.

**What we borrow:** the classify → multi-query → iterate pattern, mode/iteration-budget concept, extraction prompts, and cited-answer UX/prompting.

**What we do differently (our differentiators):**

- **Form factor: MCP service, not a self-contained app.** Vane is a full Next.js application with its own UI and server; every user must adopt Vane's stack. Our orchestrator is reachable through a thin MCP tool surface, so any LLM host (OpenWebUI, OpenCode, Claude, custom agents) can use it without vendoring our UI. That is the flexibility play: one search/rerank core, many hosts.
- **Persistent, versioned embedding cache.** Vane recomputes embeddings for every result on every query and discards them. We cache by content hash in collections (Qdrant), so popular content is embedded once and reused across queries and users. This is the core quality/cost win.
- **Cross-encoder reranking.** Vane stops at bi-encoder cosine; we add FlashRank as a second-stage reranker with a quality threshold.
- **Primary-source filter.** Vane's picker favours reputable sources but does not verify that cited sources exist or support the claims. Our Tier-2 judge does.
- **Collaborative/decentralized registry + cold storage.** Vane is single-node SQLite. Ours is designed to share URLs/CIDs across nodes (Phase 4).

Other references: `dzhng/deep-research` (simplest iterative query-generation loop, breadth/depth), `open-deep-research` (provider/MCP-agnostic), Onyx / DeerFlow / Khoj / Feynman (self-hosted deep-research agents).

**Exa** — the commercial web-search API whose *shape* inspired this project: a search call that returns ranked results plus an answer grounded in them, each carrying a citation marker. We reimplement that contract self-hosted over any reachable SearXNG. Note the product name `searxng-rank` is ours and is deliberately **not** a reference to Exa.

## Starting points for implementation

1. `src/orchestration/planner.py` — NEW: classify + rewrite + multi-query generation
2. `src/orchestration/loop.py` — iterative search-evaluation loop (exists; needs fixes)
3. `src/search/searxng.py` — provider accepting any reachable SearXNG (multi-URL + auto-provision)
4. `src/ranking/flashrank.py` — cross-encoder reranker with quality threshold
5. `src/mcp/` — NEW: thin MCP tool surface (`search` / `fetch` / `rerank`)
6. `src/storage/qdrant.py` + `src/storage/embeddings.py` — NEW: vector store + edge embedder
7. `src/extraction/primary_source.py` — two-tier content quality filter
8. Phase 4: `src/storage/ipfs_client.py`, `src/storage/orbitdb.py`

## Roadmap / planned work

- **Phase 1 — working search (now)**: any-reachable-SearXNG provider, query planner, iterative-loop fixes, FlashRank cross-encoder threshold.
- **Phase 2 — quality + tools**: edge embeddings + Qdrant collection cache, primary-source filter, thin MCP tool surface (`search`/`fetch`/`rerank`), and an Open WebUI-compatible external search API (`POST {query,count}` → `[{link,title,snippet}]`).
- **Phase 3 — orchestrator hardening**: LiteLLM/provider config (`sk-...` key in one place), cited-answer synthesis.
- **Phase 4 — deferred**: IPFS/OrbitDB/Filecoin, decentralized registry, decentralized ANN index (see `docs/embedding.md`).
- **TODO — Open WebUI integration PR**: expose the search API through Open WebUI's `external` web-search engine, then open a pull request upstream so results from our search API render as native **source chips** (like Exa/Tavily) rather than requiring a Filter-function workaround.

## Dependencies

Core: `httpx`, `trafilatura`, `flashrank`, `openai`, `python-dotenv`, `qdrant-client`, ONNX Runtime / `llama.cpp` (edge embedder), an MCP SDK.
Phase 4: IPFS + OrbitDB + Filecoin.
Optional: LiteLLM (provider gateway).

## Commands

- `uv sync` — install dependencies
- `uv run searxng-rank "your query"` — run the orchestrator CLI
- `uv run searxng-rank-mcp` — run the MCP tool server (Streamable HTTP, default `0.0.0.0:8000`)
- `uv run ruff check` — lint
- `uv run pytest` — tests
- `docker compose up -d` — SearXNG + Qdrant + `searxng-rank-mcp` (MCP on `:8000`)

## Key constraints

- Do NOT add API-key-gated search providers as the primary path. SearXNG is the default. Cloud APIs are optional for the LLM layer only.
- The cache NEVER replaces live search — it supplements it. Cache hits serve only as a speed optimization.
- Distribution is collaborative: no central authority controls what gets indexed. Quality emerges from the primary source filter + community verification.
- Embedding collections are per-manifest; clients embed at the edge; the default model must be self-hostable (see `docs/embedding.md`).
- The iterative loop is a standalone orchestrator service. MCP exposure is thin tools only — never an LLM gateway.
