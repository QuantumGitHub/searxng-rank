import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeSearxng:
    """Stand-in for SearXNGClient: canned, deterministic, no network."""

    def __init__(self, base_url=None):
        self.base_url = base_url
        self.calls = []

    def search(self, query, max_results=10, pageno=1, categories="general"):
        from src.models import SearchResult

        self.calls.append((query, pageno))
        if pageno == 1:
            return [
                SearchResult(
                    title="Alpha Page", url="https://a.example/1",
                    snippet="alpha snippet", engine="google",
                ),
                SearchResult(
                    title="Beta Page", url="https://b.example/2",
                    snippet="beta snippet", engine="ddg",
                ),
            ]
        return [
            SearchResult(
                title="Gamma Page", url="https://c.example/3",
                snippet="gamma snippet", engine="bing",
            ),
        ]

    def close(self):
        pass


def _fake_discover(*args, **kwargs):
    from src.search.discovery import Discovery

    return Discovery(base_url="http://fake:8080", healthy=True, candidates=["http://fake:8080"])


@pytest.fixture
def offline_service(monkeypatch):
    """Force the pipeline into a fully-offline, deterministic state."""
    import src.mcp.server as server_mod

    monkeypatch.setattr("src.pipeline.SearXNGClient", FakeSearxng)
    monkeypatch.setattr("src.pipeline._get_reranker", lambda: None)
    monkeypatch.setattr("src.search.discovery.discover_searxng", _fake_discover)
    monkeypatch.setattr(
        "src.extraction.trafilatura.extract_text",
        lambda url: "extracted body for " + url,
    )
    monkeypatch.setattr(server_mod, "_SERVICE", None)
    yield
    monkeypatch.setattr(server_mod, "_SERVICE", None)
