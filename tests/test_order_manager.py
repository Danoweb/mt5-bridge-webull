import pytest

from bridge.models import OrderRequest, OrderSide, OrderStatus, OrderType
from bridge.order_manager import OrderManager
from tests.fakes import FakeBrokerClient


def make_order(**overrides):
    defaults = dict(symbol="AAPL", side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET)
    defaults.update(overrides)
    return OrderRequest(**defaults)


def test_live_order_reaches_broker():
    broker = FakeBrokerClient()
    manager = OrderManager(broker=broker, dry_run=False)

    response = manager.place_order(make_order())

    assert len(broker.placed_orders) == 1
    assert response.status == OrderStatus.FILLED


def test_dry_run_never_calls_broker():
    broker = FakeBrokerClient()
    manager = OrderManager(broker=broker, dry_run=True)

    response = manager.place_order(make_order())

    assert len(broker.placed_orders) == 0
    assert response.dry_run is True
    # Dry-run still returns a plausible-looking SUBMITTED response so the
    # MT5 EA's response handling code path is exercised identically to live mode.
    assert response.status == OrderStatus.SUBMITTED


def test_duplicate_client_order_id_is_not_resubmitted():
    broker = FakeBrokerClient()
    manager = OrderManager(broker=broker, dry_run=False)
    order = make_order(client_order_id="mt5-ticket-42")

    first = manager.place_order(order)
    second = manager.place_order(order)

    assert len(broker.placed_orders) == 1, "the second call must not reach the broker"
    assert first.order_id == second.order_id


def test_orders_without_client_order_id_are_never_deduped():
    # Some callers may not supply a client_order_id; those must always be
    # submitted (we can't detect duplicates without an idempotency key).
    broker = FakeBrokerClient()
    manager = OrderManager(broker=broker, dry_run=False)

    manager.place_order(make_order())
    manager.place_order(make_order())

    assert len(broker.placed_orders) == 2


def test_close_position_with_no_open_position_returns_none():
    broker = FakeBrokerClient()
    manager = OrderManager(broker=broker, dry_run=False)

    assert manager.close_position("AAPL") is None
    assert len(broker.placed_orders) == 0


def test_close_position_sends_opposite_side_market_order():
    broker = FakeBrokerClient()
    manager = OrderManager(broker=broker, dry_run=False)
    manager.place_order(make_order(side=OrderSide.BUY, quantity=10))

    response = manager.close_position("AAPL")

    assert response is not None
    closing_order = broker.placed_orders[-1]
    assert closing_order.side == OrderSide.SELL
    assert closing_order.quantity == 10
    assert closing_order.order_type == OrderType.MARKET


def test_close_position_for_short_sends_buy_order():
    broker = FakeBrokerClient()
    manager = OrderManager(broker=broker, dry_run=False)
    manager.place_order(make_order(side=OrderSide.SELL, quantity=5))

    response = manager.close_position("AAPL")

    closing_order = broker.placed_orders[-1]
    assert closing_order.side == OrderSide.BUY
    assert closing_order.quantity == 5
