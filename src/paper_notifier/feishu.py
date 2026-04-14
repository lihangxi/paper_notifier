from __future__ import annotations

import json
import re
from typing import Iterable

import requests

from .llm_client import (
    get_active_model,
    get_active_provider_name,
    has_active_api_key,
    post_chat_completions,
)
from .config import KEYWORD_LLM_ENABLED
from .models import Paper

_KEYWORD_CACHE: dict[str, str] = {}
_KEYWORD_LLM_DISABLED_REASON: str | None = None

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "under",
    "using",
    "via",
    "with",
    "down",
    "up",
    "over",
}

_GENERIC_DOMAIN_TOKENS = {
    "algorithm",
    "algorithms",
    "communication",
    "complexity",
    "computational",
    "computing",
    "entanglement",
    "magic",
    "markovian",
    "protocol",
    "protocols",
    "quantum",
    "qubit",
    "qubits",
    "resource",
    "resources",
    "state",
    "states",
}


def _tokenize_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9\-]*", text or "")


def _source_tokens_from_paper(paper: Paper) -> set[str]:
    text = f"{paper.title} {paper.abstract}"
    return {
        token.lower()
        for token in _tokenize_words(text)
        if len(token) >= 3 and token.lower() not in _STOPWORDS
    }


def _is_low_quality_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    if not normalized:
        return True
    if "```" in normalized:
        return True
    if re.search(r"\b(\w{1,20})(?:\s+\1){2,}\b", normalized, flags=re.IGNORECASE):
        return True
    if re.search(r"\be(?:\s+e){3,}\b", normalized, flags=re.IGNORECASE):
        return True

    letters = sum(1 for ch in normalized if ch.isalpha())
    if letters / max(1, len(normalized)) < 0.45:
        return True
    return False


def _is_valid_concept_phrase(text: str, source_tokens: set[str] | None = None) -> bool:
    phrase = re.sub(r"\s+", " ", (text or "")).strip(" .,:;\"'`()[]{}")
    if not phrase:
        return False
    if _is_low_quality_text(phrase):
        return False

    words = _tokenize_words(phrase)
    if len(words) < 2 or len(words) > 6:
        return False
    if any(len(word) == 1 for word in words):
        return False

    lowered = [word.lower() for word in words]
    if len(set(lowered)) == 1:
        return False
    if all(word in _STOPWORDS for word in lowered):
        return False
    if lowered[0] in _STOPWORDS or lowered[-1] in _STOPWORDS:
        return False

    if source_tokens:
        unsupported_count = sum(
            1
            for word in lowered
            if len(word) >= 4
            and word not in source_tokens
            and word not in _GENERIC_DOMAIN_TOKENS
        )
        if unsupported_count > 0:
            return False
    return True


def _fallback_concepts_from_title(title: str, max_items: int) -> list[str]:
    concepts: list[str] = []
    seen: set[str] = set()

    chunks = re.split(r":|;|,|\(|\)|\bwith\b|\bunder\b|\bfor\b|\bon\b", title or "", flags=re.IGNORECASE)
    for chunk in chunks:
        words = [word for word in _tokenize_words(chunk) if word.lower() not in _STOPWORDS]
        if len(words) < 2:
            continue
        for span in (4, 3, 2):
            for index in range(0, len(words) - span + 1):
                phrase = " ".join(words[index : index + span])
                key = phrase.casefold()
                if key in seen:
                    continue
                if not _is_valid_concept_phrase(phrase):
                    continue
                seen.add(key)
                concepts.append(phrase)
                if len(concepts) >= max_items:
                    return concepts
    return concepts


def _post_json(url: str, payload: dict[str, object], timeout: int = 20) -> requests.Response:
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response


def _log_feishu_response(prefix: str, response: requests.Response) -> None:
    print(f"[paper-notifier] {prefix}: status={response.status_code} body={response.text[:200]}")


def _parse_llm_concepts(
    raw: str,
    max_items: int,
    source_tokens: set[str] | None = None,
) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []

    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.IGNORECASE)

    parsed: list[str] = []
    json_candidates = [text]
    array_match = re.search(r"\[[\s\S]*\]", text)
    if array_match:
        json_candidates.append(array_match.group(0).strip())

    for candidate in json_candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue

        if isinstance(data, list):
            parsed = [str(item).strip() for item in data]
            break

    if not parsed:
        lines = [segment.strip() for segment in re.split(r"\n|;", text) if segment.strip()]
        if len(lines) == 1 and "," in lines[0] and "[" not in lines[0] and "]" not in lines[0]:
            lines = [segment.strip() for segment in lines[0].split(",") if segment.strip()]
        parsed = [re.sub(r"^[\-\*\d\.)\s]+", "", line).strip() for line in lines]

    concepts: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        cleaned = item.strip().strip("\"'").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace("```", "")
        cleaned = cleaned.strip("[]").strip().strip("\"'").strip()
        if cleaned.lower() == "json":
            continue
        cleaned = re.sub(r"\s+", " ", cleaned)
        if len(cleaned) < 4:
            continue
        if not _is_valid_concept_phrase(cleaned, source_tokens):
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        concepts.append(cleaned)
        if len(concepts) >= max_items:
            break
    return concepts


def _llm_concept_keywords(paper: Paper, max_items: int = 5) -> list[str]:
    global _KEYWORD_LLM_DISABLED_REASON

    if not KEYWORD_LLM_ENABLED:
        return []
    if _KEYWORD_LLM_DISABLED_REASON:
        return []
    if not has_active_api_key():
        return []

    prompt = (
        "Extract 3-5 concept-level research keywords from the paper title and abstract. "
        "Return only a JSON array of short concept phrases (2-5 words each). "
        "Use domain concepts, not isolated words, not author names, not venue names.\n\n"
        f"Title: {paper.title}\n"
        f"Abstract: {paper.abstract}"
    )
    payload = {
        "model": get_active_model(),
        "messages": [{"role": "user", "content": prompt}],
        "reasoning": {"enabled": True},
    }
    try:
        source_tokens = _source_tokens_from_paper(paper)
        body = post_chat_completions(payload, "concept keyword generation")
        message = body.get("choices", [{}])[0].get("message", {})
        content = (message.get("content") or "").strip()
        return _parse_llm_concepts(content, max_items, source_tokens)
    except Exception as exc:
        error_text = str(exc)
        if "status=401" in error_text or "status=403" in error_text:
            _KEYWORD_LLM_DISABLED_REASON = error_text
            print(
                "[paper-notifier] "
                f"{get_active_provider_name()} concept keyword generation disabled for this run "
                f"after auth/permission error: {error_text}"
            )
            return []
        print(
            "[paper-notifier] "
            f"{get_active_provider_name()} concept keyword generation failed: {error_text}"
        )
        return []


def summarize_keywords_from_paper(paper: Paper, top_n: int = 5) -> str:
    cache_key = f"{paper.title}\n{paper.abstract}"
    cached = _KEYWORD_CACHE.get(cache_key)
    if cached is not None:
        return cached

    source_tokens = _source_tokens_from_paper(paper)
    concepts = [
        concept
        for concept in _llm_concept_keywords(paper, max_items=top_n)
        if _is_valid_concept_phrase(concept, source_tokens)
    ]
    if not concepts:
        concepts = _fallback_concepts_from_title(paper.title, top_n)
    result = ", ".join(concepts) if concepts else "N/A"
    _KEYWORD_CACHE[cache_key] = result
    return result


def format_papers(papers: Iterable[Paper]) -> str:
    paper_list = list(papers)
    lines = [f"Today's paper count: **({len(paper_list)})**\n"]
    for idx, paper in enumerate(paper_list, start=1):
        authors = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors += ", et al."
        if idx > 1:
            lines.append("")
        lines.append(f"**{idx}) {paper.title}**")
        lines.append(f"**Authors:** {authors}")
        lines.append(f"**Source:** {paper.source} | **Date:** {paper.published.date()}")
        lines.append(f"**Keywords:** {summarize_keywords_from_paper(paper)}")
        lines.append(f"**Summary:** {paper.summary or paper.abstract}")
        lines.append(f"**URL:** {paper.url}")
    return "\n".join(lines)


def format_no_match_message() -> str:
    return "Today's paper count: **(0)**\n\nNo matched papers found for the current filters."


def post_to_feishu(
    webhook_url: str,
    papers: Iterable[Paper],
    webhook_type: str,
    flow_field_title: str,
    flow_field_authors: str,
    flow_field_description: str,
    flow_single_summary: bool,
) -> None:
    paper_list = list(papers)

    if webhook_type == "flow":
        if flow_single_summary:
            payload = {
                flow_field_description: format_papers(paper_list),
            }
            response = _post_json(webhook_url, payload)
            print(
                "[paper-notifier] Feishu flow post mode=single-summary "
                f"field=({flow_field_description})"
            )
            _log_feishu_response("Feishu flow response", response)
            return

        for paper in paper_list:
            keyword_summary = summarize_keywords_from_paper(paper)
            payload = {
                flow_field_title: paper.title,
                flow_field_authors: ", ".join(paper.authors),
                flow_field_description: f"Keywords: {keyword_summary}\n\n{paper.summary or paper.abstract}",
            }
            response = _post_json(webhook_url, payload)
            _log_feishu_response("Feishu flow response", response)
        print(
            "[paper-notifier] Feishu flow post mode=per-paper "
            f"fields=({flow_field_title}, {flow_field_authors}, {flow_field_description}) "
            f"count={len(paper_list)}"
        )
        return

    payload = {
        "msg_type": "text",
        "content": {
            "text": format_papers(paper_list)
        },
    }
    response = _post_json(webhook_url, payload)
    _log_feishu_response("Feishu bot response", response)


def post_no_match_to_feishu(
    webhook_url: str,
    webhook_type: str,
    flow_field_title: str,
    flow_field_authors: str,
    flow_field_description: str,
    flow_single_summary: bool,
) -> None:
    message = format_no_match_message()

    if webhook_type == "flow":
        if flow_single_summary:
            payload = {flow_field_description: message}
        else:
            payload = {
                flow_field_title: "No matched papers",
                flow_field_authors: "paper-notifier",
                flow_field_description: message,
            }
        response = _post_json(webhook_url, payload)
        _log_feishu_response("Feishu no-match flow response", response)
        return

    payload = {
        "msg_type": "text",
        "content": {
            "text": message,
        },
    }
    response = _post_json(webhook_url, payload)
    _log_feishu_response("Feishu no-match bot response", response)
