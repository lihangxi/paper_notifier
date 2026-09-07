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
LLM_PROVIDER=openrouter

OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=minimax/minimax-m3:free

DEEPSEEK_THINKING_ENABLED=true
DEEPSEEK_REASONING_EFFORT=high

OPENROUTER_TIMEOUT_SECONDS=25
OPENROUTER_RETRY_LIMIT=10
OPENROUTER_RETRY_INTERVAL_SECONDS=60

SUMMARY_LLM_ENABLED=true
KEYWORD_LLM_ENABLED=true
IMPACT_GENERATION_ENABLED=true
```

LLM requests go through OpenRouter (OpenAI-compatible API). The default free model is `minimax/minimax-m3:free`; set `OPENROUTER_MODEL` to any OpenRouter model slug. Only `LLM_PROVIDER=openrouter` is supported.

If you use a DeepSeek model via OpenRouter, `DEEPSEEK_THINKING_ENABLED` controls thinking mode and `DEEPSEEK_REASONING_EFFORT` supports `high` or `max`.

**Note:** `.env`, `keywords.txt`, and `logs/` are user-specific and excluded from git (see `.gitignore`). They will not be committed to the repository.

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
- Create `keywords.txt` in the root directory if you want to filter papers; it is not tracked by git.
- Crossref results depend on metadata quality; not every record includes abstracts.
- Relevance filtering uses LLM scoring first (`LLM_RELEVANCE_TOPIC` + `LLM_RELEVANCE_SCORE_THRESHOLD`).
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
