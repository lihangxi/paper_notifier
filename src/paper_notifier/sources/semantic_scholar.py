from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import requests

from ..models import Paper
from ..utils import days_ago, utc_now


def fetch_semantic_scholar(
    query: str,
    limit: int,
    days_back: int,
    api_key: str,
) -> List[Paper]:
    if not query:
        return []

    fields = [
        "title",
        "authors",
        "abstract",
        "venue",
        "year",
        "url",
        "publicationDate",
    ]
    params = {
        "query": query,
        "limit": max(1, min(limit, 100)),
        "fields": ",".join(fields),
    }
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        response = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params=params,
            headers=headers,
            timeout=20,
        )
    except requests.RequestException:
        return []

    if response.status_code == 429:
        return []

    response.raise_for_status()
    data = response.json().get("data", [])

    cutoff = days_ago(days_back)
    papers: List[Paper] = []
    for item in data:
        published = _parse_publication_date(item) or utc_now()
        if published < cutoff:
            continue
        authors = [author.get("name", "").strip() for author in item.get("authors", [])]
        authors = [author for author in authors if author]
        papers.append(
            Paper(
                title=(item.get("title") or "").strip(),
                authors=authors or ["Unknown"],
                abstract=item.get("abstract") or "",
                summary="",
                url=item.get("url") or "",
                source=item.get("venue") or "Semantic Scholar",
                published=published,
            )
        )

    return papers


def _parse_publication_date(item: dict) -> datetime | None:
    value = (item.get("publicationDate") or "").strip()
    if value:
        try:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    year = item.get("year")
    if isinstance(year, int):
        return datetime(year, 1, 1, tzinfo=timezone.utc)

    return None
