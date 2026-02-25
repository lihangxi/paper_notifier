from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser

from ..models import Paper
from ..utils import days_ago


def _arxiv_query_url(query: str, max_results: int) -> str:
    encoded = quote_plus(query)
    return (
        "http://export.arxiv.org/api/query?search_query=all:"
        f"{encoded}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    )


def _arxiv_entry_published(entry) -> datetime:
    return datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _arxiv_entry_authors(entry) -> list[str]:
    return [author.name for author in entry.authors]


def fetch_arxiv(query: str, max_results: int, days_back: int) -> list[Paper]:
    url = _arxiv_query_url(query, max_results)
    feed = feedparser.parse(url)
    cutoff = days_ago(days_back)
    papers: list[Paper] = []

    for entry in feed.entries:
        published = _arxiv_entry_published(entry)
        if published < cutoff:
            continue
        authors = _arxiv_entry_authors(entry)
        papers.append(
            Paper(
                title=entry.title.replace("\n", " ").strip(),
                authors=authors,
                abstract=entry.summary or "",
                summary="",
                url=entry.link,
                source="arXiv",
                published=published,
            )
        )
    return papers
