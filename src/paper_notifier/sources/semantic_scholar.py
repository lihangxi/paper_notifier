from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..models import Paper
from ..utils import days_ago, utc_now


def _semantic_headers(api_key: str) -> dict[str, str]:
    if not api_key:
        return {}
    return {"x-api-key": api_key}


def _semantic_params(query: str, limit: int) -> dict[str, str | int]:
    fields = [
        "title",
        "authors",
        "abstract",
        "venue",
        "year",
        "url",
        "publicationDate",
    ]
    return {
        "query": query,
        "limit": max(1, min(limit, 100)),
        "fields": ",".join(fields),
    }


def _semantic_authors(item: dict[str, object]) -> list[str]:
    return [
        name
        for name in (author.get("name", "").strip() for author in item.get("authors", []))
        if name
    ]


def fetch_semantic_scholar(
    query: str,
    limit: int,
    days_back: int,
    api_key: str,
) -> list[Paper]:
    if not query:
        return []

    params = _semantic_params(query, limit)
    headers = _semantic_headers(api_key)

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

    if not response.ok:
        return []

    data = response.json().get("data", [])

    cutoff = days_ago(days_back)
    papers: list[Paper] = []
    for item in data:
        published = _parse_publication_date(item) or utc_now()
        if published < cutoff:
            continue
        authors = _semantic_authors(item)
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


def _parse_publication_date(item: dict[str, object]) -> datetime | None:
    value = (item.get("publicationDate") or "").strip()
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    year = item.get("year")
    if isinstance(year, int):
        return datetime(year, 1, 1, tzinfo=timezone.utc)

    return None
