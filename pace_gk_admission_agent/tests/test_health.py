"""Tests for the health endpoint."""

def test_health_returns_ok(client):
    """GET /health should report the service is up."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
