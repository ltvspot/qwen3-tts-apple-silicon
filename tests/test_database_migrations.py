"""Tests for migration history recording."""

from __future__ import annotations

import json
from pathlib import Path

from src.database_migrations import record_migration


def test_record_migration_appends_entries_to_the_history_log(tmp_path: Path) -> None:
    """Migration records should accumulate in a JSON history file."""

    migration_log = record_migration(
        "prompt-16-polish-hardening",
        "Added logging, health checks, and resilience tooling.",
        migrations_dir=tmp_path,
    )
    record_migration(
        "prompt-16-follow-up",
        "Added cache and error-handling regression coverage.",
        migrations_dir=tmp_path,
    )

    history = json.loads(migration_log.read_text(encoding="utf-8"))

    assert migration_log == tmp_path / "migration_log.json"
    assert [entry["name"] for entry in history] == [
        "prompt-16-polish-hardening",
        "prompt-16-follow-up",
    ]
    assert all("timestamp" in entry for entry in history)
