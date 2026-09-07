from __future__ import annotations

import os

from dotenv import load_dotenv

from .utils import parse_float, parse_int

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "").strip()
SLACK_USERNAME = os.getenv("SLACK_USERNAME", "").strip()
SLACK_ICON_EMOJI = os.getenv("SLACK_ICON_EMOJI", "").strip()
QUERY = os.getenv("QUERY", "quantum computing").strip()
MAX_PAPERS = parse_int(os.getenv("MAX_PAPERS"), 8)
DAYS_BACK = parse_int(os.getenv("DAYS_BACK"), 1)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai").strip()
RUN_TIME = os.getenv("RUN_TIME", "09:00").strip()
SCHEDULER_MISFIRE_GRACE_SECONDS = parse_int(
	os.getenv("SCHEDULER_MISFIRE_GRACE_SECONDS"), 60
)
CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "").strip()
CROSSREF_ROWS = parse_int(os.getenv("CROSSREF_ROWS"), 5)
RESEARCH_FIELD_TERMS = [
	term.strip()
	for term in os.getenv("RESEARCH_FIELD_TERMS", QUERY).split(",")
	if term.strip()
]
KEYWORDS_FILE = os.getenv("KEYWORDS_FILE", "keywords.txt").strip()
LOG_FILE = os.getenv("LOG_FILE", "logs/matched_papers.log").strip()
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
SEMANTIC_SCHOLAR_LIMIT = parse_int(os.getenv("SEMANTIC_SCHOLAR_LIMIT"), 20)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").strip().lower() or "openrouter"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "minimax/minimax-m3:free").strip() or "minimax/minimax-m3:free"
OPENROUTER_TIMEOUT_SECONDS = parse_int(os.getenv("OPENROUTER_TIMEOUT_SECONDS"), 25)
OPENROUTER_RETRY_LIMIT = parse_int(os.getenv("OPENROUTER_RETRY_LIMIT"), 10)
OPENROUTER_RETRY_INTERVAL_SECONDS = parse_int(os.getenv("OPENROUTER_RETRY_INTERVAL_SECONDS"), 60)
LLM_RELEVANCE_TOPIC = os.getenv("LLM_RELEVANCE_TOPIC", QUERY).strip() or QUERY
LLM_RELEVANCE_SCORE_THRESHOLD = parse_float(
	os.getenv("LLM_RELEVANCE_SCORE_THRESHOLD"),
	0.7,
)

RSS_FEEDS = [
	feed.strip()
	for feed in os.getenv("RSS_FEEDS", "").split(",")
	if feed.strip()
]
