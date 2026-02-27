from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Iterable

import feedparser

from ..models import Paper
from ..utils import days_ago, utc_now


def fetch_rss(feeds: Iterable[str], days_back: int) -> list[Paper]:
    feed_list = [feed for feed in feeds if feed]
    if not feed_list:
        return []

    cutoff = days_ago(days_back)
    papers: list[Paper] = []

    for feed_url in feed_list:
        feed = feedparser.parse(feed_url)
        source_name = feed.feed.get("title", "RSS") if isinstance(feed.feed, Mapping) else "RSS"
        for entry in feed.entries:
            if not isinstance(entry, Mapping):
                continue
            published = _entry_published(entry) or utc_now()
            if published < cutoff:
                continue

            title = (entry.get("title") or "").strip()
            summary = entry.get("summary") or entry.get("description") or ""
            url = entry.get("link") or ""
            authors = _entry_authors(entry)

            papers.append(
                Paper(
                    title=title,
                    authors=authors or ["Unknown"],
                    abstract=summary,
                    summary="",
                    url=url,
                    source=source_name,
                    published=published,
                )
            )

    return papers


def _entry_published(entry: dict[str, object]) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def _entry_authors(entry: dict[str, object]) -> list[str]:
    authors = []
    raw_authors = entry.get("authors", [])

    if isinstance(raw_authors, str):
        name = raw_authors.strip()
        if name:
            authors.append(name)
    elif isinstance(raw_authors, list):
        for author in raw_authors:
            if isinstance(author, str):
                name = author.strip()
            elif isinstance(author, Mapping):
                name = str(author.get("name", "")).strip()
            else:
                name = ""

            if name:
                authors.append(name)

    if not authors:
        fallback_author = entry.get("author")
        if isinstance(fallback_author, str):
            author = fallback_author.strip()
            if author:
                authors.append(author)
        elif isinstance(fallback_author, Mapping):
            author = str(fallback_author.get("name", "")).strip()
            if author:
                authors.append(author)

    return authors
