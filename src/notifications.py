"""Best-effort desktop notifications for local operator workflows."""

from __future__ import annotations

import platform
import subprocess
import time

_LAST_SENT_AT: dict[str, float] = {}


def _escape_osascript(value: str) -> str:
    """Return a string that is safe to interpolate into AppleScript."""

    return value.replace("\\", "\\\\").replace('"', '\\"')


def send_macos_notification(title: str, message: str, sound: str = "default") -> bool:
    """Send a native macOS notification when running on Darwin."""

    if platform.system() != "Darwin":
        return False

    script = (
        f'display notification "{_escape_osascript(message)}" '
        f'with title "{_escape_osascript(title)}"'
    )
    if sound and sound != "default":
        script += f' sound name "{_escape_osascript(sound)}"'

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            timeout=5,
            capture_output=True,
            check=False,
        )
    except Exception:
        return False

    return result.returncode == 0


def send_rate_limited_notification(
    key: str,
    *,
    title: str,
    message: str,
    cooldown_seconds: float = 300.0,
    sound: str = "default",
) -> bool:
    """Send a notification unless the same key fired within the cooldown window."""

    now = time.monotonic()
    last_sent_at = _LAST_SENT_AT.get(key)
    if last_sent_at is not None and (now - last_sent_at) < cooldown_seconds:
        return False

    delivered = send_macos_notification(title, message, sound=sound)
    if delivered:
        _LAST_SENT_AT[key] = now
    return delivered


def send_disk_warning_notification(*, free_gb: float, percent_used: float, critical: bool) -> bool:
    """Send a rate-limited notification for low disk capacity."""

    level = "Critical" if critical else "Warning"
    return send_rate_limited_notification(
        "disk-space-warning",
        title="Disk Warning",
        message=f"{level}: {percent_used:.1f}% used, {free_gb:.1f} GB free.",
        cooldown_seconds=900.0,
    )


def send_batch_started_notification(total_books: int) -> bool:
    """Notify the operator that a catalog batch started."""

    noun = "book" if total_books == 1 else "books"
    return send_macos_notification(
        "Audiobook Narrator",
        f"Batch started for {total_books} {noun}.",
    )


def send_batch_complete_notification(
    *,
    completed_books: int,
    total_books: int,
    failed_books: int = 0,
    skipped_books: int = 0,
) -> bool:
    """Notify the operator that a batch finished."""

    message = f"Batch complete: {completed_books}/{total_books} books completed"
    extras: list[str] = []
    if failed_books:
        extras.append(f"{failed_books} failed")
    if skipped_books:
        extras.append(f"{skipped_books} skipped")
    if extras:
        message += f" ({', '.join(extras)})"

    return send_macos_notification("Audiobook Narrator", message)


def send_batch_error_notification(message: str) -> bool:
    """Notify the operator when a batch cannot start or crashes."""

    return send_macos_notification("Batch Error", message)


def send_qa_failure_notification(*, book_id: int, chapter_number: int, reason: str) -> bool:
    """Notify the operator that automatic QA failed for a chapter."""

    return send_macos_notification(
        "QA Alert",
        f"Book {book_id}, chapter {chapter_number} failed QA: {reason}",
    )


def send_book_complete_notification(
    *,
    book_title: str,
    ready_for_export: bool,
    flagged_chapters: int = 0,
) -> bool:
    """Notify the operator that a book finished generation."""

    if ready_for_export:
        message = f"'{book_title}' is ready for export."
    else:
        message = (
            f"'{book_title}' finished generation with {flagged_chapters} "
            f"chapter{'s' if flagged_chapters != 1 else ''} needing QA review."
        )

    return send_macos_notification("Book Complete", message)
