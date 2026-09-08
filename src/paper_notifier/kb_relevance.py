from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from .models import Paper
from .zotero import ZoteroItem

_INSTALL_HINT = (
    "Knowledge-base relevance requires the optional 'kb' dependencies. "
    "Install them with:  pip install -e \".[kb]\"  "
    "(adds sentence-transformers, torch, numpy). "
    "Models are downloaded from Hugging Face on first use."
)


def _pick_device(device: str) -> str:
    requested = (device or "").strip().lower()
    if requested in ("", "auto"):
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"
    return requested


def _load_sentence_transformer(model_name: str, device: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    return SentenceTransformer(model_name, device=device)


def _load_cross_encoder(model_name: str, device: str):
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc
    return CrossEncoder(model_name, device=device)


def _to_float_array(embeddings):
    if hasattr(embeddings, "detach"):
        embeddings = embeddings.detach().cpu()
    if hasattr(embeddings, "numpy"):
        embeddings = embeddings.numpy()
    return np.asarray(embeddings, dtype=np.float32)


def _encode(model, texts: Sequence[str], batch_size: int) -> np.ndarray:
    try:
        embeddings = model.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
    except TypeError:
        embeddings = model.encode(list(texts), batch_size=batch_size)
    return _to_float_array(embeddings)


def _sanitize_model_name(model_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model_name)


def _cache_fingerprint(model_name: str) -> str:
    digest = hashlib.sha1(model_name.encode("utf-8")).hexdigest()[:12]
    return f"embeddings_{digest}.npz"


def _load_vector_cache(path: Path) -> dict[str, tuple[int, np.ndarray]] | None:
    """Load per-item embeddings as {item key: (version, vector)}."""
    try:
        with np.load(path) as data:
            keys = data["keys"].tolist()
            versions = data["versions"].tolist()
            vectors = data["vectors"]
    except (OSError, ValueError, KeyError):
        return None
    if len(keys) != len(versions) or vectors.shape[0] != len(keys):
        return None
    return {
        key: (int(version), vectors[index])
        for index, (key, version) in enumerate(zip(keys, versions))
    }


def _save_vector_cache(
    path: Path,
    item_keys: Sequence[str],
    versions: Sequence[int],
    vectors: np.ndarray,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        keys=np.asarray(list(item_keys), dtype="<U"),
        versions=np.asarray(list(versions), dtype=np.int64),
        vectors=vectors,
    )


@dataclass(frozen=True)
class ScoredMatch:
    item: ZoteroItem
    score: float  # embedding cosine similarity (L2-normalized dot product, 0-1)
    rerank_score: float | None = None  # optional cross-encoder sigmoid score (informational)


def paper_text_for_embedding(paper: Paper) -> str:
    """Textual representation of a paper used as the retrieval query."""
    parts = [paper.title or "", paper.abstract or ""]
    parts.extend(paper.authors or [])
    return " ".join(part for part in parts if part)


class KnowledgeBaseIndex:
    """Local embedding index over Zotero library items with optional reranking.

    Relevance is decided by the maximum embedding cosine similarity between the
    paper and any library item (well-calibrated for absolute thresholding). The
    optional cross-encoder reranker only enriches each match with an extra
    informational score; it does NOT drive the ranking or the decision.
    """

    def __init__(
        self,
        items: Sequence[ZoteroItem],
        *,
        embedding_model: str,
        use_reranker: bool,
        reranker_model: str | None,
        top_k: int = 20,
        device: str = "",
        batch_size: int = 32,
        cache_dir: Path | None = None,
    ) -> None:
        self.items = list(items)
        if not self.items:
            raise RuntimeError("No Zotero library items to index")

        self.top_k = max(1, int(top_k))
        self.batch_size = max(1, int(batch_size))
        self.device = _pick_device(device)
        self.embedding_model = embedding_model

        self.embedder = _load_sentence_transformer(embedding_model, self.device)
        self.reranker = None
        if use_reranker:
            if not reranker_model:
                raise RuntimeError("KB_RERANKER_MODEL is required when KB_RERANKER_ENABLED=true")
            self.reranker = _load_cross_encoder(reranker_model, self.device)

        self._vectors = self._embed_library(
            cache_dir=Path(cache_dir) if cache_dir else None,
        )

    # ------------------------------------------------------------------ #
    # Library embedding. Per-item cache (keyed by model) tracks each Zotero
    # item's version, so only new/changed items are re-embedded per run.
    # ------------------------------------------------------------------ #
    def _embed_library(self, cache_dir: Path | None) -> np.ndarray:
        cache_path: Path | None = None
        if cache_dir is not None:
            cache_path = cache_dir / _cache_fingerprint(self.embedding_model)

        stored: dict[str, tuple[int, np.ndarray]] = {}
        if cache_path is not None and cache_path.exists():
            loaded = _load_vector_cache(cache_path)
            if loaded is not None:
                stored = loaded

        reusable: dict[str, np.ndarray] = {}
        to_embed: list[ZoteroItem] = []
        for item in self.items:
            entry = stored.get(item.key)
            if entry is not None and entry[0] >= item.version:
                reusable[item.key] = entry[1]
            else:
                to_embed.append(item)

        if to_embed:
            print(
                f"[paper-notifier] embedding {len(to_embed)} new/changed Zotero items "
                f"(model={self.embedding_model}, device={self.device}); "
                f"{len(reusable)} reused from cache"
            )
            new_vectors = _encode(
                self.embedder, [item.search_text() for item in to_embed], self.batch_size
            )
            new_by_key = {
                item.key: new_vectors[index] for index, item in enumerate(to_embed)
            }
        else:
            print(
                "[paper-notifier] no changed Zotero items; "
                f"reusing all {len(reusable)} cached embeddings"
            )
            new_by_key = {}

        vectors = np.asarray(
            [
                reusable[item.key]
                if item.key in reusable
                else new_by_key[item.key]
                for item in self.items
            ],
            dtype=np.float32,
        )

        if cache_path is not None:
            _save_vector_cache(
                cache_path,
                [item.key for item in self.items],
                [item.version for item in self.items],
                vectors,
            )
            print(f"[paper-notifier] saved library embeddings to {cache_path}")
        return vectors

    # ------------------------------------------------------------------ #
    # Scoring (cosine-based; reranker optional & informational only)
    # ------------------------------------------------------------------ #
    def score_text(self, text: str) -> list[ScoredMatch]:
        query = " ".join((text or "").split())
        if not query:
            return []

        query_vector = _encode(self.embedder, [query], self.batch_size)[0]
        similarities = self._vectors @ query_vector  # both are L2-normalized

        k = min(self.top_k, len(self.items))
        if k == 0:
            return []
        if k < len(self.items):
            top_indices = np.argpartition(-similarities, kth=k - 1)[:k]
        else:
            top_indices = np.arange(len(self.items))
        order = top_indices[np.argsort(-similarities[top_indices])]
        candidate_indices = [int(index) for index in order]

        rerank_by_index: dict[int, float] = {}
        if self.reranker is not None:
            pairs = [[query, self.items[index].search_text()] for index in candidate_indices]
            logits = _to_float_array(
                self.reranker.predict(pairs, batch_size=self.batch_size)
            ).reshape(-1)
            sigmoid_scores = (1.0 / (1.0 + np.exp(-logits.astype(np.float64)))).tolist()
            rerank_by_index = dict(zip(candidate_indices, sigmoid_scores))

        return [
            ScoredMatch(
                item=self.items[index],
                score=max(0.0, min(1.0, float(similarities[index]))),
                rerank_score=rerank_by_index.get(index),
            )
            for index in candidate_indices
        ]

    def score_paper(self, paper: Paper) -> list[ScoredMatch]:
        return self.score_text(paper_text_for_embedding(paper))


__all__ = [
    "KnowledgeBaseIndex",
    "ScoredMatch",
    "paper_text_for_embedding",
]
