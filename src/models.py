from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    content: Optional[str] = None
    score: float = 0.0
    engine: str = ""


@dataclass
class LoopState:
    query: str
    iteration: int = 0
    max_iterations: int = 3
    results: list[SearchResult] = field(default_factory=list)
    refined_queries: list[str] = field(default_factory=list)
    final_answer: Optional[str] = None
    is_sufficient: bool = False
