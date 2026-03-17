from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

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
    KEYWORDS_FILE,
    LLM_RELEVANCE_SCORE_THRESHOLD,
    LLM_RELEVANCE_TOPIC,
    LOG_FILE,
    MAX_PAPERS,
    QUERY,
    RESEARCH_FIELD_TERMS,
    RSS_FEEDS,
    SEMANTIC_SCHOLAR_API_KEY,
    SEMANTIC_SCHOLAR_LIMIT,
)
from .feishu import post_no_match_to_feishu, post_to_feishu
from .keywords import filter_papers_by_keywords, load_keyword_rules
from .llm_client import (
    get_active_model,
    get_active_provider_name,
    has_active_api_key,
    post_chat_completions,
)
from .models import Paper
from .scheduler import schedule_daily
from .sources.arxiv import fetch_arxiv
from .sources.crossref import fetch_crossref
from .sources.rss import fetch_rss
from .sources.semantic_scholar import fetch_semantic_scholar
from .summarize import summarize_papers
from .utils import utc_now


def _project_root() -> Path:
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return module_path.parents[2]


def _resolve_runtime_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate

    cwd_candidate = Path.cwd() / candidate
    if cwd_candidate.exists():
        return cwd_candidate

    return _project_root() / candidate


def load_logged_paper_urls(log_file: str) -> set[str]:
    if not log_file:
        return set()

    path = _resolve_runtime_path(log_file)
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


def _normalize_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    normalized = urlunparse((scheme, netloc, path, "", "", ""))
    return normalized


def _extract_doi(value: str) -> str:
    match = re.search(r"10\.\d{4,9}/[^\s?#]+", value, flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(0).rstrip(".,;:)\"").lower()


def _normalize_title(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip().lower())
    cleaned = re.sub(r"[^a-z0-9 ]+", "", cleaned)
    return cleaned


def _paper_dedup_keys(paper: Paper) -> list[str]:
    keys: list[str] = []

    normalized_url = _normalize_url(paper.url)
    if normalized_url:
        keys.append(f"url:{normalized_url}")
        doi = _extract_doi(normalized_url)
        if doi:
            keys.append(f"doi:{doi}")

    title_key = _normalize_title(paper.title)
    if title_key:
        keys.append(f"title:{title_key}")

    return keys


def deduplicate_papers(papers: list[Paper]) -> list[Paper]:
    seen: set[str] = set()
    deduped: list[Paper] = []

    for paper in papers:
        keys = _paper_dedup_keys(paper)
        if any(key in seen for key in keys):
            continue

        deduped.append(paper)
        for key in keys:
            seen.add(key)

    return deduped


def fetch_all_papers() -> list[Paper]:
    papers: list[Paper] = []
    fetchers = [
        ("arxiv", lambda: fetch_arxiv(QUERY, MAX_PAPERS, DAYS_BACK)),
        ("crossref", lambda: fetch_crossref(QUERY, CROSSREF_ROWS, DAYS_BACK, CROSSREF_MAILTO)),
        (
            "semantic_scholar",
            lambda: fetch_semantic_scholar(QUERY, SEMANTIC_SCHOLAR_LIMIT, DAYS_BACK, SEMANTIC_SCHOLAR_API_KEY),
        ),
        ("rss", lambda: fetch_rss(RSS_FEEDS, DAYS_BACK)),
    ]

    for source_name, fetcher in fetchers:
        try:
            papers.extend(fetcher())
        except Exception as exc:
            print(f"[paper-notifier] source fetch failed ({source_name}): {exc}")

    return papers


def apply_runtime_filters(papers: list[Paper], include_sent_papers: bool) -> list[Paper]:
    keyword_file_path = _resolve_runtime_path(KEYWORDS_FILE)
    keyword_rules = load_keyword_rules(str(keyword_file_path))
    if keyword_rules.has_rules():
        before_keywords = len(papers)
        papers = filter_papers_by_keywords(papers, keyword_rules)
        print(
            f"[paper-notifier] papers after keywords filter ({keyword_file_path}, {keyword_rules.keyword_count} keywords): "
            f"{len(papers)} / {before_keywords}"
        )
    else:
        print(f"[paper-notifier] keyword filter skipped (no rules found in {keyword_file_path})")

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

    before_relevance_filter = len(papers)
    try:
        papers, unresolved = filter_papers_by_llm_relevance(
            papers,
            LLM_RELEVANCE_TOPIC,
            LLM_RELEVANCE_SCORE_THRESHOLD,
            max_attempts=5,
        )
        resolved_count = before_relevance_filter - len(unresolved)
        print(
            "[paper-notifier] papers after LLM relevance filter "
            f"(topic={LLM_RELEVANCE_TOPIC}, threshold={LLM_RELEVANCE_SCORE_THRESHOLD:.2f}): "
            f"{len(papers)} / {before_relevance_filter} "
            f"(llm_resolved={resolved_count}, llm_unresolved={len(unresolved)})"
        )
        if unresolved:
            fallback_matched = filter_papers_by_research_field(unresolved, RESEARCH_FIELD_TERMS)
            papers.extend(fallback_matched)
            print(
                "[paper-notifier] fallback term relevance filter "
                f"({len(RESEARCH_FIELD_TERMS)} terms): {len(fallback_matched)} / {len(unresolved)}"
            )
    except RuntimeError as exc:
        print(f"[paper-notifier] LLM relevance filter failed: {exc}")
        papers = filter_papers_by_research_field(papers, RESEARCH_FIELD_TERMS)
        print(
            "[paper-notifier] fallback term relevance filter "
            f"({len(RESEARCH_FIELD_TERMS)} terms): {len(papers)} / {before_relevance_filter}"
        )

    return papers


def run_once(include_sent_papers: bool = False) -> None:
    print(f"[paper-notifier] run started at {utc_now().isoformat()}")
    if not FEISHU_WEBHOOK_URL:
        raise SystemExit("FEISHU_WEBHOOK_URL is required")

    papers = fetch_all_papers()

    fetched_count = len(papers)
    papers = deduplicate_papers(papers)
    if len(papers) != fetched_count:
        print(f"[paper-notifier] papers after same-run dedup: {len(papers)} / {fetched_count}")

    print(f"[paper-notifier] fetched papers before filters: {len(papers)}")

    papers = apply_runtime_filters(papers, include_sent_papers)

    if not papers:
        print("[paper-notifier] no papers matched; sending no-match notification to Feishu")
        post_no_match_to_feishu(
            FEISHU_WEBHOOK_URL,
            FEISHU_WEBHOOK_TYPE,
            FLOW_FIELD_TITLE,
            FLOW_FIELD_AUTHORS,
            FLOW_FIELD_DESCRIPTION,
            FLOW_SINGLE_SUMMARY,
        )
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


def filter_papers_by_research_field(papers: list[Paper], field_terms: list[str]) -> list[Paper]:
    if not field_terms:
        return papers

    patterns = [_compile_match_pattern(term) for term in field_terms if term.strip()]
    if not patterns:
        return papers

    return [paper for paper in papers if matches_research_field(paper, patterns)]


def filter_papers_by_llm_relevance(
    papers: list[Paper],
    topic: str,
    threshold: float,
    max_attempts: int = 5,
) -> tuple[list[Paper], list[Paper]]:
    if not papers:
        return papers, []
    if not has_active_api_key():
        raise RuntimeError(f"missing API key for provider {get_active_provider_name()}")

    evaluated, unresolved = _llm_relevance_scores(papers, topic, max_attempts=max_attempts)

    effective_threshold = max(0.0, min(1.0, threshold))
    filtered: list[Paper] = []
    for paper, score in evaluated:
        if score >= effective_threshold:
            filtered.append(paper)
    return filtered, unresolved


_LLM_RELEVANCE_BATCH_SIZE = 50


def _llm_relevance_scores(
    papers: list[Paper],
    topic: str,
    max_attempts: int = 5,
) -> tuple[list[tuple[Paper, float]], list[Paper]]:
    if max_attempts < 1:
        max_attempts = 1

    evaluated: list[tuple[Paper, float]] = []
    unresolved: list[Paper] = []
    for batch_start in range(0, len(papers), _LLM_RELEVANCE_BATCH_SIZE):
        batch = papers[batch_start : batch_start + _LLM_RELEVANCE_BATCH_SIZE]
        batch_evaluated, batch_unresolved = _llm_relevance_scores_for_batch(
            batch,
            topic,
            batch_start // _LLM_RELEVANCE_BATCH_SIZE + 1,
            max_attempts,
        )
        evaluated.extend(batch_evaluated)
        unresolved.extend(batch_unresolved)
    return evaluated, unresolved


def _llm_relevance_scores_for_batch(
    batch: list[Paper],
    topic: str,
    batch_number: int,
    max_attempts: int,
) -> tuple[list[tuple[Paper, float]], list[Paper]]:
    remaining: list[tuple[str, Paper]] = [
        (f"P{index}", paper) for index, paper in enumerate(batch, start=1)
    ]
    evaluated: list[tuple[Paper, float]] = []

    for attempt in range(1, max_attempts + 1):
        if not remaining:
            break

        payload = {
            "model": get_active_model(),
            "messages": [{"role": "user", "content": _build_relevance_prompt(remaining, topic)}],
        }

        try:
            body = post_chat_completions(payload, "relevance filter")
            message = body.get("choices", [{}])[0].get("message", {})
            content = (message.get("content") or "").strip()
            if not content:
                raise RuntimeError(f"{get_active_provider_name()} returned empty relevance response")

            scores_by_id = _parse_relevance_scores(content, [paper_id for paper_id, _ in remaining])
        except RuntimeError as exc:
            print(
                "[paper-notifier] LLM relevance batch retry "
                f"(batch={batch_number}, attempt={attempt}/{max_attempts}) failed: {exc}"
            )
            continue

        if not scores_by_id:
            print(
                "[paper-notifier] LLM relevance batch retry "
                f"(batch={batch_number}, attempt={attempt}/{max_attempts}) returned no matched IDs"
            )
            continue

        matched_ids = set(scores_by_id.keys())
        if len(matched_ids) != len(remaining):
            print(
                "[paper-notifier] LLM relevance partial ID score set "
                f"(batch={batch_number}, attempt={attempt}/{max_attempts}, "
                f"expected={len(remaining)}, got={len(matched_ids)})"
            )
        for paper_id, paper in remaining:
            score = scores_by_id.get(paper_id)
            if score is not None:
                evaluated.append((paper, score))

        remaining = [(paper_id, paper) for paper_id, paper in remaining if paper_id not in matched_ids]

    return evaluated, [paper for _, paper in remaining]


def _build_relevance_prompt(papers: list[tuple[str, Paper]], topic: str) -> str:
    paper_lines: list[str] = []
    for paper_id, paper in papers:
        abstract = (paper.abstract or "").replace("\n", " ").strip()
        if len(abstract) > 700:
            abstract = abstract[:700] + "..."
        paper_lines.append(
            f"{paper_id}. title: {paper.title}\n"
            f"   source: {paper.source}\n"
            f"   abstract: {abstract}"
        )

    joined = "\n".join(paper_lines)
    return (
        "Score each paper for semantic relevance to the target research topic. "
        "Return only JSON with this shape: {\"scores\": [{\"id\": \"P1\", \"score\": 0.0}, ...]}. "
        "Each id must match a provided paper id exactly and each score must be a float from 0.0 to 1.0. "
        "Do not include papers not listed. No markdown, no explanations.\n\n"
        f"Target topic: {topic}\n\n"
        f"Papers:\n{joined}"
    )


def _parse_relevance_scores(content: str, expected_ids: list[str]) -> dict[str, float]:
    raw = content.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

    parsed = _load_json_with_fallback(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM relevance response is not a JSON object")

    scores = parsed.get("scores")
    if not isinstance(scores, list):
        raise RuntimeError("LLM relevance response missing 'scores' list")

    expected_id_set = set(expected_ids)
    scores_by_id: dict[str, float] = {}

    if scores and isinstance(scores[0], (int, float, str)):
        numeric_scores: list[float] = []
        try:
            numeric_scores = [float(score) for score in scores]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("LLM relevance scores must be numeric") from exc

        for paper_id, score in zip(expected_ids, numeric_scores):
            scores_by_id[paper_id] = max(0.0, min(1.0, score))
        return scores_by_id

    for entry in scores:
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("id")
        raw_score = entry.get("score")
        if not isinstance(raw_id, str):
            continue
        if raw_id not in expected_id_set:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        scores_by_id[raw_id] = max(0.0, min(1.0, score))

    return scores_by_id


def _load_json_with_fallback(raw: str) -> dict | list:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise RuntimeError("LLM relevance response is not parseable JSON")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RuntimeError("LLM relevance response is not parseable JSON") from exc


def matches_research_field(paper: Paper, patterns: list[re.Pattern]) -> bool:
    haystack = " ".join([
        paper.title or "",
        paper.abstract or "",
        paper.source or "",
    ]).strip()
    if not haystack:
        return False
    return any(pattern.search(haystack) for pattern in patterns)


def _compile_match_pattern(value: str) -> re.Pattern:
    try:
        return re.compile(value, flags=re.IGNORECASE)
    except re.error:
        escaped = re.escape(value).replace(r"\*", ".*")
        return re.compile(escaped, flags=re.IGNORECASE)


def write_log(papers) -> None:
    if not LOG_FILE:
        return

    path = _resolve_runtime_path(LOG_FILE)
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
