"""LLM-free search core.

This is the deterministic half of the pipeline: SearXNG retrieval, page
extraction, cross-encoder reranking, and dedup -- with no LLM calls anywhere.

It is deliberately decoupled from the orchestrator's LLM loop so that the thin
MCP tool surface can reuse the exact same machinery. The *semantic* loop (is
this enough? what's the next query?) always lives in the host LLM, never here.
"""

import logging
import os
import statistics
from dataclasses import dataclass, field
from typing import Optional

from src.extraction import trafilatura
from src.models import SearchResult
from src.search import discovery
from src.search.searxng import SearXNGClient

logger = logging.getLogger("searxng-rank.pipeline")

# --- FlashRank is a *quality* feature, not a requirement -------------------
# The reranker loads an ONNX cross-encoder that may be absent (wrong arch, no
# network, model not downloaded). We import it lazily and degrade to SearXNG's
# own ordering rather than crashing the whole server.
_RERANKER = None
_RERANK_ERROR: Optional[str] = None


def _get_reranker():
    global _RERANKER, _RERANK_ERROR
    if _RERANKER is not None or _RERANK_ERROR is not None:
        return _RERANKER
    try:
        from src.ranking.flashrank import FlashRankReranker

        _RERANKER = FlashRankReranker()
    except Exception as e:  # noqa: BLE001 - surface any load failure as "unavailable"
        _RERANK_ERROR = f"{type(e).__name__}: {e}"
    return _RERANKER


def reset_reranker() -> None:
    """Clear the reranker cache (used by tests)."""
    global _RERANKER, _RERANK_ERROR
    _RERANKER = None
    _RERANK_ERROR = None


def _safe_rerank(reranker, query: str, results: list[SearchResult], top_k: int):
    """Run the cross-encoder rerank; on *any* failure mark the reranker broken
    and return ``None`` so callers fall back to SearXNG ordering.

    Guards both construction and inference: some model/library combos build
    fine but raise at ``session.run`` (e.g. a BERT export wanting
    ``token_type_ids`` the tokenizer didn't feed). That must degrade, not crash
    the tool.
    """
    global _RERANKER, _RERANK_ERROR
    if reranker is None:
        return None
    try:
        return reranker.rerank_with_content(query, results, top_k=top_k)
    except Exception as e:  # noqa: BLE001 - any inference failure means "degrade"
        _RERANK_ERROR = f"flashrank rerank failed: {type(e).__name__}: {e}"
        _RERANKER = None
        logger.warning("FlashRank unavailable; using SearXNG order. %s", _RERANK_ERROR)
        return None


@dataclass
class RankedPassage:
    id: str
    title: str
    text: str
    score: float = 0.0


@dataclass
class SearchOutcome:
    query: str
    base_url: str
    healthy: bool
    results: list[SearchResult] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    widened: bool = False

    def _fmt_result(self, r: SearchResult, idx: int, content_chars: int) -> str:
        body = (r.content or r.snippet or "").strip()
        if len(body) > content_chars:
            body = body[:content_chars].rstrip() + " …"
        lines = [f"[{idx}] {r.title} — {r.url}"]
        if r.engine:
            lines.append(f"  engine: {r.engine}   score: {r.score:.3f}")
        if body:
            lines.append(f"  {body}")
        return "\n".join(lines)

    def to_text(self, content_chars: int = 500, top: int = 10) -> str:
        """Human/LLM-readable text with stable ``[n] Title — URL`` citation
        anchors plus a short sufficiency-signals block for the host LLM to read."""
        out = self.results[:top]
        header = (
            f"SearXNG results for: \"{self.query}\" "
            f"({len(self.results)} total, source: {self.base_url})"
        )
        if not self.healthy:
            header += "  [warning: no SearXNG endpoint was reachable at startup]"

        body = "\n\n".join(
            self._fmt_result(r, i + 1, content_chars) for i, r in enumerate(out)
        ) or "(no results)"

        sig = self.signals
        reranker = sig.get("reranker", "n/a")
        sig_block = (
            "Signals:\n"
            f"  reranker: {reranker}\n"
            f"  top_score: {sig.get('top_score', 'n/a')}   "
            f"median: {sig.get('median_score', 'n/a')}   "
            f"threshold: {sig.get('threshold', 'n/a')}   "
            f"passed: {sig.get('passed', 0)}/{len(self.results)}\n"
            f"  distinct_domains: {sig.get('distinct_domains', 'n/a')}   "
            f"with_content: {sig.get('with_content', 0)}/{len(self.results)}\n"
            f"  widened: {'yes' if self.widened else 'no'}"
        )

        if reranker.startswith("fallback"):
            sig_block += (
                "\n  note: FlashRank unavailable; results keep SearXNG order, "
                "scores are ordinal (top = 1.0)."
            )

        return f"{header}\n\n{body}\n\n{sig_block}"


class SearchService:
    """Stateless, LLM-free facade over the SearXNG -> extract -> rerank chain."""

    def __init__(self, base_url: Optional[str] = None):
        d = discovery.discover_searxng()
        self.base_url = (base_url or d.base_url).rstrip("/")
        self.healthy = d.healthy
        self.quality_threshold = float(os.getenv("QUALITY_THRESHOLD", "0.7"))
        self._explicit = base_url is not None
        self._searxng = SearXNGClient(base_url=self.base_url) if self.base_url else None

    def _re_resolve(self) -> None:
        """Re-run SearXNG discovery while we are unhealthy.

        SearXNG can take a few seconds to boot after the container starts; if the
        first probe missed it, we keep re-probing (discovery re-probes an unhealthy
        cache) until it answers -- so a late-starting SearXNG recovers without a
        restart. Once healthy, discovery caches the result and this stops firing.
        No-op when an explicit ``base_url`` was supplied.
        """
        if self._explicit:
            return
        d = discovery.discover_searxng(timeout=2.0)
        self.base_url = (d.base_url or "").rstrip("/")
        self.healthy = d.healthy
        if self.healthy:
            self._searxng = SearXNGClient(base_url=self.base_url)

    # -- internal helpers ---------------------------------------------------
    def _reranker(self):
        return _get_reranker()

    def _extract(self, results: list[SearchResult], limit: int) -> int:
        count = 0
        for r in results[:limit]:
            if r.content is None:
                text = trafilatura.extract_text(r.url)
                if text:
                    r.content = text[:5000]
                    count += 1
        return count

    def _score_fallback(self, results: list[SearchResult]) -> list[SearchResult]:
        for i, r in enumerate(results):
            r.score = max(0.0, 1.0 - 0.05 * i)
        return results

    def _compute_signals(self, results: list[SearchResult], reranker: str) -> dict:
        scores = [r.score for r in results]
        domains = {r.url.split("/")[2] if len(r.url.split("/")) > 2 else r.url for r in results}
        return {
            "reranker": reranker,
            "top_score": round(max(scores), 3) if scores else 0.0,
            "median_score": round(statistics.median(scores), 3) if scores else 0.0,
            "threshold": self.quality_threshold,
            "passed": sum(1 for s in scores if s >= self.quality_threshold),
            "distinct_domains": len(domains),
            "with_content": sum(1 for r in results if r.content),
        }

    @staticmethod
    def _dedup(results: list[SearchResult]) -> list[SearchResult]:
        best: dict[str, SearchResult] = {}
        for r in results:
            if r.url not in best or r.score > best[r.url].score:
                best[r.url] = r
        out = list(best.values())
        out.sort(key=lambda x: x.score, reverse=True)
        return out

    # -- public API ----------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        extract: bool = True,
        widen: bool = True,
        content_limit: int = 5,
    ) -> SearchOutcome:
        # If we last came up unhealthy, retry discovery: SearXNG may have finished
        # booting since the first probe. (No-op once healthy, or with an explicit URL.)
        if not self.healthy and not self._explicit:
            self._re_resolve()
        if self._searxng is None:
            return SearchOutcome(query=query, base_url=self.base_url, healthy=self.healthy)

        results = self._searxng.search(query, max_results=max_results)
        widened = False
        if extract:
            self._extract(results, content_limit)

        reranker = self._reranker()
        ranked = _safe_rerank(reranker, query, results, max_results)
        if ranked is not None:
            results = self._dedup(ranked)
            mode = "flashrank"
        else:
            self._score_fallback(results)
            mode = "fallback"
        signals = self._compute_signals(results, mode)

        # One bounded, deterministic widen when results look weak.
        weak = (
            signals["passed"] < max(1, max_results // 4)
            or signals["top_score"] < self.quality_threshold
        )
        if widen and results and weak:
            more = self._searxng.search(query, max_results=max_results, pageno=2)
            if extract:
                self._extract(more, content_limit)
            merged = self._dedup(results + more)
            ranked_merged = _safe_rerank(self._reranker(), query, merged, max_results)
            if ranked_merged is not None:
                merged = self._dedup(ranked_merged)
            signals = self._compute_signals(merged, signals["reranker"])
            if len(merged) > len(results):
                results = merged
                widened = True

        return SearchOutcome(
            query=query,
            base_url=self.base_url,
            healthy=self.healthy,
            results=results,
            signals=signals,
            widened=widened,
        )

    def fetch(self, url: str, *, max_chars: int = 5000) -> str:
        text = trafilatura.extract_text(url)
        if not text:
            return f"No text could be extracted from {url}."
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + " …[truncated]"
        return text

    def rerank(
        self,
        query: str,
        passages: list[RankedPassage],
        *,
        top_k: Optional[int] = None,
    ) -> tuple[list[RankedPassage], str]:
        """Rerank ``passages`` against ``query``.

        Returns ``(ranked, mode)`` where ``mode`` is ``"flashrank"`` or
        ``"fallback"`` (original order, ordinal scores) so callers can report
        whether real cross-encoder scores were used.
        """
        if not passages:
            return [], "fallback"
        rr = self._reranker()
        pseudo = [
            SearchResult(title=p.title, url=p.id, snippet=p.text, content=p.text)
            for p in passages
        ]
        k = top_k or len(passages)
        ranked = _safe_rerank(rr, query, pseudo, k)
        if ranked is None:
            for i, p in enumerate(passages):
                p.score = max(0.0, 1.0 - 0.05 * i)
            return passages, "fallback"
        by_id = {p.id: p for p in passages}
        out: list[RankedPassage] = []
        for r in ranked:
            p = by_id.get(r.url)
            if p is None:
                continue
            p.score = r.score
            out.append(p)
        return out, "flashrank"
