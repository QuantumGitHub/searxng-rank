import pytest

from src.models import SearchResult


def _make_reranker():
    try:
        from src.ranking.flashrank import FlashRankReranker

        return FlashRankReranker()
    except Exception as e:  # model not cached / no network -> skip, don't fail
        pytest.skip(f"FlashRank model not available in this env: {e!r}")


@pytest.fixture(scope="module")
def reranker():
    return _make_reranker()


def test_rerank_orders_relevant_first(reranker):
    results = [
        SearchResult(
            title="Weather", url="https://b.example",
            snippet="The weather is nice today.",
        ),
        SearchResult(
            title="Black holes",
            url="https://a.example",
            snippet="A black hole is a region of spacetime where gravity is so "
            "strong nothing escapes.",
        ),
    ]
    out = reranker.rerank_with_content("what is a black hole", results, top_k=5)
    assert len(out) == 2
    assert out[0].url == "https://a.example"  # relevant passage ranked first
    assert out[0].score > out[1].score


def test_rerank_empty_input_is_safe(reranker):
    # The old FlashRank heuristic crashed the model on an empty batch; it must not.
    assert reranker.rerank_with_content("q", [], top_k=5) == []
    assert reranker.rerank("q", [], top_k=5) == []
