"""
Tests for the Webull OpenAPI glue code, with the SDK's ApiClient/TradeClient
replaced by mocks. These verify *our* translation logic -- order payload
construction, defensive response parsing, account discovery, symbol
mapping -- not Webull's real service.
"""
from unittest.mock import MagicMock

import pytest

from bridge.broker.webull_client import WebullBrokerClient
from bridge.models import OrderRequest, OrderSide, OrderStatus, OrderType


def _mock_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = str(json_body)
    return resp


@pytest.fixture
def trade_client_factory(monkeypatch):
    """
    Patches the SDK classes used inside webull_client.py and returns the
    mock TradeClient instance that WebullBrokerClient will end up holding,
    so tests can configure its return values.
    """
    fake_trade_client = MagicMock()
    monkeypatch.setattr("bridge.broker.webull_client.WebullApiClient", lambda *a, **kw: MagicMock())
    monkeypatch.setattr("bridge.broker.webull_client.WebullTradeClient", lambda api_client: fake_trade_client)
    return fake_trade_client


def make_client(tmp_path, trade_client_factory, **overrides):
    kwargs = dict(
        app_key="key",
        app_secret="secret",
        region_id="us",
        token_dir=tmp_path / "token",
        account_id="ACC1",  # supplied explicitly so tests don't need to mock account discovery too
        symbol_map={"US500": "SPY"},
    )
    kwargs.update(overrides)
    return WebullBrokerClient(**kwargs)


def test_disconnected_without_app_credentials(tmp_path):
    client = WebullBrokerClient(app_key=None, app_secret=None, region_id="us", token_dir=tmp_path / "t")
    assert client.is_connected() is False


def test_connected_with_explicit_account_id(tmp_path, trade_client_factory):
    client = make_client(tmp_path, trade_client_factory)
    assert client.is_connected() is True
    assert client.account_id == "ACC1"


def test_account_discovery_used_when_account_id_not_configured(tmp_path, trade_client_factory):
    trade_client_factory.account_v2.get_account_list.return_value = _mock_response(
        200, {"accounts": [{"account_id": "DISCOVERED"}]}
    )
    client = make_client(tmp_path, trade_client_factory, account_id=None)
    assert client.account_id == "DISCOVERED"


def test_symbol_mapping_applies(tmp_path, trade_client_factory):
    client = make_client(tmp_path, trade_client_factory)
    assert client._resolve_symbol("US500") == "SPY"
    assert client._resolve_symbol("AAPL") == "AAPL"


def test_get_account_parses_balance_response(tmp_path, trade_client_factory):
    trade_client_factory.account_v2.get_account_balance.return_value = _mock_response(
        200, {"netLiquidation": "12345.67", "cashBalance": "500", "buyingPower": "1000"}
    )
    client = make_client(tmp_path, trade_client_factory)

    account = client.get_account()

    assert account.net_liquidation == 12345.67
    assert account.cash_balance == 500.0
    assert account.buying_power == 1000.0


def test_get_positions_parses_position_list(tmp_path, trade_client_factory):
    trade_client_factory.account_v2.get_account_position.return_value = _mock_response(
        200, {"positions": [{"symbol": "AAPL", "quantity": "10", "avg_cost": "150"}]}
    )
    client = make_client(tmp_path, trade_client_factory)

    positions = client.get_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].quantity == 10.0


def test_calling_broker_methods_without_connection_raises(tmp_path):
    client = WebullBrokerClient(app_key=None, app_secret=None, region_id="us", token_dir=tmp_path / "t")
    with pytest.raises(RuntimeError, match="Webull session is not established"):
        client.get_account()


def test_place_order_success_uses_generated_client_order_id(tmp_path, trade_client_factory):
    trade_client_factory.order_v3.place_order.return_value = _mock_response(200, {"orders": [{}]})
    client = make_client(tmp_path, trade_client_factory)

    order = OrderRequest(symbol="AAPL", side=OrderSide.BUY, quantity=2, order_type=OrderType.MARKET)
    response = client.place_order(order)

    assert response.status == OrderStatus.SUBMITTED
    assert response.order_id  # a uuid4 hex was generated since client_order_id was None

    call_args = trade_client_factory.order_v3.place_order.call_args
    account_id, orders = call_args[0]
    assert account_id == "ACC1"
    assert orders[0]["symbol"] == "AAPL"
    assert orders[0]["order_type"] == "MARKET"
    assert orders[0]["side"] == "BUY"


def test_place_order_maps_symbol_before_sending(tmp_path, trade_client_factory):
    trade_client_factory.order_v3.place_order.return_value = _mock_response(200, {"orders": [{}]})
    client = make_client(tmp_path, trade_client_factory)

    order = OrderRequest(symbol="US500", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET)
    client.place_order(order)

    _, orders = trade_client_factory.order_v3.place_order.call_args[0]
    assert orders[0]["symbol"] == "SPY"


def test_place_order_http_failure_returns_rejected(tmp_path, trade_client_factory):
    trade_client_factory.order_v3.place_order.return_value = _mock_response(400, {"msg": "bad request"})
    client = make_client(tmp_path, trade_client_factory)

    order = OrderRequest(symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET)
    response = client.place_order(order)

    assert response.status == OrderStatus.REJECTED


def test_get_order_parses_order_detail_response(tmp_path, trade_client_factory):
    trade_client_factory.order_v3.get_order_detail.return_value = _mock_response(
        200,
        {
            "orders": [
                {
                    "client_order_id": "abc123",
                    "symbol": "AAPL",
                    "side": "BUY",
                    "quantity": "5",
                    "filled_quantity": "5",
                    "avg_filled_price": "150.25",
                    "status": "FILLED",
                }
            ]
        },
    )
    client = make_client(tmp_path, trade_client_factory)

    order = client.get_order("abc123")

    assert order is not None
    assert order.status == OrderStatus.FILLED
    assert order.avg_fill_price == 150.25


def test_get_order_returns_none_when_no_orders_found(tmp_path, trade_client_factory):
    trade_client_factory.order_v3.get_order_detail.return_value = _mock_response(200, {"orders": []})
    client = make_client(tmp_path, trade_client_factory)

    assert client.get_order("does-not-exist") is None


def test_cancel_order_returns_true_on_200(tmp_path, trade_client_factory):
    trade_client_factory.order_v3.cancel_order.return_value = _mock_response(200, {})
    client = make_client(tmp_path, trade_client_factory)

    assert client.cancel_order("abc123") is True


def test_cancel_order_returns_false_on_failure(tmp_path, trade_client_factory):
    trade_client_factory.order_v3.cancel_order.return_value = _mock_response(400, {})
    client = make_client(tmp_path, trade_client_factory)

    assert client.cancel_order("abc123") is False
