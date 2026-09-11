from __future__ import annotations

import hashlib
import inspect
import os
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
    "Models are downloaded from Hugging Face on first use "
    "(or prefetch once with: python -m paper_notifier.cli --fetch-kb-models)."
)


def _apply_hf_env(*, hf_home: str = "", local_files_only: bool = False) -> None:
    """Configure Hugging Face caching/offline mode before heavy imports.

    ``sentence_transformers`` pulls in ``huggingface_hub``/``transformers``,
    which read these environment variables at import time, so this must run
    before any of them is imported (all imports here are lazy for that reason).
    """
    if hf_home:
        os.environ["HF_HOME"] = str(Path(hf_home).expanduser())
    if local_files_only:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


def _supports_local_files_only(cls) -> bool:
    try:
        return "local_files_only" in inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):
        return False


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


def _load_sentence_transformer(
    model_name: str, device: str, local_files_only: bool = False
):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc

    kwargs: dict = {"device": device}
    if local_files_only and _supports_local_files_only(SentenceTransformer):
        kwargs["local_files_only"] = True
    # Even without the kwarg (older versions) the HF_HUB_OFFLINE env var set by
    # _apply_hf_env keeps loading strictly local.
    return SentenceTransformer(model_name, **kwargs)


def _load_cross_encoder(
    model_name: str, device: str, local_files_only: bool = False
):
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError(_INSTALL_HINT) from exc

    kwargs: dict = {"device": device}
    if local_files_only and _supports_local_files_only(CrossEncoder):
        kwargs["local_files_only"] = True
    return CrossEncoder(model_name, **kwargs)


def download_kb_models(
    *,
    embedding_model: str,
    reranker_model: str | None = None,
    device: str = "",
    hf_home: str = "",
) -> str:
    """Download the configured KB models once into the local HF cache.

    Used by ``python -m paper_notifier.cli --fetch-kb-models``. Returns the
    resolved Hugging Face hub cache directory.
    """
    _apply_hf_env(hf_home=hf_home, local_files_only=False)
    resolved_device = _pick_device(device)
    _load_sentence_transformer(embedding_model, resolved_device)
    if reranker_model:
        _load_cross_encoder(reranker_model, resolved_device)

    try:
        from huggingface_hub import constants as hf_constants

        cache = getattr(hf_constants, "HF_HUB_CACHE", "")
        if cache:
            return str(cache)
    except Exception:
        pass
    root = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return str(Path(root) / "hub")


def _to_float_array(embeddings):
    if hasattr(embeddings, "detach"):
        embeddings = embeddings.detach().cpu()
    if hasattr(embeddings, "numpy"):
        embeddings = embeddings.numpy()
    return np.asarray(embeddings, dtype=np.float32)


def _encode(
    model,
    texts: Sequence[str],
    batch_size: int,
    prompt_name: str | None = None,
) -> np.ndarray:
    kwargs: dict = {
        "batch_size": batch_size,
        "normalize_embeddings": True,
        "show_progress_bar": False,
    }
    if prompt_name:
        kwargs["prompt_name"] = prompt_name
    try:
        embeddings = model.encode(list(texts), **kwargs)
    except TypeError:
        # Older sentence-transformers without prompt_name support.
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
        local_files_only: bool = False,
        hf_home: str = "",
        query_prompt: str = "",
    ) -> None:
        self.items = list(items)
        if not self.items:
            raise RuntimeError("No Zotero library items to index")

        # Must happen before sentence_transformers is imported (lazy loaders).
        _apply_hf_env(hf_home=hf_home, local_files_only=local_files_only)

        self.top_k = max(1, int(top_k))
        self.batch_size = max(1, int(batch_size))
        self.device = _pick_device(device)
        self.embedding_model = embedding_model
        self.query_prompt = (query_prompt or "").strip()

        self.embedder = _load_sentence_transformer(
            embedding_model, self.device, local_files_only
        )
        self.reranker = None
        if use_reranker:
            if not reranker_model:
                raise RuntimeError("KB_RERANKER_MODEL is required when KB_RERANKER_ENABLED=true")
            self.reranker = _load_cross_encoder(
                reranker_model, self.device, local_files_only
            )

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

        query_vector = _encode(
            self.embedder, [query], self.batch_size, self.query_prompt or None
        )[0]
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
    "download_kb_models",
    "paper_text_for_embedding",
]
