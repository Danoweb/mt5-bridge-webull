"""
Pydantic schemas shared between the API layer and the order manager.

Keeping these as plain data models (rather than passing dicts around)
means FastAPI validates every inbound request from the MT5 EA automatically
-- a malformed field (wrong type, missing required value, invalid enum)
is rejected with a 422 before any of our trading logic runs.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderRequest(BaseModel):
    """An order as sent by the MT5 EA."""

    symbol: str = Field(..., min_length=1, description="Symbol as known to MT5; mapped to a Webull ticker internally.")
    side: OrderSide
    quantity: float = Field(..., gt=0, description="Share quantity. Webull equities are whole shares; fractional support depends on the symbol.")
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = Field(default=None, gt=0)
    stop_price: Optional[float] = Field(default=None, gt=0)
    time_in_force: TimeInForce = TimeInForce.DAY

    # Supplied by the EA (e.g. the MT5 ticket number) so repeated/retried
    # requests for the *same* underlying trade can be recognized as
    # duplicates rather than executed twice. See OrderManager for how this
    # is used.
    client_order_id: Optional[str] = Field(default=None, max_length=64)

    # A model_validator (runs after all fields are populated, defaults
    # included) rather than a per-field field_validator: pydantic v2 skips
    # field_validators for a field that was left at its default (unset)
    # value unless validate_default=True is set, which would have silently
    # let `order_type=LIMIT` with no limit_price through. Checking
    # cross-field consistency here avoids that trap.
    @model_validator(mode="after")
    def _require_prices_matching_order_type(self) -> "OrderRequest":
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT/STOP_LIMIT orders")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError("stop_price is required for STOP/STOP_LIMIT orders")
        return self


class OrderResponse(BaseModel):
    order_id: str
    client_order_id: Optional[str] = None
    symbol: str
    side: OrderSide
    quantity: float
    filled_quantity: float = 0
    avg_fill_price: Optional[float] = None
    status: OrderStatus
    dry_run: bool = False
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None


class AccountInfo(BaseModel):
    account_id: str
    net_liquidation: float
    cash_balance: float
    buying_power: float


class HealthStatus(BaseModel):
    status: str
    webull_connected: bool
    dry_run: bool
    mode: str
