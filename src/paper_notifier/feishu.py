from __future__ import annotations

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
        lines.append("")
        if idx > 1:
            lines.append("---")
        lines.append(f"**{idx}) {paper.title}**")
        lines.append(f"**Authors:** {authors}")
        lines.append(f"**Source:** {paper.source} | **Date:** {paper.published.date()}")
        lines.append(f"**Abstract:** {paper.abstract}")
        lines.append(f"**URL:** {paper.url}")
    return "\n".join(lines)


def post_to_feishu(
    webhook_url: str,
    papers: Iterable[Paper],
    webhook_type: str,
    flow_field_title: str,
    flow_field_authors: str,
    flow_field_description: str,
    flow_single_summary: bool,
) -> None:
    if webhook_type == "flow":
        if flow_single_summary:
            payload = {
                flow_field_title: "Daily paper summary",
                flow_field_authors: _summarize_authors(papers),
                flow_field_description: format_papers(papers),
            }
            response = requests.post(webhook_url, json=payload, timeout=20)
            response.raise_for_status()
            print(f"[paper-notifier] Feishu flow response: status={response.status_code} body={response.text[:200]}")
            return

        for paper in papers:
            payload = {
                flow_field_title: paper.title,
                flow_field_authors: ", ".join(paper.authors),
                flow_field_description: paper.abstract,
            }
            response = requests.post(webhook_url, json=payload, timeout=20)
            response.raise_for_status()
            print(f"[paper-notifier] Feishu flow response: status={response.status_code} body={response.text[:200]}")
        return

    payload = {
        "msg_type": "text",
        "content": {
            "text": format_papers(papers)
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
