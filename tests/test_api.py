from bridge.models import OrderStatus


def test_health_reports_dry_run_and_connection_state(client):
    resp = client.get("/health")
    body = resp.json()
    assert body["dry_run"] is False  # conftest's order_manager fixture uses dry_run=False
    assert body["webull_connected"] is True
    assert body["mode"] == "paper"  # conftest's app fixture uses mode="paper"


def test_get_account(client, auth_headers):
    resp = client.get("/account", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["account_id"] == "TEST123"


def test_get_positions_empty(client, auth_headers):
    resp = client.get("/positions", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_position_404_when_none_open(client, auth_headers):
    resp = client.get("/positions/AAPL", headers=auth_headers)
    assert resp.status_code == 404


def test_place_order_then_fetch_it(client, auth_headers):
    order_body = {
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 5,
        "order_type": "MARKET",
    }
    place_resp = client.post("/orders", json=order_body, headers=auth_headers)
    assert place_resp.status_code == 201
    order = place_resp.json()
    assert order["status"] == OrderStatus.FILLED.value

    fetch_resp = client.get(f"/orders/{order['order_id']}", headers=auth_headers)
    assert fetch_resp.status_code == 200
    assert fetch_resp.json()["symbol"] == "AAPL"


def test_place_order_rejects_invalid_payload(client, auth_headers):
    # Missing required `side` field -> FastAPI/pydantic validation error,
    # not a 500 -- the bridge should never crash on a malformed EA request.
    resp = client.post("/orders", json={"symbol": "AAPL", "quantity": 1}, headers=auth_headers)
    assert resp.status_code == 422


def test_get_unknown_order_404(client, auth_headers):
    resp = client.get("/orders/does-not-exist", headers=auth_headers)
    assert resp.status_code == 404


def test_close_position_end_to_end(client, auth_headers):
    client.post(
        "/orders",
        json={"symbol": "MSFT", "side": "BUY", "quantity": 3, "order_type": "MARKET"},
        headers=auth_headers,
    )

    close_resp = client.post("/positions/MSFT/close", headers=auth_headers)
    assert close_resp.status_code == 200
    assert close_resp.json()["side"] == "SELL"

    # Position should now be flat.
    position_resp = client.get("/positions/MSFT", headers=auth_headers)
    assert position_resp.status_code == 404


def test_close_position_404_when_none_open(client, auth_headers):
    resp = client.post("/positions/NOPE/close", headers=auth_headers)
    assert resp.status_code == 404


def test_cancel_order(client, auth_headers):
    place_resp = client.post(
        "/orders",
        json={"symbol": "AAPL", "side": "BUY", "quantity": 1, "order_type": "MARKET"},
        headers=auth_headers,
    )
    order_id = place_resp.json()["order_id"]

    cancel_resp = client.delete(f"/orders/{order_id}", headers=auth_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["cancelled"] is True


def test_cancel_unknown_order_returns_400(client, auth_headers):
    resp = client.delete("/orders/does-not-exist", headers=auth_headers)
    assert resp.status_code == 400
