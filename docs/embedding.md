# EMBEDDING.md

Design notes for the vector layer. Vectors are the expensive, reusable artifact; content is cheap and content-addressed. Keep embedding out of the shared service — it happens at the edge.

## Goal

Minimal infrastructure, low cost, no per-query embedding service:

1. Clients load the embedding model locally and send **raw vectors** to the store.
2. The store never embeds; it only does ANN over vectors it is given.
3. Content is fetched once and embedded once per collection, then reused across queries and users.

This is why the default model must be self-hostable: it is the only way the "client embeds, store stores" path works without shared GPU infrastructure. Commercial embedding APIs reintroduce a per-query network call and cost, and cannot run at the edge — so they are limited to private, single-tenant collections (see below).

## Collections

A vector is only comparable to another vector from the **same vector space**. A collection is the unit of that space, identified by its full manifest:

```
collection_id = hash(
    model_id,
    model_weights_cid,   # content-addressed model weights
    version,
    dim,
    pooling,             # e.g. mean
    normalization,       # e.g. l2
    distance_metric,     # e.g. cosine
    preprocessing,       # tokenizer / prompt template, if any
)
```

Rules:

- **One model+version per collection.** Vectors from different models are never mixed.
- **Multiple collections may run concurrently.** A newer/better model becomes a new collection; consumers subscribe to whichever collections they trust.
- **No global "best model" governor.** There is no central authority. Reputation over collections (usage, staleness reports) is the Phase 4 mechanism; until then a single canonical bootstrap collection is the default.

## Default collections

- `bge-m3` — multilingual, 1024-dim, strong general retrieval. Good default when quality matters.
- `nomic-embed-text-v1.5` — 768-dim, matryoshka (truncatable), 137M params, runs acceptably on CPU. Good edge default.

Both are self-hostable. The canonical bootstrap collection exists only so the network is not empty on day one; it is not mandated.

## Edge embedding flow

```
client (model loaded locally)
  query ──► embed ──► vector ──┐
                               ▼
                    vector store (Qdrant / sqlite-vec)
                    stores vectors + content CIDs only
                    API: upsert(collection_id, cid, vector)
                         search(collection_id, query_vector, k)
```

The store is a dumb ANN box. It can be per-user, per-node, or shared. No API keys, no model downloads, no embedding on the server.

## Popular collections get reinforced

- Content is content-addressed (`CID`). Vectors live in a separate `(CID, collection_id) -> vector` map, so a page is embedded **once per collection, permanently**.
- Re-embedding is a **deduplicated background job** keyed on `(CID, model_version)`. It only runs for collections that are actually used.
- Cost therefore scales with the popularity of collections, not with the number of users. Unpopular collections are nearly free: content already exists, only a one-time embed is added.
- This is the core quality/cost win over prior art (Vane recomputes embeddings for every result on every query and discards them).

## Commercial embedding APIs

Permitted **only** for private, single-tenant collections, and only when:

1. The provider exposes a stable, pinnable model snapshot id; and
2. Every node sharing that collection uses the same account/credentials.

Otherwise the collection cannot be part of the shared network. The collaborative default stays self-hostable.

## Decentralized retrieval (Phase 4, deferred)

Exact cosine similarity over IPFS is **not** the goal — a hash gives no notion of "near". Similarity is an index-navigation problem, so the design is a decentralizable ANN index:

- **IVF + PQ sharding.** Cluster the space into centroids; store each centroid's posting list as a product-quantized shard. Publish each shard under its own CID.
- **Fetch only the nearest shards.** A query retrieves the nearest centroids' shards (typically a few percent of the corpus), never the whole database. This resolves the "user would need the entire DB cached" concern; recall is deliberately approximate.
- **IPFS/OrbitDB are transport and replication**, announcing which shards a peer holds — not the similarity engine.
- **Quantization** keeps shards small enough to fetch on demand.

Existing decentralized vector databases (Glacier DeVector, DANNY, in-browser vector stores) are immature; there is no drop-in. This layer is therefore gated behind a working local rerank + iterative loop. Local rerank alone already beats primitive SearXNG by a wide margin.

## Parking lot (open questions)

- Governance/reputation for choosing which collections to trust (no global best model).
- Incentives (if any) for peers that serve shards; billing is currently out of scope.
- Whether a canonical bootstrap embedding model is desirable or should be avoided entirely.
- Cold storage: when vectors are LRU-evicted, do we archive them with content (Filecoin) or allow recomputation?
