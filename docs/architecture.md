# Architecture

`searxng-rank` is **one core, two surfaces**. The core is a deterministic,
LLM-free retrieval pipeline; the two surfaces wrap it for two different kinds
of consumer.

## The core (LLM-free)

`src/pipeline.py` exposes `SearchService`, a stateless facade over the
SearXNG → extract → rerank → dedup chain. It makes **no LLM calls**.

```
SearXNG search (multi-engine, JSON)
  → Trafilatura page-text extraction (top results)
  → FlashRank cross-encoder rerank (quality threshold, ~0.7)
  → dedup (best score per URL)
  → bounded "widen" retry when results look weak (second page, re-rerank)
```

Each stage degrades gracefully rather than crashing:

- **No SearXNG** → the service reports `unhealthy` and a clear warning instead
  of an exception. Discovery re-probes on the next call, so a late-starting
  SearXNG recovers without a restart.
- **No reranker model** (missing weights, wrong arch, no network) → results keep
  SearXNG's own ordering with ordinal scores, and the output says so.
- **No extractable content** → the result still returns with its snippet.

The pipeline returns a `SearchOutcome`: ranked `SearchResult`s plus a
`signals` dict (top/median score, threshold, how many passed, distinct domains,
how many have content). The stable `[n] Title — URL` text format and the
`Signals:` block are what the host LLM reads to judge coverage and cite sources.

## The two surfaces

### 1. Orchestrator service

`src/orchestration/loop.py` + `llm.py` own the *semantic* loop and call any
OpenAI-compatible LLM **directly**:

```
for iteration in range(MAX_ITERATIONS):
    search (SearXNG) → extract (top results) → dedup → rerank
    evaluate: is this sufficient?  (LLM, JSON)
      → sufficient: synthesize a cited answer, stop
      → not: refine the query (LLM suggestion), repeat
```

Configured independently via `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL`, so
the provider key can live in one shared gateway (e.g. LiteLLM). This is the
"self-contained" path: a user points a client at the orchestrator and gets a
synthesized, cited answer.

### 2. Thin MCP tool surface

`src/mcp/server.py` exposes the core as three **stateless tools** over
Streamable HTTP, with **no LLM in the server**:

- **`search(query, max_results, extract, widen)`** — the "fat" retrieval tool:
  runs the full pipeline (widen/retry when scores are weak) and returns ranked,
  deduped results plus the `Signals:` block.
- **`fetch(url, max_chars)`** — a thin escape hatch: read one URL in full.
- **`rerank(query, passages)`** — re-score passages the host already has.

The **host model owns the loop** here: it decides when results are sufficient
and what to search next, then writes the cited answer. This is the
flexibility path — one search/rerank core, many hosts (Open WebUI, OpenCode,
Claude, a raw vLLM + tool-calling model) — without any host vendoring our UI
or a second LLM.

> Server-controlled *semantic* iteration (deciding the next query) needs an
> LLM and therefore lives in the orchestrator, never inside MCP. MCP tools are
> LLM-free but non-trivial.

## Why this split

- **Single source of truth.** Both surfaces run the exact same pipeline, so
  quality is identical whether a user adopts the orchestrator or drives it from
  their own agent.
- **LLM cost is a host decision.** The MCP path keeps exactly one LLM in the
  loop (the host's), and the server never samples.
- **No single point of failure.** Search works with no cache and with no
  orchestrator; the (Phase-2/4) cache only improves quality.

## What's deferred (Phase 2 / 4)

Design-only, not implemented in the current tree:

- **Edge embeddings + Qdrant collection cache** — embed at the edge, store in
  per-manifest collections (see [embedding.md](embedding.md)).
- **Primary-source filter** — two-tier (domain heuristic + LLM judge) content
  quality gate for what gets cached.
- **Collaborative / decentralized registry + cold storage** — IPFS / OrbitDB /
  Filecoin for a shared, self-freshening content store.

See [prior-art.md](prior-art.md) for how these relate to Vane and the other
open-source research agents.
