# Feishu Paper Notifier

Daily bot that searches for new papers (arXiv + Crossref) and posts a summary to a Feishu group via webhook.

## Setup

1) Create and activate a Python virtual environment.
2) Install dependencies:

```bash
pip install -r requirements.txt
pip install -e .
```

3) Copy `.env.example` to `.env` and fill in your configuration values (especially `FEISHU_WEBHOOK_URL`).
4) (Optional) Create a `keywords.txt` file to filter papers by author, title, or abstract patterns. Use sections `AUTHOR`, `TITLE`, `ABSTRACT` with regex or wildcard patterns (one per line).

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

Send one minimal Feishu Flow test payload (`title`, `author`, `abstract`):

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
- For Feishu Flow webhooks, set `FEISHU_WEBHOOK_TYPE=flow` and configure `FLOW_FIELD_TITLE`, `FLOW_FIELD_AUTHORS`, `FLOW_FIELD_DESCRIPTION`.
