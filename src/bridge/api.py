"""
HTTP API exposed to the MT5 Expert Advisor.

Built as an application *factory* (create_app) rather than a module-level
`app = FastAPI()` so the test suite can construct the app around a fake
OrderManager instead of a real Webull-backed one -- see tests/conftest.py.
"""
import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from bridge.models import (
    AccountInfo,
    HealthStatus,
    OrderRequest,
    OrderResponse,
    Position,
)
from bridge.order_manager import OrderManager
from bridge.security import make_api_key_dependency

logger = logging.getLogger(__name__)


def create_app(order_manager: OrderManager, api_key: str, mode: str = "paper") -> FastAPI:
    app = FastAPI(
        title="MT5-Webull Bridge",
        description="Bridge connector letting an MT5 Expert Advisor route trades to Webull.",
        version="1.0.0",
    )
    require_api_key = make_api_key_dependency(api_key)

    # Every unhandled exception is logged with a full traceback and turned
    # into a generic 500 response. Without this, FastAPI's default behavior
    # would still return a 500 but the traceback would only go to stderr in
    # a way that's easy to miss in a containerized deployment -- and we'd
    # risk leaking internal error details (e.g. a Webull error payload) to
    # whatever is calling the API.
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal bridge error; see server logs."})

    @app.get("/health", response_model=HealthStatus)
    def health() -> HealthStatus:
        # Deliberately unauthenticated: this lets both a human and the MT5
        # EA's startup check confirm the bridge is reachable at all (e.g.
        # the WebRequest URL is whitelisted correctly) without needing the
        # API key, and it leaks no sensitive information.
        return HealthStatus(
            status="ok",
            webull_connected=order_manager.is_broker_connected(),
            dry_run=order_manager.dry_run,
            mode=mode,
        )

    @app.get("/account", response_model=AccountInfo, dependencies=[Depends(require_api_key)])
    def get_account() -> AccountInfo:
        return order_manager.get_account()

    @app.get("/positions", response_model=list[Position], dependencies=[Depends(require_api_key)])
    def get_positions() -> list[Position]:
        return order_manager.get_positions()

    @app.get("/positions/{symbol}", response_model=Position, dependencies=[Depends(require_api_key)])
    def get_position(symbol: str) -> Position:
        position = order_manager.get_position(symbol)
        if position is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No open position for {symbol}")
        return position

    @app.post(
        "/positions/{symbol}/close",
        response_model=OrderResponse,
        dependencies=[Depends(require_api_key)],
    )
    def close_position(symbol: str) -> OrderResponse:
        response = order_manager.close_position(symbol)
        if response is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No open position for {symbol}")
        return response

    @app.post(
        "/orders",
        response_model=OrderResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_api_key)],
    )
    def place_order(order: OrderRequest) -> OrderResponse:
        return order_manager.place_order(order)

    @app.get("/orders/{order_id}", response_model=OrderResponse, dependencies=[Depends(require_api_key)])
    def get_order(order_id: str) -> OrderResponse:
        order = order_manager.get_order(order_id)
        if order is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown order_id {order_id}")
        return order

    @app.delete("/orders/{order_id}", dependencies=[Depends(require_api_key)])
    def cancel_order(order_id: str) -> dict:
        cancelled = order_manager.cancel_order(order_id)
        if not cancelled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Order could not be cancelled")
        return {"order_id": order_id, "cancelled": True}

    return app
