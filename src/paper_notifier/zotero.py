from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import requests

_ZOTERO_API_VERSION = "3"
_PAGE_SIZE = 100
_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ZoteroItem:
    """A top-level Zotero library item reduced to the fields useful for relevance."""

    key: str
    title: str
    abstract: str
    creators: tuple[str, ...]
    tags: tuple[str, ...]
    date: str
    doi: str
    url: str
    collections: tuple[str, ...] = ()
    version: int = 0  # per-item Zotero version, used for incremental embedding

    def search_text(self) -> str:
        """Combined text used for embedding and display of a library item."""
        parts = [self.title, self.abstract, self.doi, self.url]
        parts.extend(self.creators)
        parts.extend(self.tags)
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class ZoteroLibrary:
    version: int
    items: tuple[ZoteroItem, ...]
    from_cache: bool


def _creator_name(creator: dict) -> str:
    if not isinstance(creator, dict):
        return ""
    first = (creator.get("firstName") or "").strip()
    last = (creator.get("lastName") or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return (creator.get("name") or "").strip()


def _parse_items(records: Sequence[dict]) -> list[ZoteroItem]:
    items: list[ZoteroItem] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        data = record.get("data")
        if not isinstance(data, dict):
            continue
        if data.get("itemType") == "attachment":
            continue

        creators = tuple(
            name for name in (_creator_name(c) for c in data.get("creators") or []) if name
        )
        tags = tuple(
            tag
            for tag in (
                (t.get("tag") or "").strip()
                for t in data.get("tags") or []
                if isinstance(t, dict)
            )
            if tag
        )

        item = ZoteroItem(
            key=str(record.get("key") or data.get("key") or ""),
            title=(data.get("title") or "").strip(),
            abstract=(data.get("abstractNote") or "").strip(),
            creators=creators,
            tags=tags,
            date=str(data.get("date") or "").strip(),
            doi=str(data.get("DOI") or "").strip(),
            url=str(data.get("url") or "").strip(),
            collections=tuple(
                key for key in (data.get("collections") or []) if isinstance(key, str)
            ),
            version=int(record.get("version") or data.get("version") or 0),
        )
        if item.search_text():
            items.append(item)
    return items


def _filter_by_collections(
    items: Sequence[ZoteroItem], collection_keys: Sequence[str]
) -> list[ZoteroItem]:
    if not collection_keys:
        return list(items)
    wanted = set(collection_keys)
    return [item for item in items if wanted.intersection(item.collections)]


def _library_prefix(user_id: str, group_id: str) -> str:
    if group_id.strip():
        return f"/groups/{group_id.strip()}"
    if user_id.strip():
        return f"/users/{user_id.strip()}"
    raise RuntimeError(
        "ZOTERO_USER_ID (or ZOTERO_GROUP_ID for a group library) must be set "
        "when KB_RELEVANCE_ENABLED=true"
    )


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


def _load_cached_library(cache_dir: Path) -> ZoteroLibrary | None:
    items_path = cache_dir / "items.json"
    meta = _read_json(cache_dir / "meta.json", {})
    if not meta or not items_path.exists():
        return None

    records = _read_json(items_path, [])
    if not isinstance(records, list) or not records:
        return None

    items = tuple(
        ZoteroItem(
            key=str(record.get("key") or ""),
            title=str(record.get("title") or ""),
            abstract=str(record.get("abstract") or ""),
            creators=tuple(record.get("creators") or ()),
            tags=tuple(record.get("tags") or ()),
            date=str(record.get("date") or ""),
            doi=str(record.get("doi") or ""),
            url=str(record.get("url") or ""),
            collections=tuple(record.get("collections") or ()),
            version=int(record.get("version") or 0),
        )
        for record in records
    )
    return ZoteroLibrary(
        version=int(meta.get("version") or 0),
        items=items,
        from_cache=True,
    )


def _persist_cached_library(cache_dir: Path, library: ZoteroLibrary) -> None:
    records = [
        {
            "key": item.key,
            "title": item.title,
            "abstract": item.abstract,
            "creators": list(item.creators),
            "tags": list(item.tags),
            "date": item.date,
            "doi": item.doi,
            "url": item.url,
            "collections": list(item.collections),
            "version": item.version,
        }
        for item in library.items
    ]
    _write_json(cache_dir / "items.json", records)
    _write_json(
        cache_dir / "meta.json",
        {"version": library.version, "item_count": len(library.items)},
    )


def _items_request(base_url: str, prefix: str, start: int, limit: int) -> str:
    return (
        f"{base_url}{prefix}/items/top"
        f"?format=json&limit={limit}&start={start}&itemType=-attachment"
    )


def sync_zotero_library(
    cache_dir: Path,
    *,
    api_key: str = "",
    user_id: str = "",
    group_id: str = "",
    api_base: str = "https://api.zotero.org",
    collection_keys: Sequence[str] = (),
) -> ZoteroLibrary:
    """Synchronize the Zotero library snapshot.

    Uses conditional GETs (``If-Modified-Since-Version``) so an unchanged library
    is served from the on-disk cache. A ``304`` response reuses the cached items.
    The complete (unfiltered) library is cached so that changing
    ``collection_keys`` never serves stale, previously-filtered data: the
    collection filter is applied to the returned library after load/fetch.
    """
    cache_dir = Path(cache_dir)
    prefix = _library_prefix(user_id, group_id)
    base_url = api_base.rstrip("/")
    wanted_collections = list(collection_keys)

    headers = {"Zotero-API-Version": _ZOTERO_API_VERSION}
    if api_key:
        headers["Zotero-API-Key"] = api_key

    cached = _load_cached_library(cache_dir)
    if cached is not None and cached.version > 0:
        headers["If-Modified-Since-Version"] = str(cached.version)

    session = requests.Session()
    session.headers.update({"User-Agent": "paper-notifier/1.0"})

    try:
        response = session.get(
            _items_request(base_url, prefix, 0, _PAGE_SIZE),
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
        )
        if response.status_code == 304:
            if cached is not None:
                print(
                    "[paper-notifier] Zotero library unchanged since version "
                    f"{cached.version}; using cached snapshot ({len(cached.items)} items)"
                )
                return _finalize_library(cached, wanted_collections)
        response.raise_for_status()

        version_header = response.headers.get("Last-Modified-Version")
        version = int(version_header) if version_header else 0

        all_records = list(response.json())
        total_header = response.headers.get("Total-Results")
        total = int(total_header) if total_header is not None else len(all_records)
        while len(all_records) < total:
            page = session.get(
                _items_request(base_url, prefix, len(all_records), _PAGE_SIZE),
                headers=headers,
                timeout=_TIMEOUT_SECONDS,
            )
            if page.status_code == 429:
                retry_after = float(page.headers.get("Retry-After") or 5)
                print(f"[paper-notifier] Zotero API rate limited; retrying in {retry_after:.0f}s")
                time.sleep(retry_after)
                continue
            page.raise_for_status()
            records = page.json()
            if not isinstance(records, list):
                break
            all_records.extend(records)
    finally:
        session.close()

    items = _parse_items(all_records)
    if not items:
        raise RuntimeError(
            "Zotero library returned no indexable items "
            "(check ZOTERO_USER_ID/ZOTERO_GROUP_ID)"
        )

    library = ZoteroLibrary(version=version, items=tuple(items), from_cache=False)
    if version > 0:
        _persist_cached_library(cache_dir, library)
    return _finalize_library(library, wanted_collections)


def _finalize_library(library: ZoteroLibrary, collection_keys: Sequence[str]) -> ZoteroLibrary:
    """Apply the optional collection filter to a (full) library snapshot."""
    if not collection_keys:
        return library

    filtered = tuple(_filter_by_collections(library.items, collection_keys))
    if not filtered:
        raise RuntimeError(
            "Zotero library contains no items in ZOTERO_COLLECTION_KEYS="
            + ",".join(collection_keys)
        )
    return ZoteroLibrary(version=library.version, items=filtered, from_cache=library.from_cache)


__all__ = ["ZoteroItem", "ZoteroLibrary", "sync_zotero_library"]
