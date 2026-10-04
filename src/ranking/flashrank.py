import os
from typing import Any, Dict, List, Optional

import numpy as np
from flashrank import Ranker

from src.models import SearchResult

# The FlashRank 0.2.x `Ranker.rerank` only feeds `token_type_ids` into the ONNX
# graph *when the tokenizer emits a non-zero value* (`not np.all(type_ids == 0)`).
# That heuristic is wrong for models whose ONNX export declares `token_type_ids`
# as a **required** input (this BERT does): an empty or all-zero batch then omits
# the tensor and onnxruntime raises "Required inputs (['token_type_ids']) ...".
# This is a library bug and is 100% architecture-independent (it is pure input-name
# validation, identical on x86_64 / aarch64 / any Docker). We therefore build the
# ONNX feed ourselves here, always including every input the model declares.


class FlashRankReranker:
    def __init__(self, model_name: Optional[str] = None):
        model_name = model_name or os.getenv(
            "FLASHRANK_MODEL", "ms-marco-TinyBERT-L-2-v2"
        )
        self.ranker = Ranker(model_name=model_name)
        self._input_names = {i.name for i in self.ranker.session.get_inputs()}

    def _score(self, query: str, passages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Score `passages` against `query`, best-first. Returns [] for empty input.

        Each returned item is the original passage dict with a float `score` added.
        """
        if not passages:
            return passages

        # FlashRank's `tokenizers`-based encode of a [query, passage] pair yields
        # per-segment `type_ids` (query=0, passage=1); we forward them verbatim.
        encoded = self.ranker.tokenizer.encode_batch(
            [[query, p["text"]] for p in passages]
        )
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        feed = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        # Only feed tensors the model's graph actually declares as inputs.
        feed = {k: v for k, v in feed.items() if k in self._input_names}

        (logits,) = self.ranker.session.run(None, feed)
        if logits.shape[1] == 1:
            scores = 1.0 / (1.0 + np.exp(-logits.flatten()))
        else:
            exp_logits = np.exp(logits)
            scores = exp_logits[:, 1] / np.sum(exp_logits, axis=1)

        for p, s in zip(passages, scores):
            p["score"] = float(s)
        passages.sort(key=lambda x: x["score"], reverse=True)
        return passages

    def rerank(
        self, query: str, results: list[SearchResult], top_k: int = 10
    ) -> list[SearchResult]:
        passages = [
            {"id": r.url, "text": r.snippet, "title": r.title} for r in results
        ]
        scored_passages = self._score(query, passages)[:top_k]

        scored: list[SearchResult] = []
        for item in scored_passages:
            for r in results:
                if r.url == item["id"]:
                    r.score = item["score"]
                    scored.append(r)
                    break
            else:
                scored.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("id", ""),
                        snippet=item.get("text", ""),
                        score=item["score"],
                    )
                )
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

    def rerank_with_content(
        self, query: str, results: list[SearchResult], top_k: int = 5
    ) -> list[SearchResult]:
        passages = [
            {
                "id": r.url,
                "text": r.content or r.snippet,
                "title": r.title,
            }
            for r in results
        ]
        scored_passages = self._score(query, passages)[:top_k]

        scored: list[SearchResult] = []
        seen = set()
        for item in scored_passages:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            for r in results:
                if r.url == item["id"]:
                    r.score = item["score"]
                    scored.append(r)
                    break
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored
