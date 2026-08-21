"""
A fake BrokerClient used throughout the test suite instead of the real
Webull-backed one. This keeps tests fast, deterministic, and independent of
network access or real Webull credentials -- they exercise *our* logic
(order manager, API routes, auth), not the third-party broker's behaviour.
"""
from typing import Dict, List, Optional

from bridge.broker.base import BrokerClient
from bridge.models import AccountInfo, OrderRequest, OrderResponse, OrderStatus, Position


class FakeBrokerClient(BrokerClient):
    def __init__(self, connected: bool = True):
        self.connected = connected
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, OrderResponse] = {}
        self.placed_orders: List[OrderRequest] = []  # inspection point for assertions
        self._next_order_id = 1
        self.account = AccountInfo(
            account_id="TEST123",
            net_liquidation=10000.0,
            cash_balance=5000.0,
            buying_power=10000.0,
        )

    def is_connected(self) -> bool:
        return self.connected

    def get_account(self) -> AccountInfo:
        return self.account

    def get_positions(self) -> List[Position]:
        return list(self.positions.values())

    def get_position(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def place_order(self, order: OrderRequest) -> OrderResponse:
        self.placed_orders.append(order)
        order_id = str(self._next_order_id)
        self._next_order_id += 1

        response = OrderResponse(
            order_id=order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=order.quantity,
            avg_fill_price=100.0,
            status=OrderStatus.FILLED,
        )
        self.orders[order_id] = response

        # Keep a simplistic position book updated so close_position() tests
        # have something real to close.
        existing = self.positions.get(order.symbol)
        signed_qty = order.quantity if order.side.value == "BUY" else -order.quantity
        if existing is None:
            if signed_qty != 0:
                self.positions[order.symbol] = Position(symbol=order.symbol, quantity=signed_qty, avg_cost=100.0)
        else:
            new_qty = existing.quantity + signed_qty
            if new_qty == 0:
                del self.positions[order.symbol]
            else:
                self.positions[order.symbol] = Position(symbol=order.symbol, quantity=new_qty, avg_cost=100.0)

        return response

    def get_order(self, order_id: str) -> Optional[OrderResponse]:
        return self.orders.get(order_id)

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False
