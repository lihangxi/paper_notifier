from __future__ import annotations

import json
import re
from typing import Iterable

import requests

from .config import OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_TIMEOUT_SECONDS
from .models import Paper

_KEYWORD_CACHE: dict[str, str] = {}


def _parse_llm_concepts(raw: str, max_items: int) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []

    parsed: list[str] = []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            parsed = [str(item).strip() for item in data]
    except Exception:
        lines = [segment.strip() for segment in re.split(r"\n|,", text) if segment.strip()]
        parsed = [re.sub(r"^[\-\*\d\.)\s]+", "", line).strip() for line in lines]

    concepts: list[str] = []
    seen: set[str] = set()
    for item in parsed:
        cleaned = item.strip().strip("\"'").strip()
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
    if not OPENROUTER_API_KEY:
        return []

    prompt = (
        "Extract 3-5 concept-level research keywords from the paper title and abstract. "
        "Return only a JSON array of short concept phrases (2-5 words each). "
        "Use domain concepts, not isolated words, not author names, not venue names.\n\n"
        f"Title: {paper.title}\n"
        f"Abstract: {paper.abstract}"
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
            response = requests.post(webhook_url, json=payload, timeout=20)
            response.raise_for_status()
            print(
                "[paper-notifier] Feishu flow post mode=single-summary "
                f"field=({flow_field_description})"
            )
            print(f"[paper-notifier] Feishu flow response: status={response.status_code} body={response.text[:200]}")
            return

        for paper in paper_list:
            keyword_summary = summarize_keywords_from_paper(paper)
            payload = {
                flow_field_title: paper.title,
                flow_field_authors: ", ".join(paper.authors),
                flow_field_description: f"Keywords: {keyword_summary}\n\n{paper.summary or paper.abstract}",
            }
            response = requests.post(webhook_url, json=payload, timeout=20)
            response.raise_for_status()
            print(f"[paper-notifier] Feishu flow response: status={response.status_code} body={response.text[:200]}")
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
    response = requests.post(webhook_url, json=payload, timeout=20)
    response.raise_for_status()
    print(f"[paper-notifier] Feishu bot response: status={response.status_code} body={response.text[:200]}")


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
        response = requests.post(webhook_url, json=payload, timeout=20)
        response.raise_for_status()
        print(f"[paper-notifier] Feishu no-match flow response: status={response.status_code} body={response.text[:200]}")
        return

    payload = {
        "msg_type": "text",
        "content": {
            "text": message,
        },
    }
    response = requests.post(webhook_url, json=payload, timeout=20)
    response.raise_for_status()
    print(f"[paper-notifier] Feishu no-match bot response: status={response.status_code} body={response.text[:200]}")


def _summarize_authors(papers: Iterable[Paper]) -> str:
    seen = set()
    names = []
    for paper in papers:
        for author in paper.authors:
            if author in seen:
                continue
            seen.add(author)
            names.append(author)
            if len(names) >= 10:
                return ", ".join(names) + ", et al."
    return ", ".join(names) if names else "Multiple authors"
