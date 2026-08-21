import pytest
from pydantic import ValidationError

from bridge.models import OrderRequest, OrderSide, OrderType


def test_market_order_does_not_require_prices():
    order = OrderRequest(symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET)
    assert order.limit_price is None


def test_limit_order_requires_limit_price():
    with pytest.raises(ValidationError):
        OrderRequest(symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.LIMIT)


def test_limit_order_with_price_is_valid():
    order = OrderRequest(symbol="AAPL", side=OrderSide.BUY, quantity=1, order_type=OrderType.LIMIT, limit_price=150.0)
    assert order.limit_price == 150.0


def test_stop_order_requires_stop_price():
    with pytest.raises(ValidationError):
        OrderRequest(symbol="AAPL", side=OrderSide.SELL, quantity=1, order_type=OrderType.STOP)


def test_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        OrderRequest(symbol="AAPL", side=OrderSide.BUY, quantity=0, order_type=OrderType.MARKET)

    with pytest.raises(ValidationError):
        OrderRequest(symbol="AAPL", side=OrderSide.BUY, quantity=-5, order_type=OrderType.MARKET)
