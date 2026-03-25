"""Minimal migration history recording for development schema changes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path("migrations")


def record_migration(name: str, description: str, *, migrations_dir: Path = MIGRATIONS_DIR) -> Path:
    """Append a migration entry to the local JSON history log."""

    migrations_dir.mkdir(parents=True, exist_ok=True)
    migration_log = migrations_dir / "migration_log.json"

    if migration_log.exists():
        history = json.loads(migration_log.read_text(encoding="utf-8"))
    else:
        history = []

    history.append(
        {
            "name": name,
            "description": description,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )

    migration_log.write_text(json.dumps(history, indent=2), encoding="utf-8")
    return migration_log
