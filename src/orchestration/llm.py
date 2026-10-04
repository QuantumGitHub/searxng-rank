import json
import os
from typing import Optional

import httpx
from openai import OpenAI

SYSTEM_EVALUATE = """You are a research quality control judge. Your job is to
evaluate whether the search results collected so far are sufficient to answer
the user's query.

Return a JSON object with these fields:
- "sufficient": boolean — true if enough information has been gathered
- "reasoning": string — brief explanation of your decision
- "refined_query": string — if not sufficient, a refined search query to get
  missing information; if sufficient, an empty string
- "missing_aspects": list of strings — specific aspects still missing (empty
  if sufficient)

Be strict: only mark sufficient if the results directly answer the query
with supporting evidence."""


SYSTEM_SYNTHESIZE = """You are a research assistant. Synthesize a
comprehensive answer from the provided search results.

Rules:
- Cite sources using [1], [2], etc. corresponding to the source numbers
  provided
- If sources conflict, note the disagreement
- Be concise but thorough
- Use markdown formatting"""


EVALUATE_EXAMPLE = {
    "sufficient": False,
    "reasoning": "Results cover the basics but lack technical implementation details",
    "refined_query": "RAG implementation LangChain detailed guide 2025",
    "missing_aspects": ["implementation details", "code examples"],
}


class LLMClient:
    def __init__(
        self,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_base = api_base or os.getenv("LLM_API_BASE", "http://localhost:11434/v1")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "ollama")
        self.model = model or os.getenv("LLM_MODEL", "llama3.1:8b")

        self.client = OpenAI(
            base_url=self.api_base,
            api_key=self.api_key,
            http_client=httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)),
        )

    def evaluate(self, query: str, context: list[tuple[str, str, str]]) -> dict:
        context_str = "\n\n".join(
            f"[{i+1}] Title: {title}\nURL: {url}\nContent: {content[:2000]}"
            for i, (title, url, content) in enumerate(context)
        )

        messages = [
            {"role": "system", "content": SYSTEM_EVALUATE},
            {
                "role": "user",
                "content": (
                    "## User Query\n"
                    f"{query}\n\n"
                    "## Collected Results\n"
                    f"{context_str}\n\n"
                    "Is this sufficient? Respond with JSON only."
                ),
            },
        ]

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            print(f"[llm] evaluation failed: {e}")
            return {
                "sufficient": True,
                "reasoning": "Failed to evaluate, defaulting to sufficient",
                "refined_query": "",
                "missing_aspects": [],
            }

    def synthesize(self, query: str, context: list[tuple[str, str, str, float]]) -> str:
        context_str = "\n\n".join(
            f"[{i + 1}] Title: {title}\nURL: {url}\n"
            f"Relevance: {score:.3f}\nContent: {content[:3000]}"
            for i, (title, url, content, score) in enumerate(context)
        )

        messages = [
            {"role": "system", "content": SYSTEM_SYNTHESIZE},
            {
                "role": "user",
                "content": (
                    "## User Query\n"
                    f"{query}\n\n"
                    "## Sources\n"
                    f"{context_str}\n\n"
                    "Synthesize an answer using the sources above."
                ),
            },
        ]

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[llm] synthesis failed: {e}")
            return f"Failed to synthesize answer: {e}"
