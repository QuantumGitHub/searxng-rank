"""Hermetic tests for the re-probe behavior of SearXNG discovery.

The rule under test: a *cached healthy* result is sticky, but a *cached
unhealthy* result is never final -- the next call re-probes so a late-starting
SearXNG (e.g. still booting after the container starts) recovers without a
restart.
"""
import pytest

import src.search.discovery as discovery


class _FakeClient:
    def __init__(self, base_url=None):
        self.base_url = base_url

    def search(self, query, max_results=10, pageno=1, categories="general"):
        from src.models import SearchResult

        return [SearchResult(title="R", url="http://r", snippet="s")]

    def close(self):
        pass


def _clear(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("SEARXNG_SERVICE_HOST", raising=False)
    monkeypatch.delenv("SEARXNG_SERVICE_PORT", raising=False)


@pytest.fixture
def no_env(monkeypatch):
    _clear(monkeypatch)
    discovery.reset_cache()
    monkeypatch.setattr("src.pipeline.SearXNGClient", _FakeClient)
    yield
    discovery.reset_cache()


def test_unhealthy_result_is_reprobed_and_recovers(no_env, monkeypatch):
    monkeypatch.setattr(discovery, "_probe", lambda url, timeout: False)
    d1 = discovery.discover_searxng(timeout=0.001)
    assert d1.healthy is False

    # The first candidate "comes up" -- a re-resolve must find it (not short-circuit).
    first = d1.candidates[0]
    monkeypatch.setattr(discovery, "_probe", lambda url, timeout: url == first)
    d2 = discovery.discover_searxng(timeout=0.001)
    assert d2.healthy is True and d2.base_url == first


def test_healthy_result_is_sticky(no_env, monkeypatch):
    monkeypatch.setattr(discovery, "_probe", lambda url, timeout: True)
    d1 = discovery.discover_searxng(timeout=0.001)
    assert d1.healthy is True

    # Even if the endpoint later stops answering, the cached healthy result is sticky.
    monkeypatch.setattr(discovery, "_probe", lambda url, timeout: False)
    d2 = discovery.discover_searxng(timeout=0.001)
    assert d2 is d1 and d2.healthy is True


def test_search_service_re_resolves_when_unhealthy(no_env, monkeypatch):
    from src.pipeline import SearchService

    up = "http://127.0.0.1:2"
    monkeypatch.setenv("SEARXNG_SERVICE_HOST", "127.0.0.1")
    monkeypatch.setenv("SEARXNG_SERVICE_PORT", "2")

    monkeypatch.setattr(discovery, "_probe", lambda url, timeout: False)
    svc = SearchService()
    assert svc.healthy is False and svc._explicit is False

    # The endpoint "comes up" before the first search(); search() re-resolves and recovers.
    monkeypatch.setattr(discovery, "_probe", lambda url, timeout: url == up)
    out = svc.search("q", max_results=3, extract=False, widen=False)
    assert svc.healthy is True and out.healthy is True
    assert out.base_url == up


def test_search_service_explicit_url_does_not_re_resolve(no_env, monkeypatch):
    from src.pipeline import SearchService

    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    seen = []

    def probe(url, timeout):
        seen.append(url)
        return False

    monkeypatch.setattr(discovery, "_probe", probe)
    # Pin the URL via the constructor (not the env) -> search() must not re-probe.
    svc = SearchService("http://127.0.0.1:1")
    assert svc._explicit is True and svc.healthy is False

    n_at_init = len(seen)
    svc.search("q", max_results=3, extract=False, widen=False)
    # An explicit base URL is used verbatim, so search() must not re-probe discovery.
    assert len(seen) == n_at_init
