from dataclasses import dataclass
from datetime import datetime


@dataclass
class Paper:
    title: str
    authors: list[str]
    abstract: str
    summary: str
    url: str
    source: str
    published: datetime
    # Populated by the knowledge-base relevance filter when it keeps the paper.
    kb_score: float | None = None
    kb_top_match: str = ""
