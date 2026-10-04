import src.search.discovery as discovery


def test_explicit_base_url_wins(monkeypatch):
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://myhost:9999")
    discovery.reset_cache()
    try:
        d = discovery.discover_searxng(timeout=0.001, use_cache=False)
        assert d.base_url == "http://myhost:9999"
    finally:
        discovery.reset_cache()


def test_service_name_is_first_candidate(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.setenv("SEARXNG_SERVICE_HOST", "sxn")
    monkeypatch.setenv("SEARXNG_SERVICE_PORT", "1234")
    discovery.reset_cache()
    try:
        # Nothing is actually listening, so every probe fails; the resolver must
        # still return the service-name candidate first (and mark it unhealthy).
        d = discovery.discover_searxng(timeout=0.001, use_cache=False)
        assert d.candidates[0] == "http://sxn:1234"
        assert d.base_url == "http://sxn:1234"
        assert d.healthy is False
    finally:
        discovery.reset_cache()


def test_candidate_order_and_dedup(monkeypatch):
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.delenv("SEARXNG_SERVICE_HOST", raising=False)
    monkeypatch.delenv("SEARXNG_SERVICE_PORT", raising=False)
    discovery.reset_cache()
    try:
        d = discovery.discover_searxng(timeout=0.001, use_cache=False)
        # default: service name (searxng:8080) first, then local defaults, de-duped
        assert d.candidates[0] == "http://searxng:8080"
        assert "http://localhost:8080" in d.candidates
        assert "http://localhost:8888" in d.candidates
        assert len(d.candidates) == len(set(d.candidates))
    finally:
        discovery.reset_cache()
