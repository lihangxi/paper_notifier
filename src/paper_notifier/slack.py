from __future__ import annotations

import json
import re
from typing import Iterable

import requests

from .config import SLACK_ICON_EMOJI, SLACK_USERNAME
from .llm_client import (
    get_active_model,
    has_active_api_key,
    post_chat_completions,
)
from .models import Paper

_KEYWORD_CACHE: dict[str, str] = {}

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


def _parse_llm_concepts(raw: str, max_items: int) -> list[str]:
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
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        concepts.append(cleaned)
        if len(concepts) >= max_items:
            break
    return concepts


def _llm_concept_keywords(paper: Paper, max_items: int = 5) -> list[str]:
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
    }
    try:
        body = post_chat_completions(payload, "concept keyword generation")
        message = body.get("choices", [{}])[0].get("message", {})
        content = (message.get("content") or "").strip()
        return _parse_llm_concepts(content, max_items)
    except Exception as exc:
        print(f"[paper-notifier] concept keyword generation failed: {exc}")
        return []


def summarize_keywords_from_paper(paper: Paper, top_n: int = 5) -> str:
    cache_key = f"{paper.title}\n{paper.abstract}"
    cached = _KEYWORD_CACHE.get(cache_key)
    if cached is not None:
        return cached

    concepts = _llm_concept_keywords(paper, max_items=top_n)
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
