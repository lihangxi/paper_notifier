from __future__ import annotations

import re
from typing import Iterable

import requests

from .models import Paper


def format_papers(papers: Iterable[Paper]) -> str:
    paper_list = list(papers)
    lines = [f"**Daily paper digest ({len(paper_list)})**"]
    for idx, paper in enumerate(paper_list, start=1):
        authors = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors += ", et al."
        if idx > 1:
            lines.append("")
        lines.append(f"**{idx}) {paper.title}**")
        lines.append(f"**Authors:** {authors}")
        lines.append(f"**Source:** {paper.source} | **Date:** {paper.published.date()}")
        lines.append(f"**Abstract:** {paper.abstract}")
        if paper.impact:
            lines.append("**Impact:**")
            for impact_line in _normalized_impact_lines(paper.impact):
                lines.append(f"- {impact_line}")
        lines.append(f"**URL:** {paper.url}")
    return "\n".join(lines)


def _normalized_impact_lines(impact: str) -> list[str]:
    cleaned = impact.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\*+", "", cleaned)
    cleaned = re.sub(r"\bSocial/\s*", "Social or industry ", cleaned, flags=re.IGNORECASE)

    scientific_match = re.search(
        r"scientific\s+impact\s*:\s*(.+?)(?=(?:social(?:\s+or\s+industry)?\s+impact|societal\s+impact|industry\s+impact)\s*:|$)",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    social_match = re.search(
        r"(?:social(?:\s+or\s+industry)?\s+impact|societal\s+impact|industry\s+impact)\s*:\s*(.+)$",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )

    def compact(text: str) -> str:
        return " ".join(text.split())

    if scientific_match or social_match:
        scientific_text = compact(scientific_match.group(1)) if scientific_match else ""
        social_text = compact(social_match.group(1)) if social_match else ""
        if not scientific_text:
            scientific_text = "Provides a potentially useful technical contribution that merits further validation."
        if not social_text:
            social_text = "May have downstream practical relevance if the findings are validated and adopted."
        result = []
        if scientific_text:
            result.append(f"Scientific impact: {scientific_text}")
        if social_text:
            result.append(f"Social or industry impact: {social_text}")
        return result

    raw_lines = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-•]\s*", "", line)
        if line:
            raw_lines.append(compact(line))

    if len(raw_lines) >= 2:
        return [
            f"Scientific impact: {raw_lines[0]}",
            f"Social or industry impact: {raw_lines[1]}",
        ]
    if len(raw_lines) == 1:
        return [
            f"Scientific impact: {raw_lines[0]}",
            "Social or industry impact: May have downstream practical relevance if the findings are validated and adopted.",
        ]
    return []


def _paper_description_with_impact(paper: Paper) -> str:
    description = paper.abstract
    if paper.impact:
        impact_text = "\n".join(f"- {line}" for line in _normalized_impact_lines(paper.impact))
        description = f"{description}\n\nImpact:\n{impact_text}"
    return description


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
            payload = {
                flow_field_title: paper.title,
                flow_field_authors: ", ".join(paper.authors),
                flow_field_description: _paper_description_with_impact(paper),
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
