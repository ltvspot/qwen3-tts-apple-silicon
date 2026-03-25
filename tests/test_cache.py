"""Tests for API response caching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from src.api.cache import TTLCache, invalidate_library_cache
from src.database import Book, BookStatus


def create_book(test_db: Session, *, title: str) -> Book:
    """Create and persist a simple library book."""

    book = Book(
        title=title,
        author="Cache Author",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.NOT_STARTED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def test_ttl_cache_expires_entries_after_their_deadline() -> None:
    """Expired TTL cache entries should be evicted on read."""

    cache = TTLCache(ttl_seconds=1)
    cache.set("library:all:100:0", {"total": 1})
    value, _ = cache._cache["library:all:100:0"]
    cache._cache["library:all:100:0"] = (
        value,
        datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    assert cache.get("library:all:100:0") is None


def test_library_endpoint_uses_cache_until_it_is_invalidated(client, test_db: Session) -> None:
    """Library responses should stay cached until an explicit invalidation occurs."""

    invalidate_library_cache()
    create_book(test_db, title="Cached Book One")

    first_response = client.get("/api/library")
    assert first_response.status_code == 200
    assert first_response.json()["total"] == 1

    create_book(test_db, title="Cached Book Two")

    cached_response = client.get("/api/library")
    assert cached_response.status_code == 200
    assert cached_response.json()["total"] == 1

    invalidate_library_cache()

    refreshed_response = client.get("/api/library")
    assert refreshed_response.status_code == 200
    assert refreshed_response.json()["total"] == 2
