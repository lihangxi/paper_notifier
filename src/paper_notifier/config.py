from __future__ import annotations

import os

from dotenv import load_dotenv

from .utils import parse_bool, parse_int

load_dotenv()

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
QUERY = os.getenv("QUERY", "quantum computing").strip()
MAX_PAPERS = parse_int(os.getenv("MAX_PAPERS"), 8)
DAYS_BACK = parse_int(os.getenv("DAYS_BACK"), 1)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai").strip()
RUN_TIME = os.getenv("RUN_TIME", "09:00").strip()
CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "").strip()
CROSSREF_ROWS = parse_int(os.getenv("CROSSREF_ROWS"), 5)
KEY_AUTHORS = [
	author.strip()
	for author in os.getenv("KEY_AUTHORS", "").split(",")
	if author.strip()
]
KEYWORDS_FILE = os.getenv("KEYWORDS_FILE", "keywords.txt").strip()
LOG_FILE = os.getenv("LOG_FILE", "logs/matched_papers.log").strip()
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
SEMANTIC_SCHOLAR_LIMIT = parse_int(os.getenv("SEMANTIC_SCHOLAR_LIMIT"), 20)

RSS_FEEDS = [
	feed.strip()
	for feed in os.getenv("RSS_FEEDS", "").split(",")
	if feed.strip()
]
FEISHU_WEBHOOK_TYPE = os.getenv("FEISHU_WEBHOOK_TYPE", "bot").strip().lower()
FLOW_FIELD_TITLE = os.getenv("FLOW_FIELD_TITLE", "paper_title").strip() or "paper_title"
FLOW_FIELD_AUTHORS = os.getenv("FLOW_FIELD_AUTHORS", "authors").strip() or "authors"
FLOW_FIELD_DESCRIPTION = os.getenv("FLOW_FIELD_DESCRIPTION", "description").strip() or "description"
FLOW_SINGLE_SUMMARY = parse_bool(os.getenv("FLOW_SINGLE_SUMMARY"), True)
