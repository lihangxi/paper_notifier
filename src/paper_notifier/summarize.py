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


def _heuristic_impact_sentence(title: str, venue: str) -> str:
    title_lower = title.lower()
    venue_lower = venue.lower()
    if any(key in venue_lower for key in ["nature", "science", "cell", "prl", "physical review letters"]):
        return "Impact: If validated, this work could influence a broad range of follow-up research due to its high-visibility venue."
    if "quantum" in title_lower or "qubit" in title_lower:
        return "Impact: If results hold, this paper could guide near-term progress in quantum computing methods and benchmarks."
    return "Impact: If validated and reproducible, this work could provide a practical foundation for future research and applications."


def _fetch_url_context(url: str, timeout: int = 10) -> str:
    if not url:
        return ""
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "paper-notifier/1.0"},
            allow_redirects=True,
        )
        response.raise_for_status()
    except Exception:
        return ""

    content_type = (response.headers.get("Content-Type") or "").lower()
    body = response.text if "text" in content_type or "html" in content_type else ""
    if not body:
        return ""

    body = re.sub(r"<script[^>]*>.*?</script>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = _collapse_whitespace(body)
    if len(body) > 2200:
        return body[:2200]
    return body


def _normalize_summary_text(text: str) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"^\s*summary\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
    return "\n".join(lines).strip()


def _ensure_impact_sentence(summary: str, title: str, venue: str) -> str:
    compact = _collapse_whitespace(summary)
    if not compact:
        return _heuristic_impact_sentence(title, venue)

    impact_match = re.search(r"(?:^|\s)(Impact\s*:\s*[^\n]+)$", compact, flags=re.IGNORECASE)
    if impact_match:
        impact_sentence = impact_match.group(1).strip()
        body = compact[: impact_match.start(1)].strip()
        if body:
            return f"{body} {impact_sentence}"
        return impact_sentence

    if compact.endswith("."):
        return f"{compact} {_heuristic_impact_sentence(title, venue)}"
    return f"{compact}. {_heuristic_impact_sentence(title, venue)}"


def summarize_with_openrouter(paper: Paper, url_context: str) -> str:
    if not OPENROUTER_API_KEY:
        return ""

    author_text = ", ".join(paper.authors[:8]) if paper.authors else "Unknown authors"
    context_block = f"\nURL content excerpt: {url_context}\n" if url_context else "\nURL content excerpt: (not accessible)\n"
    prompt = (
        "You are helping a research digest. Write one concise summary paragraph (40-70 words) "
        "using the abstract and any accessible URL content excerpt below. "
        "The summary must end with exactly one sentence that starts with 'Impact:' and states likely impact. "
        "Do not use markdown bullets or headings. Avoid hype and uncertainty inflation.\n\n"
        f"Title: {paper.title}\n"
        f"Authors: {author_text}\n"
        f"Venue: {paper.source}\n"
        f"Abstract: {paper.abstract}\n"
        f"URL: {paper.url}"
        f"{context_block}"
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
        return _normalize_summary_text(content)
    except Exception as exc:
        print(f"[paper-notifier] OpenRouter summary generation failed: {exc}")
        return ""


def _fallback_summary(paper: Paper) -> str:
    abstract = _collapse_whitespace(paper.abstract)
    if not abstract:
        abstract = "No abstract is available in metadata."
    return _ensure_impact_sentence(abstract, paper.title, paper.source)


def summarize_papers(papers: List[Paper]) -> List[Paper]:
    summarized = []
    for paper in papers:
        paper.abstract = extract_abstract(paper.abstract)
        url_context = _fetch_url_context(paper.url)
        generated_summary = summarize_with_openrouter(paper, url_context)
        paper.summary = _ensure_impact_sentence(generated_summary, paper.title, paper.source)
        if not paper.summary:
            paper.summary = _fallback_summary(paper)
        summarized.append(paper)
    return summarized

