from __future__ import annotations

import html
import re
from typing import List

from .models import Paper


def extract_abstract(text: str, limit: int = 380) -> str:
    cleaned = html.unescape(text or "")
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = " ".join(cleaned.split())

    if re.search(r"\babstract\s*:", cleaned, flags=re.IGNORECASE):
        parts = re.split(r"\babstract\s*:\s*", cleaned, flags=re.IGNORECASE)
        if len(parts) > 1 and len(parts[0]) < 160:
            cleaned = parts[-1].strip()

    cleaned = re.sub(
        r"^\s*arxiv\s*:\s*\S+\s*(announce\s*type\s*:\s*[^:]+)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*summary\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*abstract\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())

    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def estimate_impact(title: str, venue: str) -> str:
    title_lower = title.lower()
    venue_lower = venue.lower()
    if any(key in venue_lower for key in ["nature", "science", "cell", "prl", "physical review letters"]):
        return "High visibility venue; likely broad impact."
    if "quantum" in title_lower or "qubit" in title_lower:
        return "Relevant to quantum computing; check novelty and benchmarks."
    return "Potentially relevant; review methods and results."


def summarize_papers(papers: List[Paper]) -> List[Paper]:
    summarized = []
    for paper in papers:
        paper.abstract = extract_abstract(paper.abstract)
        if not paper.impact:
            paper.impact = estimate_impact(paper.title, paper.source)
        summarized.append(paper)
    return summarized

