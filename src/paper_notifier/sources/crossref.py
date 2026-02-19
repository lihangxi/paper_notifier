from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import requests

from ..models import Paper
from ..utils import days_ago


def fetch_crossref(query: str, rows: int, days_back: int, mailto: str) -> List[Paper]:
    cutoff = days_ago(days_back)
    from_date = cutoff.date().isoformat()
    until_date = datetime.now(timezone.utc).date().isoformat()
    params = {
        "query.title": query,
        "rows": rows,
        "sort": "published",
        "order": "desc",
        "filter": f"from-pub-date:{from_date},until-pub-date:{until_date}",
    }
    if mailto:
        params["mailto"] = mailto

    response = requests.get("https://api.crossref.org/works", params=params, timeout=20)
    response.raise_for_status()
    message = response.json().get("message", {})
    items = message.get("items", [])

    papers: List[Paper] = []
    for item in items:
        published_parts = item.get("published", {}).get("date-parts", [[1970, 1, 1]])[0]
        published = datetime(*published_parts, tzinfo=timezone.utc)
        if published < cutoff:
            continue
        title = (item.get("title") or [""])[0].strip()
        authors = []
        for author in item.get("author", []):
            given = author.get("given", "").strip()
            family = author.get("family", "").strip()
            full = " ".join(part for part in [given, family] if part)
            if full:
                authors.append(full)
        venue = (item.get("container-title") or ["Crossref"])[0]
        abstract = item.get("abstract", "")
        url = item.get("URL", "")
        papers.append(
            Paper(
                title=title,
                authors=authors or ["Unknown"],
                abstract=abstract or "No abstract provided.",
                impact="",
                url=url,
                source=venue,
                published=published,
            )
        )
    return papers
