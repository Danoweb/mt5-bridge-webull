"""API-key auth must gate every endpoint except /health."""


def test_health_does_not_require_api_key(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_protected_endpoint_rejects_missing_key(client):
    resp = client.get("/account")
    assert resp.status_code == 401


def test_protected_endpoint_rejects_wrong_key(client):
    resp = client.get("/account", headers={"X-API-Key": "wrong-key"})
    assert resp.status_code == 401


def test_protected_endpoint_accepts_correct_key(client, auth_headers):
    resp = client.get("/account", headers=auth_headers)
    assert resp.status_code == 200
