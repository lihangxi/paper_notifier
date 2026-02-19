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
5) (Optional) Set `OPENROUTER_API_KEY` to generate social/scientific impact explanations for each paper using `openrouter/free`.

OpenRouter-related options:

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openrouter/free
OPENROUTER_TIMEOUT_SECONDS=25
```

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
- To filter by authors, set `KEY_AUTHORS` as a comma-separated list in `.env`.
- To log matched papers, set `LOG_FILE` (defaults to `logs/matched_papers.log`).
- To enable Semantic Scholar, set `SEMANTIC_SCHOLAR_API_KEY` (optional) and `SEMANTIC_SCHOLAR_LIMIT`.
- To add journal feeds, set `RSS_FEEDS` as a comma-separated list of RSS URLs.
- For Feishu Flow webhooks, set `FEISHU_WEBHOOK_TYPE=flow` and configure `FLOW_FIELD_DESCRIPTION`.
- If `FLOW_SINGLE_SUMMARY=true`, only `FLOW_FIELD_DESCRIPTION` is used.
- If `FLOW_SINGLE_SUMMARY=false`, `FLOW_FIELD_TITLE`, `FLOW_FIELD_AUTHORS`, and `FLOW_FIELD_DESCRIPTION` are all used (one payload per paper).
- If `OPENROUTER_API_KEY` is configured, each paper includes an LLM-generated impact note using title, authors, abstract, and paper URL.
- Impact output is normalized to two lines for consistent formatting:
	- `Scientific impact: ...`
	- `Social or industry impact: ...`
- If one impact line is missing from LLM output, the notifier auto-fills a concise fallback line to keep both categories present.
- Abstract text is cleaned to remove common metadata prefixes (for example `Published online` and leading DOI strings).
- On OpenRouter API failure or missing key, the notifier falls back to heuristic impact text.
