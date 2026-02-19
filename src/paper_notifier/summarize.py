from __future__ import annotations

import html
import json
import re
from typing import List

import requests

from .config import OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_TIMEOUT_SECONDS
from .models import Paper


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


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
    cleaned = re.sub(
        r"^\s*[^.]{0,120}?\bpublished\s+online\b[^.]*[.;:]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*doi\s*[:\s]\s*10\.\S+\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*[^.]{0,140}?\bdoi\s*[:\s]\s*10\.\S+\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^\s*(?:doi\s*[:\s]*)?(?:10\.)?\d{3,9}/\S+\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*[,;:\-]+\s*", "", cleaned)
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


def normalize_impact_text(text: str) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"\s*\(?\s*word\s*count\s*:\s*\d+\s*\)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*based\s+on\s+(the\s+)?paper\s*metadata\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bpapermetadata\b", "paper metadata", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()

    sci_match = re.search(
        r"scientific\s+impact\s*:\s*(.+?)(?=(?:social(?:\s+or\s+industry)?\s+impact|societal\s+impact|industry\s+impact)\s*:|$)",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    social_match = re.search(
        r"(?:social(?:\s+or\s+industry)?\s+impact|societal\s+impact|industry\s+impact)\s*:\s*(.+)$",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )

    lines: list[str] = []
    scientific_text = ""
    social_text = ""
    if sci_match:
        scientific_text = _collapse_whitespace(sci_match.group(1))
    if social_match:
        social_text = _collapse_whitespace(social_match.group(1))

    if scientific_text or social_text:
        if not scientific_text:
            scientific_text = "Provides a potentially useful technical contribution that merits further validation."
        if not social_text:
            social_text = "May have downstream practical relevance if the findings are validated and adopted."
        lines.append(f"Scientific impact: {scientific_text}")
        lines.append(f"Social or industry impact: {social_text}")

    if not lines:
        for raw_line in cleaned.split("\n"):
            line = re.sub(r"^\s*[-•]\s*", "", raw_line).strip()
            if not line:
                continue
            lines.append(_collapse_whitespace(line))
            if len(lines) >= 2:
                break

    if lines and not lines[0].lower().startswith("scientific impact:"):
        lines[0] = f"Scientific impact: {lines[0]}"

    if len(lines) >= 2 and not lines[1].lower().startswith("social or industry impact:"):
        lines[1] = f"Social or industry impact: {lines[1]}"

    if len(lines) == 1:
        if lines[0].lower().startswith("scientific impact:"):
            lines.append(
                "Social or industry impact: May have downstream practical relevance if the findings are validated and adopted."
            )
        else:
            lines.insert(
                0,
                "Scientific impact: Provides a potentially useful technical contribution that merits further validation.",
            )

    if not lines:
        return ""
    return "\n".join(lines[:2])


def explain_impact_with_openrouter(paper: Paper) -> str:
    if not OPENROUTER_API_KEY:
        return ""

    author_text = ", ".join(paper.authors[:8]) if paper.authors else "Unknown authors"
    prompt = (
        "You are helping a research digest. Based on the paper metadata, "
        "write exactly two lines: "
        "(1) Scientific impact: ... and (2) Social or industry impact: ... "
        "Keep total length under 90 words, avoid hype, avoid markdown headers, "
        "and do not include word-count text. "
        "If the URL is accessible and contains open content, use it to improve accuracy.\n\n"
        f"Title: {paper.title}\n"
        f"Authors: {author_text}\n"
        f"Abstract: {paper.abstract}\n"
        f"URL: {paper.url}"
    )

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "reasoning": {"enabled": True},
    }
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=max(5, OPENROUTER_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        body = response.json()
        message = body.get("choices", [{}])[0].get("message", {})
        content = (message.get("content") or "").strip()
        return normalize_impact_text(content)
    except Exception as exc:
        print(f"[paper-notifier] OpenRouter impact generation failed: {exc}")
        return ""


def summarize_papers(papers: List[Paper]) -> List[Paper]:
    summarized = []
    for paper in papers:
        paper.abstract = extract_abstract(paper.abstract)
        if not paper.impact:
            paper.impact = explain_impact_with_openrouter(paper) or estimate_impact(paper.title, paper.source)
        summarized.append(paper)
    return summarized

