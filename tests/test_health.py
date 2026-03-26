"""Health endpoint tests."""

from fastapi.testclient import TestClient


def test_health_check(client: TestClient) -> None:
    """Health endpoint returns service metadata."""

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded", "error"}
    assert payload["version"] == "0.1.0"
    assert isinstance(payload["startup"]["warnings"], list)
    assert isinstance(payload["startup"]["errors"], list)
    assert len(payload["startup"]["checks"]) == 7
    assert {"name", "status", "detail", "critical"} <= set(payload["startup"]["checks"][0])
    assert {"total_gb", "free_gb", "percent_used"} <= set(payload["disk"])
