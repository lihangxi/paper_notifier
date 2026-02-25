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
