# Feishu Paper Notifier

Daily bot that searches for new papers (arXiv, Crossref, Semantic Scholar, and RSS feeds) and posts a summary to a Feishu webhook.

## Setup

1) Create and activate a Python virtual environment.
2) Install dependencies:

```bash
pip install -r requirements.txt
pip install -e .
```

3) Copy `.env.example` to `.env` and fill in your configuration values (especially `FEISHU_WEBHOOK_URL`).
4) (Optional) Create a `keywords.txt` file to filter papers by author, title, or abstract patterns. Use sections `AUTHOR`, `TITLE`, `ABSTRACT` with regex or wildcard patterns (one per line).
5) (Optional) Configure an LLM provider to generate paper summaries and concept keywords (both can be enabled/disabled via env toggles).

LLM provider options:

```dotenv
LLM_PROVIDER=openrouter

OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=openrouter/free

SILICONFLOW_API_KEY=
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_MODEL=Qwen/Qwen2.5-7B-Instruct

DEEPSEEK_THINKING_ENABLED=true
DEEPSEEK_REASONING_EFFORT=high

OPENROUTER_TIMEOUT_SECONDS=25
OPENROUTER_RETRY_LIMIT=10
OPENROUTER_RETRY_INTERVAL_SECONDS=60

SUMMARY_LLM_ENABLED=true
KEYWORD_LLM_ENABLED=true
IMPACT_GENERATION_ENABLED=true
```

Set `LLM_PROVIDER=siliconflow` to use SiliconFlow via the OpenAI-compatible API.
If you use a DeepSeek model on an OpenAI-compatible endpoint, `DEEPSEEK_THINKING_ENABLED` controls thinking mode and `DEEPSEEK_REASONING_EFFORT` supports `high` or `max`.

Recommended Feishu Flow config (single summary field):

```dotenv
FEISHU_WEBHOOK_TYPE=flow
FLOW_SINGLE_SUMMARY=true
FLOW_FIELD_DESCRIPTION=summary
```

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

Send one Feishu Flow test payload using your configured flow mode/fields:

```bash
python -m paper_notifier.cli --test-flow
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
- For Feishu Flow webhooks, set `FEISHU_WEBHOOK_TYPE=flow` and configure `FLOW_FIELD_DESCRIPTION`.
- If `FLOW_SINGLE_SUMMARY=true`, only `FLOW_FIELD_DESCRIPTION` is used.
- If `FLOW_SINGLE_SUMMARY=false`, `FLOW_FIELD_TITLE`, `FLOW_FIELD_AUTHORS`, and `FLOW_FIELD_DESCRIPTION` are all used (one payload per paper).
- If no papers match current filters, the notifier still sends a Feishu message indicating zero matched papers.
- If `SUMMARY_LLM_ENABLED=true` and provider API key is available, each paper includes an LLM-generated summary using title, authors, abstract, and URL content when accessible.
- If `KEYWORD_LLM_ENABLED=true` and provider API key is available, each paper includes concept-level `Keywords` generated from title and abstract.
- If `IMPACT_GENERATION_ENABLED=true`, the summary ends with one sentence prefixed with `Impact:`; if false, no `Impact:` sentence is generated.
- For DeepSeek models, thinking mode is sent as `extra_body.thinking.type` and effort is sent as `reasoning_effort` (`high`/`max`).
- LLM requests retry automatically on HTTP `429` up to `OPENROUTER_RETRY_LIMIT` attempts with `OPENROUTER_RETRY_INTERVAL_SECONDS` pause between attempts.
- Feishu messages now use a single `Summary` entry per paper (no separate `Abstract` or `Impact` entries).
- Abstract text is cleaned to remove common metadata prefixes (for example `Published online` and leading DOI strings).
- On summary LLM failure (or if disabled), the notifier falls back to abstract-based summary content.
- On keyword LLM failure (or if disabled), the notifier falls back to deterministic title-based concept phrases.
