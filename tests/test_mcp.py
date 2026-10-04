import pytest

from mcp import Client
from src.mcp.server import mcp


def _tool_text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


@pytest.mark.anyio
async def test_search_tool(offline_service):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search",
            {"query": "rust async runtime", "max_results": 3, "extract": True, "widen": True},
        )
        text = _tool_text(result)
    assert "[1]" in text
    assert "Alpha Page — https://a.example/1" in text
    assert "Signals:" in text


@pytest.mark.anyio
async def test_fetch_tool(offline_service):
    async with Client(mcp) as client:
        result = await client.call_tool("fetch", {"url": "https://a.example/1", "max_chars": 100})
        text = _tool_text(result)
    assert "extracted body for https://a.example/1" in text


@pytest.mark.anyio
async def test_rerank_tool_fallback(offline_service):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "rerank",
            {
                "query": "q",
                "passages": [
                    {"id": "u1", "title": "T1", "text": "x"},
                    {"id": "u2", "title": "T2", "text": "y"},
                ],
            },
        )
        text = _tool_text(result)
    assert "[1]" in text
    assert "u1" in text
    assert "u2" in text


@pytest.mark.anyio
async def test_search_survives_broken_reranker(offline_service, monkeypatch):
    """A constructible reranker that throws at inference must degrade, not crash."""

    class Broken:
        def rerank_with_content(self, query, results, top_k=10):
            raise ValueError("Required inputs (['token_type_ids']) are missing")

    monkeypatch.setattr("src.pipeline._get_reranker", lambda: Broken())
    async with Client(mcp) as client:
        result = await client.call_tool("search", {"query": "x", "extract": False, "widen": False})
        text = _tool_text(result)
    assert "[1]" in text
    assert "fallback" in text
