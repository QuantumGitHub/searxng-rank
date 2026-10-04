# MCP integration

The MCP surface is a **Streamable-HTTP** server (not stdio/SSE) on
`http://<host>:8000/mcp/` — the transport [Open WebUI](https://openwebui.com/)
supports natively. One server hosts many clients; the **host model owns the
search loop** and cites the results.

The server exposes three stateless tools:

| Tool | Purpose |
|------|---------|
| `search` | Full retrieval: SearXNG → extract → rerank → dedup, plus bounded widen/retry. Returns ranked `[n] Title — URL` blocks + a `Signals:` block. |
| `fetch` | Read one URL in full (Trafilatura). |
| `rerank` | Re-score passages the host already has. |

No LLM calls happen in the server. Sampling (server-side LLM tool use) is
deprecated as of MCP protocol `2026-07-28`, so the host model is the only LLM
in the loop.

## Open WebUI

1. Run the server (`docker compose up -d` brings up `searxng-rank-mcp` on
   `:8000`, or `uv run searxng-rank-mcp` on the host).
2. **Settings → Admin → Integrations → External Tool Servers**.
3. Type = **MCP (Streamable HTTP)**, URL e.g.
   `http://searxng-rank-mcp:8000/mcp/` (same Docker network) or
   `http://host.docker.internal:8000/mcp/` (server on the host).
4. Adding MCP servers is **admin-only**; grant users access via Access Control.
   Use Auth = **None** on a local Docker network and set `WEBUI_SECRET_KEY`.
5. Set the model's function calling to **Native** — MCP tools require a model
   with reliable native tool calling.

Because Open WebUI's native "source chips" come from its own web-search/RAG
pipeline (not from external MCP output), citations here render as the model's
markdown: each `search` result carries a stable `[n] Title — URL` header, and the
tool description asks the model to cite `[n]` and use markdown links
`[Title](url)`, which Open WebUI renders everywhere. A Filter function can
post-process an answer into the chip shape if chips are required.

## OpenCode / Claude / any MCP host

Any MCP-capable host works: register a **Streamable-HTTP** server at the
`/mcp/` URL. The host's model calls `search`, reads the ranked results and the
`Signals:` block, decides whether to call `search` again with a refined query,
and writes a cited answer. Point it at any reachable SearXNG
(`SEARXNG_BASE_URL`, or the same-network service name, or a local default).

## Raw vLLM + tool-calling model

For hosts without an MCP SDK, the server is a plain HTTP endpoint. A
vLLM tool-calling model can be given the three tools' JSON schemas and the
loop is: call the endpoint, parse the returned text, decide the next query,
repeat. The same `[n]`/`Signals:` contract applies, so the model's reasoning
about coverage is identical to the MCP path.

## How citations look

`search` returns, per result, a header the model can cite verbatim:

```
[1] Weather — https://en.wikipedia.org/wiki/Weather
  The atmosphere is the layer of gases ...

[2] Black hole — https://en.wikipedia.org/wiki/Black_hole
  A black hole is a region of spacetime ...

Signals:
  reranker: flashrank
  top_score: 0.91   median: 0.74   threshold: 0.7   passed: 8/8
  distinct_domains: 6   with_content: 7/8
  widened: no
```

The model is expected to cite `[n]` and/or markdown links in its final answer,
and to read the `Signals:` block to decide whether coverage is sufficient or a
further (refined) search is needed.
