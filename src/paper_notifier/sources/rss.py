from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List

import feedparser

from ..models import Paper
from ..utils import days_ago, utc_now


def fetch_rss(feeds: Iterable[str], days_back: int) -> List[Paper]:
    feed_list = [feed for feed in feeds if feed]
    if not feed_list:
        return []

    cutoff = days_ago(days_back)
    papers: List[Paper] = []

    for feed_url in feed_list:
        feed = feedparser.parse(feed_url)
        source_name = feed.feed.get("title", "RSS")
        for entry in feed.entries:
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


def _entry_published(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None


def _entry_authors(entry: dict) -> List[str]:
    authors = []
    for author in entry.get("authors", []):
        name = author.get("name", "").strip()
        if name:
            authors.append(name)

    if not authors:
        author = (entry.get("author") or "").strip()
        if author:
            authors.append(author)

    return authors
