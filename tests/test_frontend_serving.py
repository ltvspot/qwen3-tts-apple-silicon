"""Tests for frontend static file serving."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.config import settings


def _frontend_build_exists() -> bool:
    """Return whether the configured frontend build is available on disk."""

    frontend_build = Path(settings.FRONTEND_BUILD_DIR)
    return frontend_build.exists() and (frontend_build / "index.html").exists()


def test_health_endpoint_still_works(client: TestClient) -> None:
    """API health check must not be intercepted by the SPA catch-all."""

    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_api_routes_not_intercepted(client: TestClient) -> None:
    """API routes must still return JSON, not index.html."""

    response = client.get("/api/library")

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("application/json")


def test_root_serves_frontend_or_404(client: TestClient) -> None:
    """Root path should serve index.html if frontend build exists."""

    response = client.get("/")

    expected_status = 200 if _frontend_build_exists() else 404
    assert response.status_code == expected_status
    if expected_status == 200:
        assert "html" in response.headers.get("content-type", "")


def test_unknown_path_serves_spa_or_404(client: TestClient) -> None:
    """Unknown non-API paths should serve index.html (SPA) or 404."""

    response = client.get("/library")

    expected_status = 200 if _frontend_build_exists() else 404
    assert response.status_code == expected_status
    if expected_status == 200:
        content_type = response.headers.get("content-type", "")
        assert "html" in content_type or "octet-stream" in content_type


def test_static_assets_served_when_frontend_available(client: TestClient) -> None:
    """Built frontend assets should be served without affecting API routes."""

    response = client.get("/asset-manifest.json")

    expected_status = 200 if _frontend_build_exists() else 404
    assert response.status_code == expected_status
    if expected_status == 200:
        manifest = response.json()
        asset_response = client.get(manifest["files"]["main.js"])
        assert asset_response.status_code == 200
        assert "javascript" in asset_response.headers.get("content-type", "")
