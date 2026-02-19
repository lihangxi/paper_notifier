from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from urllib.parse import quote_plus

import feedparser

from ..models import Paper
from ..utils import days_ago


def fetch_arxiv(query: str, max_results: int, days_back: int) -> List[Paper]:
    encoded = quote_plus(query)
    url = (
        "http://export.arxiv.org/api/query?search_query=all:"
        f"{encoded}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    )
    feed = feedparser.parse(url)
    cutoff = days_ago(days_back)
    papers: List[Paper] = []

    for entry in feed.entries:
        published = datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if published < cutoff:
            continue
        authors = [author.name for author in entry.authors]
        papers.append(
            Paper(
                title=entry.title.replace("\n", " ").strip(),
                authors=authors,
                abstract=entry.summary or "",
                impact="",
                url=entry.link,
                source="arXiv",
                published=published,
            )
        )
    return papers
