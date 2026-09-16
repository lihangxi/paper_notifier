from __future__ import annotations

import os

from dotenv import load_dotenv

from .utils import parse_bool, parse_float, parse_int

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "").strip()
SLACK_USERNAME = os.getenv("SLACK_USERNAME", "").strip()
SLACK_ICON_EMOJI = os.getenv("SLACK_ICON_EMOJI", "").strip()
# Post each paper as its own Slack message instead of one combined message
# (individual messages can be pinned or reacted to in Slack).
SLACK_ONE_MESSAGE_PER_PAPER = parse_bool(os.getenv("SLACK_ONE_MESSAGE_PER_PAPER"), False)
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
KEYWORDS_FILTER_ENABLED = parse_bool(os.getenv("KEYWORDS_FILTER_ENABLED"), True)
LOG_FILE = os.getenv("LOG_FILE", "logs/matched_papers.log").strip()
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
SEMANTIC_SCHOLAR_LIMIT = parse_int(os.getenv("SEMANTIC_SCHOLAR_LIMIT"), 20)

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").strip().lower() or "openrouter"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "dots-studio/dots-3-note-preview:free").strip() or "dots-studio/dots-3-note-preview:free"
DEEPSEEK_THINKING_ENABLED = parse_bool(os.getenv("DEEPSEEK_THINKING_ENABLED"), False)
DEEPSEEK_REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "").strip().lower()
# Official DeepSeek platform (OpenAI-compatible) provider settings.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv(
	"DEEPSEEK_BASE_URL", "https://api.deepseek.com"
).strip() or "https://api.deepseek.com"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
OPENROUTER_TIMEOUT_SECONDS = parse_int(os.getenv("OPENROUTER_TIMEOUT_SECONDS"), 25)
OPENROUTER_RETRY_LIMIT = parse_int(os.getenv("OPENROUTER_RETRY_LIMIT"), 10)
OPENROUTER_RETRY_INTERVAL_SECONDS = parse_int(os.getenv("OPENROUTER_RETRY_INTERVAL_SECONDS"), 60)
LLM_RELEVANCE_TOPIC = os.getenv("LLM_RELEVANCE_TOPIC", QUERY).strip() or QUERY
LLM_RELEVANCE_SCORE_THRESHOLD = parse_float(
	os.getenv("LLM_RELEVANCE_SCORE_THRESHOLD"),
	0.7,
)
IMPACT_GENERATION_ENABLED = parse_bool(os.getenv("IMPACT_GENERATION_ENABLED"), True)
SUMMARY_LLM_ENABLED = parse_bool(os.getenv("SUMMARY_LLM_ENABLED"), True)
KEYWORD_LLM_ENABLED = parse_bool(os.getenv("KEYWORD_LLM_ENABLED"), True)

RSS_FEEDS = [
	feed.strip()
	for feed in os.getenv("RSS_FEEDS", "").split(",")
	if feed.strip()
]

# Knowledge-base relevance (Zotero library). When KB_RELEVANCE_ENABLED is true,
# paper relevance is scored against the user's Zotero library instead of the
# single-topic LLM prompt. All KB models are open-source and run locally.
KB_RELEVANCE_ENABLED = parse_bool(os.getenv("KB_RELEVANCE_ENABLED"), False)

ZOTERO_API_KEY = os.getenv("ZOTERO_API_KEY", "").strip()
ZOTERO_USER_ID = os.getenv("ZOTERO_USER_ID", "").strip()
ZOTERO_GROUP_ID = os.getenv("ZOTERO_GROUP_ID", "").strip()
ZOTERO_API_BASE = os.getenv(
	"ZOTERO_API_BASE", "https://api.zotero.org"
).strip() or "https://api.zotero.org"
ZOTERO_COLLECTION_KEYS = [
	key.strip()
	for key in os.getenv("ZOTERO_COLLECTION_KEYS", "").split(",")
	if key.strip()
]
ZOTERO_CACHE_DIR = os.getenv("ZOTERO_CACHE_DIR", "kb_cache").strip() or "kb_cache"

KB_EMBEDDING_MODEL = os.getenv(
	"KB_EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5"
).strip() or "BAAI/bge-base-en-v1.5"
# Optional sentence-transformers prompt name applied to the paper (query) side,
# e.g. "query" for Qwen3-Embedding models. Leave empty for BGE models.
KB_QUERY_PROMPT = os.getenv("KB_QUERY_PROMPT", "").strip()
KB_RERANKER_MODEL = os.getenv(
	"KB_RERANKER_MODEL", "BAAI/bge-reranker-base"
).strip() or "BAAI/bge-reranker-base"
KB_RERANKER_ENABLED = parse_bool(os.getenv("KB_RERANKER_ENABLED"), False)
KB_TOP_K = parse_int(os.getenv("KB_TOP_K"), 20)
KB_SCORE_THRESHOLD = parse_float(os.getenv("KB_SCORE_THRESHOLD"), 0.75)
KB_DEVICE = os.getenv("KB_DEVICE", "").strip().lower()
KB_BATCH_SIZE = parse_int(os.getenv("KB_BATCH_SIZE"), 32)
KB_SHOW_IN_SLACK = parse_bool(os.getenv("KB_SHOW_IN_SLACK"), True)
# Hugging Face cache handling for the local KB models. KB_HF_HOME optionally
# relocates the cache (e.g. kb_cache/hf) so models live inside the project;
# KB_LOCAL_FILES_ONLY loads them strictly from the local cache with all
# Hugging Face network access disabled (use --fetch-kb-models to populate).
KB_HF_HOME = os.getenv("KB_HF_HOME", "").strip()
KB_LOCAL_FILES_ONLY = parse_bool(os.getenv("KB_LOCAL_FILES_ONLY"), False)
