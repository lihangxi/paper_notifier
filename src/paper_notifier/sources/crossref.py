from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..models import Paper
from ..utils import days_ago


def _first_text(values: list[str], default: str) -> str:
    return (values[0] if values else default).strip()


def _crossref_authors(item: dict[str, object]) -> list[str]:
    authors: list[str] = []
    for author in item.get("author", []):
        given = author.get("given", "").strip()
        family = author.get("family", "").strip()
        full = " ".join(part for part in (given, family) if part)
        if full:
            authors.append(full)
    return authors


def _crossref_published(item: dict[str, object]) -> datetime:
    parts = item.get("published", {}).get("date-parts", [[1970, 1, 1]])[0]
    if not isinstance(parts, list) or not parts:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

    year = int(parts[0]) if len(parts) >= 1 else 1970
    month = int(parts[1]) if len(parts) >= 2 else 1
    day = int(parts[2]) if len(parts) >= 3 else 1
    return datetime(year, month, day, tzinfo=timezone.utc)


def _build_crossref_params(
    query: str,
    rows: int,
    from_date: str,
    until_date: str,
    mailto: str,
) -> dict[str, str | int]:
    params = {
        "query.title": query,
        "rows": rows,
        "sort": "published",
        "order": "desc",
        "filter": f"from-pub-date:{from_date},until-pub-date:{until_date}",
    }
    if mailto:
        params["mailto"] = mailto
    return params


def fetch_crossref(query: str, rows: int, days_back: int, mailto: str) -> list[Paper]:
    cutoff = days_ago(days_back)
    from_date = cutoff.date().isoformat()
    until_date = datetime.now(timezone.utc).date().isoformat()
    params = _build_crossref_params(query, rows, from_date, until_date, mailto)

    response = requests.get("https://api.crossref.org/works", params=params, timeout=20)
    response.raise_for_status()
    message = response.json().get("message", {})
    items = message.get("items", [])

    papers: list[Paper] = []
    for item in items:
        published = _crossref_published(item)
        if published < cutoff:
            continue
        title = _first_text(item.get("title") or [], "")
        authors = _crossref_authors(item)
        venue = _first_text(item.get("container-title") or [], "Crossref")
        abstract = item.get("abstract", "")
        url = item.get("URL", "")
        papers.append(
            Paper(
                title=title,
                authors=authors or ["Unknown"],
                abstract=abstract or "No abstract provided.",
                summary="",
                url=url,
                source=venue,
                published=published,
            )
        )
    return papers
