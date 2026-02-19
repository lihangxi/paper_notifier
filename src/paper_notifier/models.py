from dataclasses import dataclass
from datetime import datetime
from typing import List


@dataclass
class Paper:
    title: str
    authors: List[str]
    abstract: str
    impact: str
    url: str
    source: str
    published: datetime
