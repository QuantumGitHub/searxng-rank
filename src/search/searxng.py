import os
from typing import Optional

import httpx

from src.models import SearchResult


class SearXNGClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (
            base_url
            or os.getenv("SEARXNG_BASE_URL", "http://localhost:8888")
        )
        self.search_url = f"{self.base_url.rstrip('/')}/search"
        self.client = httpx.Client(timeout=15.0)

    def search(
        self,
        query: str,
        max_results: int = 10,
        pageno: int = 1,
        categories: str = "general",
    ) -> list[SearchResult]:
        params = {
            "q": query,
            "format": "json",
            "language": "en",
            "categories": categories,
            "pageno": pageno,
        }
        try:
            resp = self.client.get(self.search_url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[searxng] search failed: {e}")
            return []

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                engine=item.get("engine", ""),
            ))
        return results

    def close(self):
        self.client.close()
