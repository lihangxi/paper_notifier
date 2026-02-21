from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    CROSSREF_MAILTO,
    CROSSREF_ROWS,
    DAYS_BACK,
    FEISHU_WEBHOOK_URL,
    FEISHU_WEBHOOK_TYPE,
    FLOW_FIELD_AUTHORS,
    FLOW_FIELD_DESCRIPTION,
    FLOW_FIELD_TITLE,
    FLOW_SINGLE_SUMMARY,
    KEY_AUTHORS,
    KEYWORDS_FILE,
    LOG_FILE,
    MAX_PAPERS,
    QUERY,
    RSS_FEEDS,
    SEMANTIC_SCHOLAR_API_KEY,
    SEMANTIC_SCHOLAR_LIMIT,
)
from .feishu import post_to_feishu
from .keywords import filter_papers_by_keywords, load_keyword_rules
from .models import Paper
from .scheduler import schedule_daily
from .sources.arxiv import fetch_arxiv
from .sources.crossref import fetch_crossref
from .sources.rss import fetch_rss
from .sources.semantic_scholar import fetch_semantic_scholar
from .summarize import summarize_papers
from .utils import utc_now


def load_logged_paper_urls(log_file: str) -> set[str]:
    if not log_file:
        return set()

    path = Path(log_file)
    if not path.exists():
        return set()

    urls: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line.startswith("- "):
                continue
            parts = line.split(" | ")
            if len(parts) < 5:
                continue
            url = parts[-1].strip()
            if url:
                urls.add(url)
    return urls


def filter_previously_sent_papers(papers: list[Paper], logged_urls: set[str]) -> list[Paper]:
    if not logged_urls:
        return papers
    return [paper for paper in papers if paper.url not in logged_urls]


def run_once(include_sent_papers: bool = False) -> None:
    print(f"[paper-notifier] run started at {utc_now().isoformat()}")
    if not FEISHU_WEBHOOK_URL:
        raise SystemExit("FEISHU_WEBHOOK_URL is required")

    papers = []
    papers.extend(fetch_arxiv(QUERY, MAX_PAPERS, DAYS_BACK))
    papers.extend(fetch_crossref(QUERY, CROSSREF_ROWS, DAYS_BACK, CROSSREF_MAILTO))
    papers.extend(fetch_semantic_scholar(QUERY, SEMANTIC_SCHOLAR_LIMIT, DAYS_BACK, SEMANTIC_SCHOLAR_API_KEY))
    papers.extend(fetch_rss(RSS_FEEDS, DAYS_BACK))
    print(f"[paper-notifier] fetched papers before author filter: {len(papers)}")

    keyword_rules = load_keyword_rules(KEYWORDS_FILE)
    if keyword_rules.has_rules():
        before_keywords = len(papers)
        papers = filter_papers_by_keywords(papers, keyword_rules)
        print(
            f"[paper-notifier] papers after keywords filter ({KEYWORDS_FILE}, {keyword_rules.keyword_count} keywords): "
            f"{len(papers)} / {before_keywords}"
        )

    if include_sent_papers:
        print("[paper-notifier] sent-paper filter bypassed via --include-sent-papers")
    else:
        logged_urls = load_logged_paper_urls(LOG_FILE)
        if logged_urls:
            before_log_filter = len(papers)
            papers = filter_previously_sent_papers(papers, logged_urls)
            print(
                f"[paper-notifier] papers after sent-paper log filter ({LOG_FILE}): "
                f"{len(papers)} / {before_log_filter}"
            )

    if KEY_AUTHORS:
        papers = [paper for paper in papers if matches_key_authors(paper.authors)]
        print(f"[paper-notifier] papers after KEY_AUTHORS filter: {len(papers)}")

    if not papers:
        print("[paper-notifier] no papers matched; skipping Feishu webhook")
        return

    papers = summarize_papers(papers)
    write_log(papers)
    print(f"[paper-notifier] posting {len(papers)} papers to Feishu (type={FEISHU_WEBHOOK_TYPE})")
    post_to_feishu(
        FEISHU_WEBHOOK_URL,
        papers,
        FEISHU_WEBHOOK_TYPE,
        FLOW_FIELD_TITLE,
        FLOW_FIELD_AUTHORS,
        FLOW_FIELD_DESCRIPTION,
        FLOW_SINGLE_SUMMARY,
    )
    print("[paper-notifier] Feishu post completed")


def run_test_flow() -> None:
    print(f"[paper-notifier] flow test started at {utc_now().isoformat()}")
    if not FEISHU_WEBHOOK_URL:
        raise SystemExit("FEISHU_WEBHOOK_URL is required")
    if FEISHU_WEBHOOK_TYPE != "flow":
        raise SystemExit("--test-flow requires FEISHU_WEBHOOK_TYPE=flow")

    test_paper = Paper(
        title="paper test",
        authors=["paper-notifier"],
        abstract="abstract test",
        summary="",
        url="https://example.com/paper-test",
        source="paper-notifier",
        published=datetime.now(timezone.utc),
    )
    post_to_feishu(
        FEISHU_WEBHOOK_URL,
        [test_paper],
        FEISHU_WEBHOOK_TYPE,
        FLOW_FIELD_TITLE,
        FLOW_FIELD_AUTHORS,
        FLOW_FIELD_DESCRIPTION,
        FLOW_SINGLE_SUMMARY,
    )
    print("[paper-notifier] flow test post completed")


def matches_key_authors(authors: list[str]) -> bool:
    normalized_authors = [author.lower() for author in authors]
    for key_author in KEY_AUTHORS:
        key_lower = key_author.lower()
        for author in normalized_authors:
            if key_lower in author:
                return True
    return False


def write_log(papers) -> None:
    if not LOG_FILE:
        return

    path = Path(LOG_FILE)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = utc_now().isoformat()
    lines = [f"{timestamp} matched_papers={len(papers)}"]
    for paper in papers:
        authors = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors += ", et al."
        lines.append(
            f"- {paper.title} | {authors} | {paper.source} | {paper.published.date()} | {paper.url}"
        )

    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Feishu paper notifier")
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--schedule", action="store_true", help="run daily on schedule")
    parser.add_argument("--test-flow", action="store_true", help="send one minimal flow payload and exit")
    parser.add_argument(
        "--include-sent-papers",
        action="store_true",
        help="bypass matched_papers.log dedup filter and include papers sent before",
    )
    args = parser.parse_args()

    if args.test_flow:
        run_test_flow()
    elif args.schedule:
        schedule_daily(lambda: run_once(include_sent_papers=args.include_sent_papers))
    else:
        run_once(include_sent_papers=args.include_sent_papers)


if __name__ == "__main__":
    main()
