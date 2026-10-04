import os

from src.extraction import trafilatura
from src.models import LoopState, SearchResult
from src.orchestration.llm import LLMClient
from src.ranking.flashrank import FlashRankReranker
from src.search.searxng import SearXNGClient


class SearchLoop:
    def __init__(self):
        self.searxng = SearXNGClient()
        self.reranker = FlashRankReranker()
        self.llm = LLMClient()
        self.quality_threshold = float(os.getenv("QUALITY_THRESHOLD", "0.7"))
        self.max_iterations = int(os.getenv("MAX_ITERATIONS", "3"))
        self.max_results_per_round = 5

    def run(self, query: str) -> LoopState:
        state = LoopState(
            query=query,
            max_iterations=self.max_iterations,
        )

        print(f"\n{'='*60}")
        print(f" Query: {query}")
        print(f"{'='*60}\n")

        while state.iteration < state.max_iterations:
            state.iteration += 1
            print(f"\n--- Iteration {state.iteration}/{state.max_iterations} ---")

            search_results = self._search(state)
            state.results.extend(search_results)

            extracted = self._extract_content(search_results)
            state.results = self._deduplicate(state.results)

            self._rerank(state.query, state.results)

            eval_result = self._evaluate(state, extracted)

            if eval_result["sufficient"]:
                print(f"  [eval] Sufficient. Reason: {eval_result['reasoning']}")
                state.is_sufficient = True
                break
            else:
                refined = eval_result.get("refined_query", "")
                print(f"  [eval] Insufficient: {eval_result['reasoning']}")
                if refined and refined != state.query:
                    print(f"  [eval] Refining query: {refined}")
                    state.refined_queries.append(refined)
                    state.query = refined
                else:
                    print("  [eval] No refinement suggestion — stopping.")
                    break

        answer = self._synthesize(state)
        state.final_answer = answer

        self.searxng.close()
        return state

    def _search(self, state: LoopState) -> list[SearchResult]:
        print(f"  [search] Querying SearXNG: \"{state.query}\"")
        results = self.searxng.search(state.query, max_results=10)
        print(f"  [search] Got {len(results)} results from SearXNG")
        for r in results[:3]:
            print(f"    - {r.title[:70]}")
        if len(results) > 3:
            print(f"    ... and {len(results) - 3} more")
        return results

    def _extract_content(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        top = min(len(results), self.max_results_per_round)
        print(f"  [extract] Extracting content from top {top} results...")
        count = 0
        for r in results[:self.max_results_per_round]:
            if r.content is None:
                text = trafilatura.extract_text(r.url)
                if text:
                    r.content = text[:5000]
                    count += 1
                    print(f"    ✓ {r.title[:60]}")
                else:
                    print(f"    ✗ {r.title[:60]} (no content)")
        print(f"  [extract] Extracted {count}/{top} pages")
        return results[:self.max_results_per_round]

    def _rerank(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        print(f"  [rerank] Reranking {len(results)} results with FlashRank...")
        reranked = self.reranker.rerank_with_content(query, results, top_k=10)
        good = [r for r in reranked if r.score >= self.quality_threshold]
        print(
            f"  [rerank] {len(good)}/{len(reranked)} "
            f"passed quality threshold ({self.quality_threshold})"
        )
        for r in reranked[:3]:
            print(f"    {r.score:.3f} {r.title[:60]}")
        return reranked

    def _evaluate(
        self, state: LoopState, _extracted: list[SearchResult]
    ) -> dict:
        print("  [llm] Evaluating sufficiency...")
        context = []
        for r in state.results:
            content = r.content or r.snippet
            if content:
                context.append((r.title, r.url, content))

        result = self.llm.evaluate(state.query, context)
        print(f"  [llm] Sufficient: {result.get('sufficient', '?')}")
        print(f"  [llm] Reasoning: {result.get('reasoning', 'N/A')[:100]}")
        return result

    def _synthesize(self, state: LoopState) -> str:
        print("\n  [llm] Synthesizing final answer...")
        context = []
        seen = set()
        for r in state.results:
            content = r.content or r.snippet
            if content and r.url not in seen:
                seen.add(r.url)
                context.append((r.title, r.url, content, r.score))

        context.sort(key=lambda x: x[3], reverse=True)
        context = context[:10]

        answer = self.llm.synthesize(state.query, context)
        print(f"  [llm] Answer synthesized ({len(answer)} chars)\n")
        return answer

    def _deduplicate(
        self, results: list[SearchResult]
    ) -> list[SearchResult]:
        seen = set()
        deduped = []
        for r in results:
            if r.url not in seen:
                seen.add(r.url)
                deduped.append(r)
        return deduped
