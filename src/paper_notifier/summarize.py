from __future__ import annotations

import html
import re

import requests

from .llm_client import (
    get_active_model,
    get_active_provider_name,
    has_active_api_key,
    post_chat_completions,
)
from .config import IMPACT_GENERATION_ENABLED, SUMMARY_LLM_ENABLED
from .models import Paper


_LEADING_CLEANUP_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"^\s*arxiv\s*:\s*\S+\s*(announce\s*type\s*:\s*[^:]+)?\s*", re.IGNORECASE),
    (r"^\s*summary\s*:\s*", re.IGNORECASE),
    (r"^\s*abstract\s*:\s*", re.IGNORECASE),
    (r"^\s*[^.]{0,120}?\bpublished\s+online\b[^.]*[.;:]\s*", re.IGNORECASE),
    (r"^\s*doi\s*[:\s]\s*10\.\S+\s*", re.IGNORECASE),
    (r"^\s*[^.]{0,140}?\bdoi\s*[:\s]\s*10\.\S+\s*", re.IGNORECASE),
    (r"^\s*(?:doi\s*[:\s]*)?(?:10\.)?\d{3,9}/\S+\s*", re.IGNORECASE),
    (r"^\s*[,;:\-]+\s*", 0),
)


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _strip_non_latin_noise(text: str) -> str:
    cleaned = re.sub(r"[\u4e00-\u9fff\u3040-\u30ff\u0e00-\u0e7f\uac00-\ud7af]+", " ", text)
    cleaned = re.sub(r"[^A-Za-z0-9\s\.,;:()\[\]/%+\-]", " ", cleaned)
    return _collapse_whitespace(cleaned)


def _strip_leading_noise(text: str) -> str:
    cleaned = text
    for pattern, flags in _LEADING_CLEANUP_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=flags)
    return cleaned


def extract_abstract(text: str, limit: int = 380) -> str:
    cleaned = html.unescape(text or "")
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = _strip_non_latin_noise(cleaned)
    cleaned = _collapse_whitespace(cleaned)

    if re.search(r"\babstract\s*:", cleaned, flags=re.IGNORECASE):
        parts = re.split(r"\babstract\s*:\s*", cleaned, flags=re.IGNORECASE)
        if len(parts) > 1 and len(parts[0]) < 160:
            cleaned = parts[-1].strip()

    cleaned = _strip_leading_noise(cleaned)
    cleaned = _collapse_whitespace(cleaned)

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
    cleaned = re.sub(r"^\s*```[a-zA-Z]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"^\s*summary\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    lines = [
        re.sub(r"^\s*[-*\d\.)]+\s*", "", line).strip()
        for line in cleaned.split("\n")
        if line.strip()
    ]
    return "\n".join(lines).strip()


def _is_low_quality_summary(text: str) -> bool:
    normalized = _collapse_whitespace(text)
    if not normalized:
        return True
    if "```" in normalized:
        return True
    if re.match(r"^title\s*:", normalized, flags=re.IGNORECASE):
        return True
    if re.match(r"^impact\s*:", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"\be(?:\s+e){3,}\b", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(\w{1,20})(?:\s+\1){3,}\b", normalized, flags=re.IGNORECASE):
        return True

    letters = sum(1 for ch in normalized if ch.isalpha())
    if letters / max(1, len(normalized)) < 0.45:
        return True

    tokens = re.findall(r"\b[\w\-]+\b", normalized.lower())
    if len(tokens) >= 20:
        unique_ratio = len(set(tokens)) / len(tokens)
        if unique_ratio < 0.35:
            return True
    return False


def _strip_impact_sentence(text: str) -> str:
    if not text:
        return ""
    without_inline = re.sub(r"\s+Impact\s*:\s*[^\n]+", "", text, flags=re.IGNORECASE)
    without_line = re.sub(r"(?im)^\s*Impact\s*:\s*.*$", "", without_inline)
    return _normalize_summary_text(without_line)


def _truncate_to_sentences(text: str, max_sentences: int = 2, limit: int = 360) -> str:
    normalized = _collapse_whitespace(text)
    if not normalized:
        return ""
    sentence_parts = re.split(r"(?<=[.!?])\s+", normalized)
    selected = [part.strip() for part in sentence_parts if part.strip()][:max_sentences]
    compact = " ".join(selected) if selected else normalized
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


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


def summarize_with_llm(paper: Paper, url_context: str) -> str:
    if not SUMMARY_LLM_ENABLED:
        return ""
    if not has_active_api_key():
        return ""

    author_text = ", ".join(paper.authors[:8]) if paper.authors else "Unknown authors"
    context_block = f"\nURL content excerpt: {url_context}\n" if url_context else "\nURL content excerpt: (not accessible)\n"
    if IMPACT_GENERATION_ENABLED:
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
    else:
        prompt = (
            "You are helping a research digest. Write one concise summary paragraph (40-70 words) "
            "using the abstract and any accessible URL content excerpt below. "
            "Do not add an Impact sentence. Do not use markdown bullets or headings. "
            "Avoid hype and uncertainty inflation.\n\n"
            f"Title: {paper.title}\n"
            f"Authors: {author_text}\n"
            f"Venue: {paper.source}\n"
            f"Abstract: {paper.abstract}\n"
            f"URL: {paper.url}"
            f"{context_block}"
        )

    payload = {
        "model": get_active_model(),
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        body = post_chat_completions(payload, "summary generation")
        message = body.get("choices", [{}])[0].get("message", {})
        content = (message.get("content") or "").strip()
        return _normalize_summary_text(content)
    except Exception as exc:
        print(f"[paper-notifier] {get_active_provider_name()} summary generation failed: {exc}")
        return ""


def _fallback_summary(paper: Paper, include_impact: bool = True) -> str:
    abstract = _truncate_to_sentences(_strip_non_latin_noise(paper.abstract))
    if not abstract:
        abstract = "No abstract is available in metadata."
    if include_impact:
        return _ensure_impact_sentence(abstract, paper.title, paper.source)
    return abstract


def summarize_papers(papers: list[Paper]) -> list[Paper]:
    for paper in papers:
        paper.abstract = extract_abstract(paper.abstract)
        url_context = _fetch_url_context(paper.url)
        generated_summary = summarize_with_llm(paper, url_context)
        if IMPACT_GENERATION_ENABLED:
            candidate = _ensure_impact_sentence(generated_summary, paper.title, paper.source)
        else:
            candidate = _strip_impact_sentence(generated_summary)
        if not candidate or _is_low_quality_summary(candidate):
            if candidate:
                print(
                    "[paper-notifier] Low-quality LLM summary detected; "
                    f"using fallback summary for: {paper.title[:80]}"
                )
            paper.summary = _fallback_summary(paper, include_impact=IMPACT_GENERATION_ENABLED)
        else:
            paper.summary = candidate
    return papers

