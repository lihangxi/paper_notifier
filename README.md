# Slack Paper Notifier

Daily bot that searches for new papers (arXiv, Crossref, Semantic Scholar, and RSS feeds) and posts a summary to a Slack channel via a Slack app (Web API `chat.postMessage`).

## Setup

1) Create and activate a Python virtual environment.
2) Install dependencies:

```bash
pip install -r requirements.txt
pip install -e .
```

3) Set up a Slack app and bot token (this replaces the deprecated legacy Incoming Webhooks custom integration):
   1. Go to https://api.slack.com/apps → **Create New App** → *From scratch*, pick a name and workspace.
   2. Under **OAuth & Permissions → Scopes → Bot Token Scopes**, add `chat:write` (add `chat:write.public` if you want to post to public channels without adding the bot to each one).
   3. Under **OAuth & Permissions**, click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-…`).
   4. Copy `.env.example` to `.env` and set `SLACK_BOT_TOKEN` to that token and `SLACK_CHANNEL` to the target channel (e.g. `#paper-notifications` or a channel ID like `C123ABC456`). Invite the bot to the channel with `/invite @your-app`.
4) (Optional) Create a `keywords.txt` file to filter papers by author, title, or abstract patterns. Use sections `AUTHOR`, `TITLE`, `ABSTRACT` with regex or wildcard patterns (one per line).
5) (Optional) Configure an LLM provider to generate paper summaries and concept keywords (both can be enabled/disabled via env toggles).

LLM provider options:

```dotenv
LLM_PROVIDER=deepseek

# --- Official DeepSeek platform (OpenAI-compatible) -----------------------
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_THINKING_ENABLED=false   # deepseek-chat accepts no thinking/effort params
DEEPSEEK_REASONING_EFFORT=

# --- OpenRouter (only when LLM_PROVIDER=openrouter) -----------------------
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=dots-studio/dots-3-note-preview:free

# --- Shared LLM request settings (retries/timeouts) -----------------------
OPENROUTER_TIMEOUT_SECONDS=25
OPENROUTER_RETRY_LIMIT=10
OPENROUTER_RETRY_INTERVAL_SECONDS=60

SUMMARY_LLM_ENABLED=true
KEYWORD_LLM_ENABLED=true
IMPACT_GENERATION_ENABLED=true
```

LLM requests use an OpenAI-compatible chat-completions API. Two providers are supported, selected with `LLM_PROVIDER`:

- `deepseek` — the official DeepSeek platform (`https://api.deepseek.com`), using `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` (default `deepseek-chat`). `deepseek-chat` takes no thinking/effort parameters; only enable `DEEPSEEK_THINKING_ENABLED` if you point `DEEPSEEK_MODEL` at a model that supports thinking.
- `openrouter` (also the default when unset) — the OpenRouter aggregator, using `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` / `OPENROUTER_MODEL`. Free `:free` model slugs change frequently; if you see 404s, pick a current slug from the catalog or use a paid model.

Retries on HTTP `429` and timeouts are shared settings (`OPENROUTER_TIMEOUT_SECONDS`, `OPENROUTER_RETRY_LIMIT`, `OPENROUTER_RETRY_INTERVAL_SECONDS`). Quota-exhaustion 429s are detected and stop retrying immediately, disabling LLM calls for that run.

If you use a DeepSeek model via OpenRouter, `DEEPSEEK_THINKING_ENABLED` controls thinking mode and `DEEPSEEK_REASONING_EFFORT` supports `high` or `max`.

**Note:** `.env`, `keywords.txt`, and `logs/` are user-specific and excluded from git (see `.gitignore`). They will not be committed to the repository.

## Knowledge-base relevance (Zotero library)

By default relevance is scored by an LLM prompt against the single `LLM_RELEVANCE_TOPIC` string. To instead evaluate each paper against your **own research library**, enable the Zotero knowledge-base mode. It runs entirely with **free, open-source models locally** (no per-paper API cost):

1. Install the optional dependencies:

   ```bash
   pip install -e ".[kb]"
   ```

   On first use the models are downloaded from Hugging Face and cached (see
   "Offline model use" below to fetch once and avoid any network access later).

2. Set up Zotero access in `.env`:

   ```dotenv
   KB_RELEVANCE_ENABLED=true

   # Web API (recommended): create a key at https://www.zotero.org/settings/keys
   ZOTERO_API_KEY=...
   ZOTERO_USER_ID=12345            # numeric id shown on the keys page
   # ZOTERO_GROUP_ID=...           # use instead for a group library
   ZOTERO_API_BASE=https://api.zotero.org

   # OR local API (no key; Zotero desktop running with its local API enabled):
   # ZOTERO_API_BASE=http://localhost:23119/api
   # ZOTERO_USER_ID=0

   # Optional: only index items in these collections (comma-separated keys)
   # ZOTERO_COLLECTION_KEYS=ABCD1234,WXYZ5678
   ```

3. Choose scoring models and threshold (see `.env.example` for all options):

   ```dotenv
   # CPU-friendly defaults ...
   KB_EMBEDDING_MODEL=BAAI/bge-base-en-v1.5
   KB_SCORE_THRESHOLD=0.75
   KB_DEVICE=auto
   ```

   For GPU hardware swap in larger models, e.g. `Qwen/Qwen3-Embedding-4B` and `Qwen/Qwen3-Reranker-4B` (both Apache-2.0). `KB_DEVICE=auto` uses CUDA when available and falls back to CPU.

How it works: on each run the Zotero library snapshot is synchronized via the API (cached; unchanged libraries are served from `kb_cache/` using conditional requests). Embeddings are stored per library item together with each item's Zotero version, so when your library changes only **new/changed items are re-embedded** — unchanged items are reused from cache. A paper's relevance is its **maximum embedding-cosine similarity** to any library item (embeddings are L2-normalized, so this is a stable 0–1 score). The paper is kept when that best match reaches `KB_SCORE_THRESHOLD` (default 0.75; from real-run calibration relevant papers score ~0.75–0.85 while unrelated news/biology score below ~0.70). An optional cross-encoder reranker (`KB_RERANKER_ENABLED=true`) adds an informational rerank score to each match but does not drive the decision, because its sigmoid scores are uncalibrated for absolute gating. With `KB_SHOW_IN_SLACK=true`, each posted paper includes a `Library match:` line showing its best-matching library item and cosine score. Per-paper scores are also printed to the log so you can tune the threshold.

When KB mode is enabled, the LLM topic relevance filter is bypassed. If the KB pipeline fails (for example the models are not installed or Zotero cannot be reached), the app falls back to `RESEARCH_FIELD_TERMS` term filtering and prints the reason.

### Offline model use (no re-downloads)

The embedding/reranker weights are cached by Hugging Face after the first download. To avoid **any** Hugging Face network access on each run (slow metadata checks, blocked networks, or accidental re-downloads when the user cache is cleared), prefetch once and then run strictly offline:

1. Download the configured models once (already-cached models are skipped):

   ```bash
   python -m paper_notifier.cli --fetch-kb-models
   ```

   This prints the resolved cache directory. By default it is the user-level Hugging Face cache (`~/.cache/huggingface/hub`). To keep the models inside the project instead (easy to back up or copy to another machine), set:

   ```dotenv
   KB_HF_HOME=kb_cache/hf
   ```

   and run the fetch command again (or copy the existing cache folder there).

2. Disable Hugging Face network access for normal runs:

   ```dotenv
   KB_LOCAL_FILES_ONLY=true
   ```

   With this on, the models are loaded strictly from the local cache (`HF_HUB_OFFLINE=1` / `local_files_only`). If a model is missing, the KB step fails fast with a clear message instead of downloading, and the app falls back to `RESEARCH_FIELD_TERMS` term filtering.

You can also point `KB_EMBEDDING_MODEL` / `KB_RERANKER_MODEL` directly at a local model directory (a folder downloaded from Hugging Face works as-is, no config needed beyond the path).

### Benchmarking embedding models on your own data

`scripts/benchmark_kb_models.py` scores the same candidate papers against your own Zotero library with several embedding models and reports separation metrics (AUC, score distributions, suggested thresholds, CPU timing) for each. It fetches a fresh paper pool, builds positives from the sent-paper log (`logs/matched_papers.log`, with abstracts pulled from arXiv/Crossref) plus author-whitelist matches, and caches library vectors per model (checkpointed, resumable). See the script docstring for the `fetch` / `positives` / `download` / `score` workflow.

## Run

Run once:

```bash
python -m paper_notifier.cli --once
```

Run once and include papers that were already logged in `logs/matched_papers.log`:

```bash
python -m paper_notifier.cli --once --include-sent-papers
```

Run on schedule (daily at configured time):

```bash
python -m paper_notifier.cli --schedule
```

When schedule mode starts, the app prints scheduler status and the next run time.

On Windows you can double-click `run_scheduler.bat` to start the scheduler in a console window (it keeps running and shows logs; close the window to stop).

Send one Slack test message to verify your token and channel:

```bash
python -m paper_notifier.cli --test
```

## VS Code

- Recommended extensions are listed in `.vscode/extensions.json`.
- A ready-to-run task is available in `.vscode/tasks.json`:
	- `paper-notifier: help`

## Notes

- You can filter papers using `keywords.txt` with `AUTHOR`, `TITLE`, `ABSTRACT` sections (regex supported). See the project repo root for an example if one is not present.
- Create `keywords.txt` in the root directory if you want to filter papers; it is not tracked by git. Set `KEYWORDS_FILTER_ENABLED=false` to skip the keywords filter entirely (e.g. when knowledge-base relevance is doing the filtering).
- Crossref results depend on metadata quality; not every record includes abstracts.
- Relevance filtering uses LLM scoring first (`LLM_RELEVANCE_TOPIC` + `LLM_RELEVANCE_SCORE_THRESHOLD`).
- Alternatively, with `KB_RELEVANCE_ENABLED=true`, relevance is scored against your Zotero library using local open-source embedding/reranker models (see "Knowledge-base relevance" above); the LLM topic filter is then bypassed.
- If LLM relevance API fails, the app prints the failure message in terminal and falls back to term filtering with `RESEARCH_FIELD_TERMS`.
- To log matched papers, set `LOG_FILE` (defaults to `logs/matched_papers.log`).
- To enable Semantic Scholar, set `SEMANTIC_SCHOLAR_API_KEY` (optional) and `SEMANTIC_SCHOLAR_LIMIT`.
- To add journal feeds, set `RSS_FEEDS` as a comma-separated list of RSS URLs.
- Within a single run, papers are deduplicated by normalized URL/DOI/title before keyword and relevance filters.
- If you see occasional APScheduler "run time ... was missed" warnings near startup, increase `SCHEDULER_MISFIRE_GRACE_SECONDS` (default `60`).
- Messages are posted with the Slack Web API `chat.postMessage` using `SLACK_BOT_TOKEN` (a bot token with `chat:write`) to `SLACK_CHANNEL`. Optional `SLACK_USERNAME` and `SLACK_ICON_EMOJI` override the bot display name and icon per message.
- If no papers match current filters, the notifier still sends a Slack message indicating zero matched papers.
- If `SUMMARY_LLM_ENABLED=true` and the provider API key is available, each paper includes an LLM-generated summary using title, authors, abstract, and URL content when accessible.
- If `KEYWORD_LLM_ENABLED=true` and the provider API key is available, each paper includes concept-level `Keywords` generated from title and abstract.
- If `IMPACT_GENERATION_ENABLED=true`, the summary ends with one sentence prefixed with `Impact:`; if false, no `Impact:` sentence is generated.
- For DeepSeek models, thinking mode is sent as `extra_body.thinking.type` and effort is sent as `reasoning_effort` (`high`/`max`).
- Slack messages use a single `Summary` entry per paper (no separate `Abstract` or `Impact` entries), do not show URL preview cards, and are split across multiple posts only when they would exceed Slack's message size limit.
- LLM requests retry automatically on HTTP `429` up to `OPENROUTER_RETRY_LIMIT` attempts with `OPENROUTER_RETRY_INTERVAL_SECONDS` pause between attempts.
- Abstract text is cleaned to remove common metadata prefixes (for example `Published online` and leading DOI strings).
- On summary LLM failure (or if disabled), the notifier falls back to abstract-based summary content.
- On keyword LLM failure (or if disabled), the notifier falls back to deterministic title-based concept phrases.
