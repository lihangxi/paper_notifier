from __future__ import annotations

import json
import re
from typing import Iterable

import requests

from .config import (
    KB_SHOW_IN_SLACK,
    KEYWORD_LLM_ENABLED,
    SLACK_ICON_EMOJI,
    SLACK_USERNAME,
)
from .llm_client import (
    get_active_model,
    get_active_provider_name,
    has_active_api_key,
    is_exhaustion_message,
    post_chat_completions,
)
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

# Slack truncates messages longer than 40_000 characters, so chunk before posting.
_SLACK_MESSAGE_LIMIT = 40_000


_SLACK_API_URL = "https://slack.com/api/chat.postMessage"


def _post_chat_message(token: str, channel: str, text: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "channel": channel,
        "text": text,
        "mrkdwn": True,
        # Suppress Slack's automatic URL preview cards (e.g. arXiv.org).
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if SLACK_USERNAME:
        payload["username"] = SLACK_USERNAME
    if SLACK_ICON_EMOJI:
        payload["icon_emoji"] = SLACK_ICON_EMOJI

    response = requests.post(
        _SLACK_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=20,
    )
    response.raise_for_status()

    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Slack chat.postMessage returned non-JSON response: {response.text[:200]}"
        ) from exc

    if not body.get("ok"):
        error = body.get("error", "unknown_error")
        raise RuntimeError(f"Slack chat.postMessage failed: {error}")

    return body


def _log_slack_response(prefix: str, response: dict[str, object]) -> None:
    print(
        f"[paper-notifier] {prefix}: ok={response.get('ok')} "
        f"channel={response.get('channel')} ts={response.get('ts')}"
    )


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
        if is_exhaustion_message(error_text):
            if not _KEYWORD_LLM_DISABLED_REASON:
                _KEYWORD_LLM_DISABLED_REASON = error_text
                print(
                    "[paper-notifier] "
                    f"{get_active_provider_name()} concept keyword generation disabled for this run "
                    f"after quota/permission error: {error_text[:200]}"
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
    lines = [f"*Today's paper count: ({len(paper_list)})*\n"]
    for idx, paper in enumerate(paper_list, start=1):
        authors = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors += ", et al."
        if idx > 1:
            lines.append("")
        lines.append(f"*{idx}) {paper.title}*")
        lines.append(f"*Authors:* {authors}")
        lines.append(f"*Source:* {paper.source} | *Date:* {paper.published.date()}")
        if KB_SHOW_IN_SLACK and paper.kb_score is not None:
            match_title = (paper.kb_top_match or "N/A").replace("\n", " ").strip()
            lines.append(
                f"*Library match:* {match_title} *(score {paper.kb_score:.2f})*"
            )
        lines.append(f"*Keywords:* {summarize_keywords_from_paper(paper)}")
        lines.append(f"*Summary:* {paper.summary or paper.abstract}")
        lines.append(f"*URL:* {paper.url}")
    return "\n".join(lines)


def format_no_match_message() -> str:
    return "Today's paper count: *(0)*\n\nNo matched papers found for the current filters."


def _split_messages(text: str) -> list[str]:
    """Split formatted text into chunks that each fit within Slack's message limit."""
    if len(text) <= _SLACK_MESSAGE_LIMIT:
        return [text]

    blocks = text.split("\n\n")
    messages: list[str] = []
    current = blocks[0] if blocks else ""
    for block in blocks[1:]:
        candidate = current + "\n\n" + block
        if len(candidate) > _SLACK_MESSAGE_LIMIT:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def post_to_slack(token: str, channel: str, papers: Iterable[Paper]) -> None:
    paper_list = list(papers)
    text = format_papers(paper_list)
    messages = _split_messages(text)
    for message in messages:
        response = _post_chat_message(token, channel, message)
        _log_slack_response("Slack response", response)
    print(
        "[paper-notifier] Slack post completed "
        f"channel={channel} count={len(paper_list)} messages={len(messages)}"
    )


def post_no_match_to_slack(token: str, channel: str) -> None:
    response = _post_chat_message(token, channel, format_no_match_message())
    _log_slack_response("Slack no-match response", response)


def post_test_to_slack(token: str, channel: str) -> None:
    text = (
        "*paper-notifier test message*\n\n"
        "Hello from paper-notifier! Your Slack bot token is configured correctly."
    )
    response = _post_chat_message(token, channel, text)
    _log_slack_response("Slack test response", response)
