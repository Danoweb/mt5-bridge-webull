"""
Webull broker client, built on Webull's **official** OpenAPI Python SDK
(`webull-openapi-python-sdk` on PyPI, source at
github.com/webull-inc/webull-openapi-python-sdk).

This is Webull-sanctioned (not a reverse-engineered private API), but it is
still a fairly young/sparsely-documented SDK, so a few notes for future
maintainers:

- Authentication is app key/secret (from Webull's OpenAPI developer
  portal), not account email/password. There is no code-entry MFA step --
  the *first* time (or any time a stored token has expired/been revoked),
  simply constructing `TradeClient(api_client)` blocks for up to ~5
  minutes while Webull polls for you to approve a login request pushed to
  your Webull mobile app. See scripts/webull_login.py, which exists solely
  to do this once, in the foreground, so that wait doesn't happen the
  first time the long-running service starts.
- The SDK persists its own verified session token to a local file (see
  `token_dir` below) and reuses it on subsequent runs, so this blocking
  wait is a one-time-per-approval cost, not a per-restart one.
- The exact JSON field names Webull returns for account balance/positions
  are not fully documented publicly at the time of writing. Parsing below
  is deliberately defensive (tries several plausible key names, logs the
  raw payload at DEBUG, and never lets an unrecognized field crash a
  request) -- if you see 0/empty values where you expect real data, run
  with LOG_LEVEL=DEBUG and compare the logged raw response against what's
  parsed here.
"""
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from webull.core.client import ApiClient as WebullApiClient
from webull.trade.trade_client import TradeClient as WebullTradeClient

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

# Our internal order types -> Webull's order_type strings. STOP/STOP_LIMIT
# map to Webull's STOP_LOSS/STOP_LOSS_LIMIT names; the official SDK's own
# samples only demonstrate these for non-US markets, so treat STOP orders
# on US equities as unverified until you've confirmed it works against
# your account (start with the sandbox endpoint -- see README).
_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP: "STOP_LOSS",
    OrderType.STOP_LIMIT: "STOP_LOSS_LIMIT",
}

# Observed Webull order status strings -> our normalized OrderStatus.
# Unrecognized statuses fall back to SUBMITTED rather than raising, so a
# status string we haven't seen before doesn't crash a poll -- it's logged
# instead (see _parse_order) so it can be added here once confirmed.
_STATUS_MAP = {
    "PENDING": OrderStatus.PENDING,
    "WORKING": OrderStatus.SUBMITTED,
    "SUBMITTED": OrderStatus.SUBMITTED,
    "FILLED": OrderStatus.FILLED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "FAILED": OrderStatus.REJECTED,
}


def _first_present(d: dict, *keys: str):
    """Returns the value of the first key present in `d`, or None. Used
    throughout this file because Webull's exact response field naming
    isn't fully confirmed -- see module docstring."""
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


class WebullBrokerClient(BrokerClient):
    def __init__(
        self,
        app_key: Optional[str],
        app_secret: Optional[str],
        region_id: str,
        token_dir: Path,
        account_id: Optional[str] = None,
        api_endpoint: Optional[str] = None,
        instrument_type: str = "EQUITY",
        symbol_map: Optional[dict] = None,
    ):
        self._region_id = region_id
        self._market = region_id.upper()
        self._instrument_type = instrument_type
        # MT5 symbol -> Webull ticker, e.g. {"US500": "SPY"}. Defaults to
        # identity when unmapped -- correct for plain US equity tickers,
        # usually wrong for indices/CFD/forex symbols. See README.
        self._symbol_map = symbol_map or {}
        self._account_id = account_id
        self._trade_client: Optional[WebullTradeClient] = None

        if not app_key or not app_secret:
            logger.warning(
                "WEBULL_APP_KEY/WEBULL_APP_SECRET not configured; broker starts "
                "disconnected. Set them (see README) to enable live trading."
            )
            return

        api_client = WebullApiClient(app_key, app_secret, region_id)
        api_client.set_token_dir(str(token_dir))
        if api_endpoint:
            api_client.add_endpoint(region_id, api_endpoint)

        try:
            # Constructing TradeClient is what actually performs
            # authentication -- see module docstring. It can block for
            # minutes on an unapproved/expired token, which is expected
            # and fine when run from scripts/webull_login.py, but a
            # deployment mistake if it happens inside the long-running
            # service (you'd see the whole bridge hang at startup).
            self._trade_client = WebullTradeClient(api_client)
        except Exception:
            logger.exception(
                "Failed to establish a Webull session. Run scripts/webull_login.py "
                "(see README) to complete the mobile-app approval step, then restart."
            )
            return

        if not self._account_id:
            self._account_id = self._discover_account_id()

    # -- connection / account discovery --------------------------------------

    @property
    def account_id(self) -> Optional[str]:
        return self._account_id

    def is_connected(self) -> bool:
        return self._trade_client is not None and self._account_id is not None

    def _ensure_ready(self) -> None:
        if not self.is_connected():
            raise RuntimeError(
                "Webull session is not established. Run scripts/webull_login.py "
                "and check WEBULL_APP_KEY/WEBULL_APP_SECRET/WEBULL_ACCOUNT_ID."
            )

    def _discover_account_id(self) -> Optional[str]:
        res = self._trade_client.account_v2.get_account_list()
        if res.status_code != 200:
            logger.error("Failed to list Webull accounts (HTTP %s): %s", res.status_code, res.text)
            return None

        payload = res.json()
        accounts = payload.get("accounts") if isinstance(payload, dict) else payload
        if not accounts:
            logger.error("Webull returned no accounts for this app key. Raw response: %s", payload)
            return None

        if len(accounts) > 1:
            logger.warning(
                "Multiple Webull accounts found; defaulting to the first one. Set "
                "WEBULL_ACCOUNT_ID explicitly to choose a different one. Accounts: %s",
                accounts,
            )

        first = accounts[0]
        account_id = _first_present(first, "account_id", "accountId", "id") if isinstance(first, dict) else first
        if not account_id:
            logger.error("Could not find an account id field in Webull's response: %s", first)
        return account_id

    def _resolve_symbol(self, mt5_symbol: str) -> str:
        webull_symbol = self._symbol_map.get(mt5_symbol, mt5_symbol)
        if webull_symbol != mt5_symbol:
            logger.debug("Mapped MT5 symbol %s -> Webull symbol %s", mt5_symbol, webull_symbol)
        return webull_symbol

    # -- BrokerClient implementation -----------------------------------------

    def get_account(self) -> AccountInfo:
        self._ensure_ready()
        res = self._trade_client.account_v2.get_account_balance(self._account_id)
        if res.status_code != 200:
            raise RuntimeError(f"Webull get_account_balance failed (HTTP {res.status_code}): {res.text}")

        data = res.json() or {}
        logger.debug("Webull account balance raw response: %s", data)

        return AccountInfo(
            account_id=str(self._account_id),
            net_liquidation=float(_first_present(data, "net_liquidation", "netLiquidation", "total_asset") or 0),
            cash_balance=float(_first_present(data, "cash_balance", "cashBalance", "cash") or 0),
            buying_power=float(_first_present(data, "buying_power", "buyingPower", "day_buying_power") or 0),
        )

    def get_positions(self) -> List[Position]:
        self._ensure_ready()
        res = self._trade_client.account_v2.get_account_position(self._account_id)
        if res.status_code != 200:
            raise RuntimeError(f"Webull get_account_position failed (HTTP {res.status_code}): {res.text}")

        data = res.json() or {}
        logger.debug("Webull positions raw response: %s", data)
        raw_positions = data.get("positions") if isinstance(data, dict) else data
        positions = []
        for p in raw_positions or []:
            symbol = _first_present(p, "symbol", "ticker_symbol") or "UNKNOWN"
            quantity = _first_present(p, "quantity", "position", "qty")
            avg_cost = _first_present(p, "avg_cost", "costPrice", "cost_price")
            if quantity is None:
                logger.warning("Skipping Webull position with no recognizable quantity field: %s", p)
                continue
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=float(quantity),
                    avg_cost=float(avg_cost or 0),
                    market_value=float(_first_present(p, "market_value", "marketValue") or 0) or None,
                    unrealized_pnl=float(_first_present(p, "unrealized_pnl", "unrealizedProfitLoss") or 0) or None,
                )
            )
        return positions

    def get_position(self, symbol: str) -> Optional[Position]:
        webull_symbol = self._resolve_symbol(symbol)
        for position in self.get_positions():
            if position.symbol == webull_symbol:
                return position
        return None

    def _build_order_payload(self, order: OrderRequest, client_order_id: str) -> dict:
        payload = {
            "combo_type": "NORMAL",
            "client_order_id": client_order_id,
            "symbol": self._resolve_symbol(order.symbol),
            "instrument_type": self._instrument_type,
            "market": self._market,
            "order_type": _ORDER_TYPE_MAP[order.order_type],
            "quantity": str(order.quantity),
            # CORE = regular trading session only; pre/post-market is not
            # exposed as a bridge option in v1 -- see README limitations.
            "support_trading_session": "CORE",
            "side": order.side.value,
            "time_in_force": order.time_in_force.value,
            "entrust_type": "QTY",
        }
        if order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        if order.stop_price is not None:
            payload["stop_price"] = str(order.stop_price)
        return payload

    def place_order(self, order: OrderRequest) -> OrderResponse:
        self._ensure_ready()
        # We always generate our own client_order_id (even if the EA didn't
        # supply one) because the official API's cancel/detail endpoints
        # are keyed on it -- it doubles as our OrderResponse.order_id, so
        # get_order()/cancel_order() below can round-trip through it
        # without needing a separate Webull-assigned order id.
        client_order_id = order.client_order_id or uuid.uuid4().hex
        payload = self._build_order_payload(order, client_order_id)

        logger.info("Placing Webull order: %s", payload)
        res = self._trade_client.order_v3.place_order(self._account_id, [payload])

        if res.status_code != 200:
            logger.error("Webull rejected order (HTTP %s): %s", res.status_code, getattr(res, "text", ""))
            return OrderResponse(
                order_id=client_order_id,
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                status=OrderStatus.REJECTED,
            )

        logger.debug("Webull place_order raw response: %s", res.json())
        return OrderResponse(
            order_id=client_order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            status=OrderStatus.SUBMITTED,
        )

    def _parse_order(self, raw: dict) -> OrderResponse:
        status_str = str(_first_present(raw, "status", "order_status") or "").upper()
        status = _STATUS_MAP.get(status_str)
        if status is None:
            logger.warning("Unrecognized Webull order status %r; treating as SUBMITTED. Raw: %s", status_str, raw)
            status = OrderStatus.SUBMITTED

        avg_price = _first_present(raw, "avg_filled_price", "avgFilledPrice")
        return OrderResponse(
            order_id=str(_first_present(raw, "client_order_id", "clientOrderId") or ""),
            symbol=_first_present(raw, "symbol") or "UNKNOWN",
            side=OrderSide(_first_present(raw, "side") or "BUY"),
            quantity=float(_first_present(raw, "quantity", "total_quantity") or 0),
            filled_quantity=float(_first_present(raw, "filled_quantity", "filledQuantity") or 0),
            avg_fill_price=float(avg_price) if avg_price else None,
            status=status,
        )

    def get_order(self, order_id: str) -> Optional[OrderResponse]:
        self._ensure_ready()
        res = self._trade_client.order_v3.get_order_detail(self._account_id, order_id)
        if res.status_code != 200:
            return None

        data = res.json() or {}
        logger.debug("Webull get_order_detail raw response: %s", data)
        # Confirmed shape (see SDK samples): {"orders": [{...}]}
        orders = data.get("orders") or []
        if not orders:
            return None
        return self._parse_order(orders[0])

    def cancel_order(self, order_id: str) -> bool:
        self._ensure_ready()
        logger.info("Cancelling Webull order %s", order_id)
        res = self._trade_client.order_v3.cancel_order(self._account_id, order_id)
        return res.status_code == 200
