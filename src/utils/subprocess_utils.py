"""Safe subprocess helpers for long-running media operations."""

from __future__ import annotations

import subprocess

FFMPEG_TIMEOUT_SECONDS = 120


def _stderr_text(error: subprocess.CalledProcessError) -> str:
    """Return a readable stderr payload regardless of subprocess text mode."""

    stderr = error.stderr or ""
    if isinstance(stderr, bytes):
        return stderr.decode(errors="replace")
    return str(stderr)


def run_ffmpeg(
    args: list[str],
    timeout: int = FFMPEG_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with a timeout so corrupt input cannot hang the export pipeline."""

    try:
        return subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg timed out after {timeout}s. Command: {' '.join(args[:5])}... "
            "This usually means corrupt audio input."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffmpeg failed with code {exc.returncode}: {_stderr_text(exc)[:500]}"
        ) from exc
