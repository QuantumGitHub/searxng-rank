"""Resolve a reachable SearXNG base URL at startup.

Precedence:
  1. ``SEARXNG_BASE_URL``        explicit override (used verbatim).
  2. same-network service name   ``SEARXNG_SERVICE_HOST`` / ``SEARXNG_SERVICE_PORT``
                                 (defaults to the ``searxng`` compose service:8080).
  3. a small list of common local defaults (host / host.docker.internal).

Every candidate is verified with a cheap ``GET /search?...&format=json`` probe.
The first that answers is used. If none answer, the first candidate is returned
anyway (marked unhealthy) so callers get a consistent, actionable error rather
than an exception at import time.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "searxng"
_DEFAULT_PORT = "8080"
_PROBE_PATH = "/search"
_PROBE_PARAMS = {"q": "ping", "format": "json"}


@dataclass
class Discovery:
    base_url: str
    healthy: bool
    candidates: list[str] = field(default_factory=list)


def _candidate_list() -> list[str]:
    cands: list[str] = []

    explicit = os.getenv("SEARXNG_BASE_URL")
    if explicit:
        cands.append(explicit.rstrip("/"))

    host = os.getenv("SEARXNG_SERVICE_HOST", _DEFAULT_HOST)
    port = os.getenv("SEARXNG_SERVICE_PORT", _DEFAULT_PORT)
    cands.append(f"http://{host}:{port}")

    cands.extend(
        [
            "http://localhost:8080",
            "http://localhost:8888",
            "http://host.docker.internal:8080",
        ]
    )

    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _probe(base_url: str, timeout: float) -> bool:
    url = base_url.rstrip("/") + _PROBE_PATH
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params=_PROBE_PARAMS)
        if resp.status_code != 200:
            return False
        data = resp.json()
        return isinstance(data, dict) and "results" in data
    except Exception:  # noqa: BLE001 - any network/parse failure means "unreachable"
        return False


_CACHE: Optional[Discovery] = None


def discover_searxng(timeout: float = 4.0, use_cache: bool = True) -> Discovery:
    """Return the best reachable SearXNG base URL.

    A cached *healthy* result is sticky (probed once). An *unhealthy* result is
    never cached as final: if the last resolution found nothing -- e.g. SearXNG
    was still booting when the server first probed -- the next call re-probes and
    picks the endpoint up once it answers, so a late-starting SearXNG recovers
    without a restart.
    """
    global _CACHE
    if use_cache and _CACHE is not None and _CACHE.healthy:
        return _CACHE

    candidates = _candidate_list()
    for cand in candidates:
        if _probe(cand, timeout):
            logger.info("SearXNG (re)resolved to %s (tried: %s)", cand, candidates)
            _CACHE = Discovery(base_url=cand, healthy=True, candidates=candidates)
            return _CACHE

    best = candidates[0] if candidates else f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
    if _CACHE is None:
        logger.warning(
            "No reachable SearXNG found (tried: %s); defaulting to %s",
            candidates,
            best,
        )
    _CACHE = Discovery(base_url=best, healthy=False, candidates=candidates)
    return _CACHE


def reset_cache() -> None:
    """Clear the discovery cache (used by tests)."""
    global _CACHE
    _CACHE = None
