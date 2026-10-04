# Prior art

`searxng-rank` is deliberately inspired by existing work, while reimplementing
its contract **self-hosted over any reachable SearXNG** and keeping the LLM
loop in the host. This page records what we drew from and where we differ.

> `searxng-rank` is our own name. It is **not affiliated with, or a reference
> to, Exa** — Exa is cited here only as the source of the *shape* (a search
> call that returns ranked results plus a grounded, cited answer).

## Exa (the "shape" inspiration)

[Exa](https://exa.ai/) is a commercial web-search API whose core idea is: a
single search call returns **ranked results together with an answer that is
grounded in and cites them**. That "results + cited answer" contract is the
part we replicate — but over SearXNG rather than a proprietary index, and with
the semantic loop left to the calling model instead of a hosted one.

## Vane (the closest open-source Perplexity)

[Vane](https://github.com/ItzCrazyKns/Vane) (formerly Perplexica, ~36.7k★, MIT)
is the reference open-source Perplexity and the closest prior art. Its
architecture (TypeScript / Next.js) already implements much of our pipeline:

- Question classification + standalone query rewrite.
- An LLM tool-calling research loop with **mode-based iteration budgets**
  (`speed`=2, `balanced`=6, `quality`=25).
- A SearXNG JSON client; multi-source (web / academic / social / uploads).
- **Bi-encoder reranking**: embed query + each result, cosine similarity,
  threshold filter, in-memory dedup.
- Quality mode: LLM result picker → Playwright/Chromium + Readability scraper →
  chunking → LLM fact extractor.
- URL-level dedup/merge and a citation-writing prompt.
- Provider abstraction (`BaseLLM` / `BaseEmbedding`) over OpenAI, Anthropic,
  Gemini, Groq, Ollama, LM Studio, Lemonade, and local `transformers.js`
  embeddings.

**What we borrow:** the classify → multi-query → iterate pattern, the
mode/iteration-budget concept, the extraction prompts, and the cited-answer
UX/prompting.

**What we do differently:**

- **Form factor: an MCP service, not a self-contained app.** Vane is a full
  Next.js application with its own UI and server; every user must adopt Vane's
  stack. Our orchestrator is reachable through a thin MCP tool surface, so any
  LLM host (Open WebUI, OpenCode, Claude, custom agents) can use it without
  vendoring our UI. One search/rerank core, many hosts.
- **Persistent, versioned embedding cache.** Vane recomputes embeddings for
  every result on every query and discards them. We cache by content hash in
  per-manifest collections (Qdrant), so popular content is embedded once and
  reused across queries and users — the core quality/cost win
  (see [embedding.md](embedding.md)).
- **Cross-encoder reranking.** Vane stops at bi-encoder cosine; we add
  FlashRank as a second-stage reranker with a quality threshold.
- **Primary-source filter.** Vane's picker favours reputable sources but does
  not verify that cited sources exist or support the claims. Our Tier-2 judge
  does.
- **Collaborative / decentralized registry + cold storage.** Vane is
  single-node SQLite. Ours is designed to share URLs/CIDs across nodes
  (Phase 4).

## Other references

- [`dzhng/deep-research`](https://github.com/dzhng/deep-research) — the
  simplest iterative query-generation loop (breadth/depth).
- [`open-deep-research`](https://github.com/langchain-ai/open-deep-research)
  — provider/MCP-agnostic deep-research agent.
- **Onyx / DeerFlow / Khoj / Feynman** — self-hosted deep-research agents with
  different trade-offs (scale, RAG, on-device).

## A note on local prior art

During development a working copy of Vane was kept on disk next to this repo
(outside the published tree) to study its classifier/researcher and reranking.
It is **not** part of this repository — only the *ideas* are, and they are
credited above.
