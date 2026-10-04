from typing import Optional

import trafilatura


def extract_text(url: str) -> Optional[str]:
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return None
        text = trafilatura.extract(downloaded)
        return text
    except Exception as e:
        print(f"[trafilatura] extraction failed for {url}: {e}")
        return None
