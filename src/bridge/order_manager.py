"""
Order manager: the business-logic layer between the HTTP API and the broker.

This is where decisions get made that are specific to *this bridge*, as
opposed to generic Webull plumbing (broker/webull_client.py) or generic
HTTP concerns (api.py). Keeping it as its own module makes it easy to unit
test the decision logic (dry-run short-circuiting, idempotency, rejection
of nonsensical requests) with a fake broker, independent of both FastAPI
and Webull.
"""
import logging
import threading
from typing import Dict, List, Optional

from bridge.broker.base import BrokerClient
from bridge.models import (
    AccountInfo,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, broker: BrokerClient, dry_run: bool = True):
        self._broker = broker
        self._dry_run = dry_run

        # In-memory idempotency cache: client_order_id -> the OrderResponse
        # we returned the first time we saw it. MT5's WebRequest() is a
        # blocking synchronous call from inside the EA; if it times out
        # (slow network to a home-hosted bridge, a cold Webull API call,
        # etc.) a naively-written EA may retry the same signal. Without
        # this cache a transient timeout could cause the *same* trade to be
        # submitted twice. This is intentionally a simple in-process dict
        # (not persisted) -- a restart clears it, which is an acceptable
        # tradeoff because the MT5 EA is expected to send a fresh
        # client_order_id per distinct trade decision, not replay old ones
        # across restarts.
        self._orders_by_client_id: Dict[str, OrderResponse] = {}
        self._lock = threading.Lock()

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def is_broker_connected(self) -> bool:
        return self._broker.is_connected()

    def get_account(self) -> AccountInfo:
        return self._broker.get_account()

    def get_positions(self) -> List[Position]:
        return self._broker.get_positions()

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._broker.get_position(symbol)

    def place_order(self, order: OrderRequest) -> OrderResponse:
        if order.client_order_id:
            with self._lock:
                cached = self._orders_by_client_id.get(order.client_order_id)
            if cached is not None:
                logger.info(
                    "Duplicate client_order_id=%s received; returning cached result "
                    "instead of re-submitting to the broker.",
                    order.client_order_id,
                )
                return cached

        if self._dry_run:
            # Dry-run exists so a user can point their live MT5 chart at the
            # bridge and confirm the *wiring* (auth, symbol mapping, request
            # shape) is correct before any real order reaches Webull. We
            # still go through every step above (idempotency check) so
            # dry-run behaves identically to live mode except for the final
            # broker call.
            logger.info(
                "[DRY RUN] Would place order: %s %s x%s (%s)",
                order.side.value,
                order.symbol,
                order.quantity,
                order.order_type.value,
            )
            response = OrderResponse(
                order_id=f"dryrun-{order.client_order_id or id(order)}",
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                status=OrderStatus.SUBMITTED,
                dry_run=True,
            )
        else:
            response = self._broker.place_order(order)

        if order.client_order_id:
            with self._lock:
                self._orders_by_client_id[order.client_order_id] = response

        return response

    def get_order(self, order_id: str) -> Optional[OrderResponse]:
        return self._broker.get_order(order_id)

    def cancel_order(self, order_id: str) -> bool:
        if self._dry_run:
            logger.info("[DRY RUN] Would cancel order %s", order_id)
            return True
        return self._broker.cancel_order(order_id)

    def close_position(self, symbol: str) -> Optional[OrderResponse]:
        """
        Close an entire open position with a market order in the opposite
        direction. This is what the MT5 EA calls when the strategy closes
        its MT5-side position, so the Webull-side mirror gets flattened too.
        """
        position = self._broker.get_position(symbol)
        if position is None or position.quantity == 0:
            logger.info("close_position(%s) called but no open position found; nothing to do.", symbol)
            return None

        closing_side = OrderSide.SELL if position.quantity > 0 else OrderSide.BUY
        close_order = OrderRequest(
            symbol=symbol,
            side=closing_side,
            quantity=abs(position.quantity),
            order_type=OrderType.MARKET,
        )
        logger.info("Closing position: %s %s x%s", closing_side.value, symbol, abs(position.quantity))
        return self.place_order(close_order)
