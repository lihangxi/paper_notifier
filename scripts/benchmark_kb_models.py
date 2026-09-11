"""Benchmark embedding models for the Zotero knowledge-base relevance filter.

Compares candidate sentence-transformers embedding models on YOUR data:

* positives: papers the daily pipeline actually sent (logs/matched_papers.log,
  with abstracts fetched from arXiv/Crossref) plus fresh-pool papers whose
  authors match the curated whitelist in keywords.txt.
* negatives: the rest of a fresh fetch pool (news, other fields, off-topic).

For every model it reports how well the max embedding cosine to the Zotero
library separates positives from negatives (AUC), score distributions,
suggested thresholds and CPU timing. Nothing here touches Slack.

Typical workflow:

  python scripts/benchmark_kb_models.py download
  python scripts/benchmark_kb_models.py fetch
  python scripts/benchmark_kb_models.py positives
  python scripts/benchmark_kb_models.py score
"""

from __future__ import annotations

import argparse
import gc
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

BENCH_DIR = REPO_ROOT / "kb_cache" / "bench"
POOL_PATH = BENCH_DIR / "pool.json"
POOL_INDEX_PATH = BENCH_DIR / "pool_index.txt"
POSITIVES_PATH = BENCH_DIR / "positives.json"
RESULTS_PATH = BENCH_DIR / "results.json"
SCORES_CSV_PATH = BENCH_DIR / "scores.csv"

# short name -> (huggingface model id, query prompt name, trust_remote_code)
VARIANTS: dict[str, tuple[str, str, bool]] = {
    "bge-base": ("BAAI/bge-base-en-v1.5", "", False),
    "bge-large": ("BAAI/bge-large-en-v1.5", "", False),
    "bge-large-instr": ("BAAI/bge-large-en-v1.5", "query", False),
    "gte-large": ("Alibaba-NLP/gte-large-en-v1.5", "", True),
    "gte-modernbert": ("Alibaba-NLP/gte-modernbert-base", "", False),
    "qwen3-0.6b": ("Qwen/Qwen3-Embedding-0.6B", "", False),
    "qwen3-0.6b-instr": ("Qwen/Qwen3-Embedding-0.6B", "query", False),
}
DEFAULT_VARIANTS = list(VARIANTS)


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


def _normalize_title(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip().lower())
    return re.sub(r"[^a-z0-9 ]+", "", cleaned)


def _normalize_url(value: str) -> str:
    raw = (value or "").strip().lower()
    raw = re.sub(r"^https?://", "", raw)
    return raw.rstrip("/")


def _strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _paper_record(paper) -> dict:
    return {
        "title": paper.title,
        "authors": list(paper.authors or []),
        "abstract": paper.abstract or "",
        "url": paper.url or "",
        "source": paper.source or "",
        "published": paper.published.isoformat() if paper.published else "",
    }


def _record_to_paper(record: dict):
    from paper_notifier.models import Paper

    published = None
    raw = record.get("published") or ""
    if raw:
        try:
            published = datetime.fromisoformat(raw)
        except ValueError:
            published = None
    return Paper(
        title=record.get("title") or "",
        authors=list(record.get("authors") or []),
        abstract=record.get("abstract") or "",
        summary="",
        url=record.get("url") or "",
        source=record.get("source") or "",
        published=published or datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Subcommand: download
# --------------------------------------------------------------------------- #
def cmd_download(args: argparse.Namespace) -> None:
    from paper_notifier import config as cfg
    from paper_notifier.kb_relevance import _apply_hf_env

    hf_home = str(REPO_ROOT / cfg.KB_HF_HOME) if cfg.KB_HF_HOME else ""
    _apply_hf_env(hf_home=hf_home, local_files_only=False)

    from sentence_transformers import SentenceTransformer

    models: list[tuple[str, bool]] = []
    for name in args.models:
        hf_name, _prompt, trust_remote = VARIANTS[name]
        if (hf_name, trust_remote) not in models:
            models.append((hf_name, trust_remote))

    for hf_name, trust_remote in models:
        print(f"[bench] downloading {hf_name} ...", flush=True)
        SentenceTransformer(hf_name, device="cpu", trust_remote_code=trust_remote)

    from huggingface_hub import constants as hf_constants

    print(f"[bench] models cached in: {hf_constants.HF_HUB_CACHE}")


# --------------------------------------------------------------------------- #
# Subcommand: fetch (fresh candidate pool)
# --------------------------------------------------------------------------- #
def cmd_fetch(args: argparse.Namespace) -> None:
    from paper_notifier import config as cfg
    from paper_notifier.sources.arxiv import fetch_arxiv
    from paper_notifier.sources.crossref import fetch_crossref
    from paper_notifier.sources.rss import fetch_rss
    from paper_notifier.sources.semantic_scholar import fetch_semantic_scholar

    days = args.days
    print(f"[bench] fetching pool (days_back={days}) for query {cfg.QUERY!r} ...")

    papers = []
    fetchers = [
        ("arxiv", lambda: fetch_arxiv(cfg.QUERY, args.arxiv, days)),
        ("crossref", lambda: fetch_crossref(cfg.QUERY, args.crossref, days, cfg.CROSSREF_MAILTO)),
        (
            "semantic_scholar",
            lambda: fetch_semantic_scholar(cfg.QUERY, args.semantic_scholar, days, cfg.SEMANTIC_SCHOLAR_API_KEY),
        ),
        ("rss", lambda: fetch_rss(cfg.RSS_FEEDS, days)),
    ]
    for source_name, fetcher in fetchers:
        try:
            found = fetcher()
            print(f"[bench]   {source_name}: {len(found)} papers")
            papers.extend(found)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"[bench]   {source_name} failed: {exc}")

    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    records: list[dict] = []
    for paper in papers:
        title_key = _normalize_title(paper.title)
        url_key = _normalize_url(paper.url)
        if (title_key and title_key in seen_titles) or (url_key and url_key in seen_urls):
            continue
        if title_key:
            seen_titles.add(title_key)
        if url_key:
            seen_urls.add(url_key)
        records.append(_paper_record(paper))

    _write_json(POOL_PATH, records)
    with POOL_INDEX_PATH.open("w", encoding="utf-8") as handle:
        for index, record in enumerate(records):
            authors = ", ".join(record["authors"][:2])
            has_abstract = "A" if record["abstract"] else "-"
            handle.write(
                f"[{index:03d}] ({has_abstract}) {record['title']} | {authors} | {record['url']}\n"
            )
    print(f"[bench] pool: {len(records)} unique papers -> {POOL_PATH}")
    print(f"[bench] compact index written to {POOL_INDEX_PATH}")


# --------------------------------------------------------------------------- #
# Subcommand: positives (historically sent papers + abstracts)
# --------------------------------------------------------------------------- #
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s?#]+")


def _fetch_arxiv_abstracts(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"id_list": ",".join(ids), "max_results": len(ids)}
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read().decode("utf-8", errors="replace")

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    results: dict[str, dict] = {}
    for entry in ET.fromstring(payload).findall("atom:entry", ns):
        raw_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
        match = re.search(r"abs/([0-9]{4}\.[0-9]{4,5})", raw_id)
        if not match:
            continue
        title = _strip_markup(entry.findtext("atom:title", default="", namespaces=ns))
        summary = _strip_markup(entry.findtext("atom:summary", default="", namespaces=ns))
        authors = [
            (author.findtext("atom:name", default="", namespaces=ns) or "").strip()
            for author in entry.findall("atom:author", ns)
        ]
        results[match.group(1)] = {
            "title": title,
            "abstract": summary,
            "authors": [name for name in authors if name],
        }
    return results


def _fetch_crossref_abstract(doi: str, mailto: str) -> dict | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    if mailto:
        url += "?mailto=" + urllib.parse.quote(mailto)
    request = urllib.request.Request(url, headers={"User-Agent": "paper-notifier-benchmark"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            message = json.loads(response.read().decode("utf-8", errors="replace"))["message"]
    except Exception:
        return None
    abstract = _strip_markup(message.get("abstract") or "")
    if not abstract:
        return None
    authors = [
        f"{author.get('given', '')} {author.get('family', '')}".strip()
        for author in message.get("author") or []
    ]
    return {
        "title": _strip_markup(message.get("title", [""])[0] if message.get("title") else ""),
        "abstract": abstract,
        "authors": [name for name in authors if name],
    }


def cmd_positives(args: argparse.Namespace) -> None:
    from paper_notifier import config as cfg

    log_path = REPO_ROOT / cfg.LOG_FILE
    if not log_path.exists():
        raise SystemExit(f"[bench] log file not found: {log_path}")

    entries: dict[str, dict] = {}
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        parts = line[2:].split(" | ")
        if len(parts) < 5:
            continue
        title, authors_raw, source, _date, url = parts[0], parts[1], parts[2], parts[3], parts[4]
        authors = [
            name.strip()
            for name in authors_raw.split(",")
            if name.strip() and name.strip().lower() != "et al."
        ]
        key = _normalize_title(title)
        if not key:
            continue
        entries.setdefault(key, {"title": title, "authors": authors, "source": source, "url": url, "sent": True})

    print(f"[bench] {len(entries)} unique sent papers found in {log_path}")

    arxiv_map: dict[str, dict] = {}
    arxiv_ids = []
    for entry in entries.values():
        match = _ARXIV_RE.search(entry["url"])
        if match:
            arxiv_ids.append(match.group(1))
    if arxiv_ids:
        print(f"[bench] fetching {len(arxiv_ids)} arXiv abstracts ...")
        arxiv_map = _fetch_arxiv_abstracts(arxiv_ids)

    records: list[dict] = []
    skipped = 0
    for entry in entries.values():
        enriched = None
        match = _ARXIV_RE.search(entry["url"])
        if match:
            enriched = arxiv_map.get(match.group(1))
        if enriched is None:
            doi = ""
            doi_match = _DOI_RE.search(entry["url"])
            if doi_match:
                doi = doi_match.group(0).rstrip(".,;:)")
            elif "nature.com" in entry["url"]:
                slug = entry["url"].rstrip("/").split("/")[-1]
                if slug.startswith("s"):
                    doi = "10.1038/" + slug
            if doi:
                enriched = _fetch_crossref_abstract(doi, cfg.CROSSREF_MAILTO)
        if enriched is None or not enriched.get("abstract"):
            skipped += 1
            continue
        records.append(
            {
                "title": entry["title"],
                "authors": enriched.get("authors") or entry["authors"],
                "abstract": enriched["abstract"],
                "url": entry["url"],
                "source": entry["source"],
                "published": "",
            }
        )

    _write_json(POSITIVES_PATH, records)
    print(f"[bench] positives with abstracts: {len(records)} (skipped {skipped}) -> {POSITIVES_PATH}")


# --------------------------------------------------------------------------- #
# Subcommand: score
# --------------------------------------------------------------------------- #
def _rankdata(values) -> "object":
    import numpy as np

    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1, dtype=float)
    sorted_values = values[order]
    index = 0
    while index < len(values):
        end = index
        while end + 1 < len(values) and sorted_values[end + 1] == sorted_values[index]:
            end += 1
        if end > index:
            ranks[order[index : end + 1]] = (index + end + 2) / 2.0
        index = end + 1
    return ranks


def _auc(pos, neg) -> float:
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    ranks = _rankdata(list(pos) + list(neg))
    r_pos = float(sum(ranks[: len(pos)]))
    return (r_pos - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def _metrics(pos, neg) -> dict:
    import numpy as np

    pos_a = np.asarray(pos, dtype=float)
    neg_a = np.asarray(neg, dtype=float)
    thresholds = np.unique(np.concatenate([pos_a, neg_a]))

    best_f1, best_threshold, best_precision, best_recall = 0.0, float("nan"), 0.0, 0.0
    recall90 = float("nan")
    for threshold in thresholds:
        tp = float((pos_a >= threshold).sum())
        fp = float((neg_a >= threshold).sum())
        if tp + fp == 0:
            continue
        precision = tp / (tp + fp)
        recall = tp / len(pos_a) if len(pos_a) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best_f1:
            best_f1, best_threshold, best_precision, best_recall = f1, float(threshold), precision, recall
        if len(pos_a) and recall >= 0.90:
            recall90 = float(threshold)

    return {
        "auc": _auc(pos_a, neg_a),
        "pos_mean": float(pos_a.mean()) if len(pos_a) else float("nan"),
        "pos_std": float(pos_a.std(ddof=1)) if len(pos_a) > 1 else 0.0,
        "pos_min": float(pos_a.min()) if len(pos_a) else float("nan"),
        "neg_mean": float(neg_a.mean()) if len(neg_a) else float("nan"),
        "neg_std": float(neg_a.std(ddof=1)) if len(neg_a) > 1 else 0.0,
        "neg_max": float(neg_a.max()) if len(neg_a) else float("nan"),
        "sep": float(pos_a.mean() - neg_a.mean()) if len(pos_a) and len(neg_a) else float("nan"),
        "t_best_f1": best_threshold,
        "best_f1": best_f1,
        "best_precision": best_precision,
        "best_recall": best_recall,
        "t_recall90": recall90,
        "t_zero_fp": float(neg_a.max()) if len(neg_a) else float("nan"),
    }


def cmd_score(args: argparse.Namespace) -> None:
    import numpy as np

    from paper_notifier import config as cfg
    from paper_notifier.keywords import load_keyword_rules
    from paper_notifier.kb_relevance import (
        _apply_hf_env,
        _cache_fingerprint,
        _load_vector_cache,
        _save_vector_cache,
        paper_text_for_embedding,
    )
    from paper_notifier.zotero import _filter_by_collections, _load_cached_library

    cache_dir = REPO_ROOT / cfg.ZOTERO_CACHE_DIR
    library = _load_cached_library(cache_dir)
    if library is None:
        raise SystemExit(
            f"[bench] no cached Zotero snapshot in {cache_dir}; run the pipeline once first"
        )
    items = _filter_by_collections(library.items, cfg.ZOTERO_COLLECTION_KEYS)
    if args.library_sample and args.library_sample < len(items):
        import random

        keep = sorted(random.Random(42).sample(range(len(items)), args.library_sample))
        items = [items[index] for index in keep]
        print(f"[bench] library subsampled to {len(items)} items (seed 42)")
    texts_lib = [item.search_text() for item in items]
    print(f"[bench] library: {len(items)} items (version={library.version})")

    pool = _read_json(POOL_PATH, [])
    positives = _read_json(POSITIVES_PATH, [])
    if not pool:
        raise SystemExit("[bench] run `fetch` first (no candidate pool found)")

    rules = load_keyword_rules(str(REPO_ROOT / cfg.KEYWORDS_FILE))
    print(f"[bench] keyword rules: {rules.keyword_count} patterns (author whitelist)")

    rows: list[dict] = []
    pool_titles = {_normalize_title(record.get("title") or "") for record in pool}
    for record in pool:
        paper = _record_to_paper(record)
        whitelist = bool(rules.has_rules() and rules.matches(paper))
        rows.append(
            {
                "paper": paper,
                "whitelist": whitelist,
                "core": 1 if whitelist else 0,
                "broad": 1 if whitelist else 0,
                "origin": "pool",
                "url": paper.url,
            }
        )

    logged_added = 0
    logged_legacy = 0
    for record in positives:
        if _normalize_title(record.get("title") or "") in pool_titles:
            continue
        if not (record.get("abstract") or "").strip():
            continue
        paper = _record_to_paper(record)
        whitelist = bool(rules.has_rules() and rules.matches(paper))
        rows.append(
            {
                "paper": paper,
                "whitelist": whitelist,
                # Papers sent by older/looser configs without a whitelist match
                # are ambiguous: kept as positives in the broad view only.
                "core": 1 if whitelist else None,
                "broad": 1,
                "origin": "log-whitelist" if whitelist else "log-legacy",
                "url": paper.url,
            }
        )
        logged_added += 1
        if not whitelist:
            logged_legacy += 1

    core_pos_idx = [index for index, row in enumerate(rows) if row["core"] == 1]
    core_neg_idx = [index for index, row in enumerate(rows) if row["core"] == 0]
    broad_pos_idx = [index for index, row in enumerate(rows) if row["broad"] == 1]
    broad_neg_idx = [index for index, row in enumerate(rows) if row["broad"] == 0]
    hard_neg_idx = [
        index
        for index in core_neg_idx
        if "quantum" in (rows[index]["paper"].title + " " + rows[index]["paper"].abstract).lower()
    ]
    print(
        f"[bench] candidates: {len(rows)} | core pos={len(core_pos_idx)} "
        f"neg={len(core_neg_idx)} (hard quantum neg={len(hard_neg_idx)}) | "
        f"broad pos={len(broad_pos_idx)} neg={len(broad_neg_idx)} | "
        f"logged: whitelist={logged_added - logged_legacy}, legacy={logged_legacy}"
    )
    if len(core_pos_idx) < 5:
        print("[bench] WARNING: very few core positives; metrics will be noisy")

    hf_home = str(REPO_ROOT / cfg.KB_HF_HOME) if cfg.KB_HF_HOME else ""
    showcase_indices = [
        index for index in broad_pos_idx if rows[index]["origin"].startswith("log")
    ][: args.showcase]

    all_results: dict[str, dict] = {}
    score_columns: dict[str, list[float]] = {}
    match_columns: dict[str, list[str]] = {}

    for name in args.models:
        hf_name, prompt, trust_remote = VARIANTS[name]
        print(f"\n[bench] ===== {name} ({hf_name}{', prompt=' + prompt if prompt else ''}) =====")

        _apply_hf_env(hf_home=hf_home, local_files_only=True)
        from sentence_transformers import SentenceTransformer

        started = time.time()
        model = SentenceTransformer(
            hf_name, device=args.device, local_files_only=True, trust_remote_code=trust_remote
        )
        model.max_seq_length = args.max_seq_length
        prompts = getattr(model, "prompts", {}) or {}
        if prompt and prompt not in prompts:
            raise SystemExit(
                f"[bench] model {hf_name} has no prompt named {prompt!r} (available: {sorted(prompts)})"
            )
        dim_getter = getattr(model, "get_embedding_dimension", None)
        dim = dim_getter() if callable(dim_getter) else model.get_sentence_embedding_dimension()
        load_seconds = time.time() - started

        cache_path = cache_dir / _cache_fingerprint(hf_name)
        stored = _load_vector_cache(cache_path) or {}
        lib_vectors = np.zeros((len(items), dim), dtype=np.float32)
        completed_flags = np.zeros(len(items), dtype=bool)
        missing: list[int] = []
        for index, item in enumerate(items):
            entry = stored.get(item.key)
            if entry is not None and entry[0] >= item.version and entry[1].shape[0] == dim:
                lib_vectors[index] = entry[1]
                completed_flags[index] = True
            else:
                missing.append(index)

        sample_encoded = model.tokenizer(texts_lib[:200], truncation=False)
        token_lengths = [len(ids) for ids in sample_encoded["input_ids"]]
        over_cap = sum(1 for length in token_lengths if length > model.max_seq_length)
        print(
            f"[bench] model loaded in {load_seconds:.1f}s; max_seq_length={model.max_seq_length}; "
            f"token lengths p50={int(np.percentile(token_lengths, 50))} "
            f"p95={int(np.percentile(token_lengths, 95))} max={max(token_lengths)} "
            f"({over_cap}/200 sample items exceed the cap)",
            flush=True,
        )
        print(
            f"[bench] library vectors: {len(items) - len(missing)} reused from cache, {len(missing)} to embed",
            flush=True,
        )

        started = time.time()
        cursor = 0
        first_chunk = True
        while cursor < len(missing):
            chunk_size = args.probe_items if first_chunk else args.embed_chunk
            batch_indices = missing[cursor : cursor + chunk_size]
            embeddings = np.asarray(
                model.encode(
                    [texts_lib[index] for index in batch_indices],
                    batch_size=args.batch,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                ),
                dtype=np.float32,
            )
            for offset, index in enumerate(batch_indices):
                lib_vectors[index] = embeddings[offset]
                completed_flags[index] = True
            cursor += len(batch_indices)
            done = cursor
            elapsed = time.time() - started
            rate = elapsed / max(1, done)
            print(
                f"[bench]   [{time.strftime('%H:%M:%S')}] library {done}/{len(missing)} "
                f"({rate * 1000:.0f} ms/item, ETA {rate * (len(missing) - done):.0f}s)",
                flush=True,
            )
            _save_vector_cache(
                cache_path,
                [item.key for item, flag in zip(items, completed_flags) if flag],
                [item.version for item, flag in zip(items, completed_flags) if flag],
                lib_vectors[completed_flags],
            )
            if first_chunk:
                first_chunk = False
                if rate * 1000 > args.max_ms_per_item:
                    raise SystemExit(
                        f"[bench] measured {rate * 1000:.0f} ms/item > "
                        f"--max-ms-per-item={args.max_ms_per_item}; aborting pathological run"
                    )
        library_seconds = time.time() - started

        started = time.time()
        candidate_texts = [paper_text_for_embedding(row["paper"]) for row in rows]
        query_vectors = np.zeros((len(rows), dim), dtype=np.float32)
        encode_kwargs: dict = {
            "batch_size": args.batch,
            "normalize_embeddings": True,
            "show_progress_bar": False,
        }
        if prompt:
            encode_kwargs["prompt_name"] = prompt
        for start in range(0, len(rows), args.embed_chunk):
            chunk = candidate_texts[start : start + args.embed_chunk]
            embeddings = np.asarray(model.encode(chunk, **encode_kwargs), dtype=np.float32)
            query_vectors[start : start + len(chunk)] = embeddings
            done = min(start + len(chunk), len(rows))
            elapsed = time.time() - started
            rate = elapsed / max(1, done)
            print(
                f"[bench]   [{time.strftime('%H:%M:%S')}] candidates {done}/{len(rows)} "
                f"({rate * 1000:.0f} ms/item, ETA {rate * (len(rows) - done):.0f}s)",
                flush=True,
            )

        similarities = query_vectors @ lib_vectors.T  # both matrices are L2-normalized
        best_indices = similarities.argmax(axis=1)
        scores = [float(similarities[row_index, best_indices[row_index]]) for row_index in range(len(rows))]
        top_matches = [items[int(best_indices[row_index])].title for row_index in range(len(rows))]
        score_seconds = time.time() - started
        index_seconds = load_seconds + library_seconds

        score_columns[name] = scores
        match_columns[name] = top_matches

        def subset(indices: list[int]) -> list[float]:
            return [scores[index] for index in indices if not np.isnan(scores[index])]

        core_pos, core_neg = subset(core_pos_idx), subset(core_neg_idx)
        broad_pos, broad_neg = subset(broad_pos_idx), subset(broad_neg_idx)
        hard_neg = subset(hard_neg_idx)

        metrics = _metrics(core_pos, core_neg)
        metrics["auc_broad"] = _auc(broad_pos, broad_neg)
        metrics["auc_hard_neg"] = _auc(core_pos, hard_neg)
        metrics["n_core_pos"] = len(core_pos)
        metrics["n_core_neg"] = len(core_neg)
        metrics["n_hard_neg"] = len(hard_neg)
        metrics["index_seconds"] = index_seconds
        metrics["score_seconds_total"] = score_seconds
        metrics["score_ms_per_paper"] = 1000.0 * score_seconds / max(1, len(rows))
        all_results[name] = metrics

        print(
            f"[bench] AUC core={metrics['auc']:.3f} broad={metrics['auc_broad']:.3f} "
            f"hard-neg={metrics['auc_hard_neg']:.3f} | "
            f"core pos {metrics['pos_mean']:.3f}±{metrics['pos_std']:.3f} (min {metrics['pos_min']:.3f}) | "
            f"core neg {metrics['neg_mean']:.3f}±{metrics['neg_std']:.3f} (max {metrics['neg_max']:.3f}) | "
            f"sep={metrics['sep']:.3f}"
        )
        print(
            f"[bench] thresholds: bestF1={metrics['t_best_f1']:.3f} "
            f"(P={metrics['best_precision']:.2f}, R={metrics['best_recall']:.2f}) | "
            f"recall90={metrics['t_recall90']:.3f} | zero-FP={metrics['t_zero_fp']:.3f}"
        )
        print(
            f"[bench] timing: index {index_seconds:.1f}s, scoring {score_seconds:.1f}s "
            f"({metrics['score_ms_per_paper']:.0f} ms/paper)"
        )

        # Showcase: top library match for a few logged positives.
        for index_in_rows in showcase_indices:
            row = rows[index_in_rows]
            title = row["paper"].title[:70]
            match = match_columns[name][index_in_rows][:70]
            score = score_columns[name][index_in_rows]
            print(f"[bench]   {score:.3f}  '{title}'  ->  '{match}'")

        del model
        gc.collect()

    _write_json(
        RESULTS_PATH,
        {
            "generated_at": _now_iso(),
            "library_items": len(items),
            "n_core_pos": len(core_pos_idx),
            "n_core_neg": len(core_neg_idx),
            "n_hard_neg": len(hard_neg_idx),
            "n_broad_pos": len(broad_pos_idx),
            "variants": {
                name: {"model": VARIANTS[name][0], "prompt": VARIANTS[name][1], **metrics}
                for name, metrics in all_results.items()
            },
            "papers": [
                {
                    "url": row["url"],
                    "title": row["paper"].title,
                    "label_broad": row["broad"],
                    "label_core": row["core"],
                    "whitelist": row["whitelist"],
                    "origin": row["origin"],
                    "scores": {name: score_columns[name][index] for name in args.models},
                    "top_match": {name: match_columns[name][index] for name in args.models},
                }
                for index, row in enumerate(rows)
            ],
        },
    )

    csv_header = ["label_broad", "label_core", "origin", "title", "url"] + [
        f"{name}_score" for name in args.models
    ] + [f"{name}_top_match" for name in args.models]
    with SCORES_CSV_PATH.open("w", encoding="utf-8") as handle:
        handle.write(",".join(csv_header) + "\n")
        for index, row in enumerate(rows):
            values = [
                str(row["broad"]),
                "" if row["core"] is None else str(row["core"]),
                row["origin"],
                '"' + row["paper"].title.replace('"', "'") + '"',
                row["url"],
            ]
            values += [f"{score_columns[name][index]:.4f}" for name in args.models]
            values += ['"' + match_columns[name][index].replace('"', "'") + '"' for name in args.models]
            handle.write(",".join(values) + "\n")

    print(f"\n[bench] results -> {RESULTS_PATH}")
    print(f"[bench] per-paper scores -> {SCORES_CSV_PATH}")

    print("\n[bench] summary (AUC core vs whitelist negatives; hard = vs quantum-labelled negatives)")
    header = (
        f"{'model':<18} {'AUC_core':>8} {'AUC_hard':>8} {'AUC_broad':>9} {'pos_mean':>8} "
        f"{'neg_mean':>8} {'F1_thr':>7} {'R90_thr':>8} {'idx_s':>6}"
    )
    print(header)
    for name in args.models:
        metrics = all_results[name]
        print(
            f"{name:<18} {metrics['auc']:>8.3f} {metrics['auc_hard_neg']:>8.3f} "
            f"{metrics['auc_broad']:>9.3f} "
            f"{metrics['pos_mean']:>8.3f} {metrics['neg_mean']:>8.3f} {metrics['t_best_f1']:>7.3f} "
            f"{metrics['t_recall90']:>8.3f} {metrics['index_seconds']:>6.1f}"
        )


def main() -> None:
    _configure_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser("download", help="download benchmark models into the project HF cache")
    download.add_argument("--models", nargs="+", choices=DEFAULT_VARIANTS, default=DEFAULT_VARIANTS)
    download.set_defaults(func=cmd_download)

    fetch = sub.add_parser("fetch", help="fetch a fresh candidate pool (network)")
    fetch.add_argument("--days", type=int, default=7)
    fetch.add_argument("--arxiv", type=int, default=100)
    fetch.add_argument("--crossref", type=int, default=40)
    fetch.add_argument("--semantic-scholar", type=int, default=100)
    fetch.set_defaults(func=cmd_fetch)

    positives = sub.add_parser("positives", help="extract sent papers from the log and fetch abstracts")
    positives.set_defaults(func=cmd_positives)

    score = sub.add_parser("score", help="score the pool with every model and report metrics")
    score.add_argument("--models", nargs="+", choices=DEFAULT_VARIANTS, default=DEFAULT_VARIANTS)
    score.add_argument("--device", default="cpu")
    score.add_argument("--batch", type=int, default=16)
    score.add_argument("--embed-chunk", type=int, default=64)
    score.add_argument(
        "--library-sample",
        type=int,
        default=0,
        help="use a random (seeded) subsample of the library for a fast comparison; 0 = full library",
    )
    score.add_argument(
        "--max-seq-length",
        type=int,
        default=512,
        help="token cap applied to every model (production bge truncates at 512)",
    )
    score.add_argument(
        "--probe-items",
        type=int,
        default=16,
        help="first library chunk size used to measure speed before committing to the full run",
    )
    score.add_argument(
        "--max-ms-per-item",
        type=float,
        default=4000.0,
        help="abort if the first chunk measures slower than this (protects against pathological models)",
    )
    score.add_argument("--showcase", type=int, default=3)
    score.set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
