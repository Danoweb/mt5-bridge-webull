"""
Process entrypoint. Wires together config -> logging -> broker client ->
order manager -> FastAPI app, then hands off to uvicorn.

Kept deliberately thin: anything with actual logic belongs in bridge/*, not
here, so it can be unit tested without starting a real HTTP server.
"""
import json
import logging

import uvicorn

from bridge.api import create_app
from bridge.broker.webull_client import WebullBrokerClient
from bridge.config import get_settings
from bridge.logging_config import configure_logging
from bridge.order_manager import OrderManager

logger = logging.getLogger(__name__)


def _load_symbol_map(path) -> dict:
    if path is None:
        return {}
    if not path.exists():
        # A configured-but-missing symbol map file is almost certainly a
        # deployment mistake (typo'd path, forgot to mount the volume) --
        # better to fail loudly at startup than silently trade the wrong
        # symbol later.
        raise FileNotFoundError(f"SYMBOL_MAP_FILE is set to {path} but that file does not exist.")
    return json.loads(path.read_text())


def build_app():
    settings = get_settings()
    configure_logging(settings)

    logger.info(
        "Starting MT5-Webull bridge (mode=%s, dry_run=%s)",
        settings.webull_mode,
        settings.dry_run,
    )
    if settings.webull_mode == "live":
        logger.warning(
            "WEBULL_MODE=live -- orders placed with DRY_RUN=false will use REAL MONEY. "
            "If that's not intentional, set WEBULL_MODE=paper and restart."
        )

    symbol_map = _load_symbol_map(settings.symbol_map_file)
    if symbol_map:
        logger.info("Loaded %d symbol mapping(s) from %s", len(symbol_map), settings.symbol_map_file)

    credentials = settings.active_webull_credentials()
    broker = WebullBrokerClient(
        app_key=credentials.app_key,
        app_secret=credentials.app_secret,
        region_id=settings.webull_region_id,
        token_dir=credentials.token_dir,
        account_id=credentials.account_id,
        api_endpoint=credentials.api_endpoint,
        instrument_type=settings.webull_instrument_type,
        symbol_map=symbol_map,
    )
    if not broker.is_connected():
        logger.warning(
            "Bridge is starting WITHOUT an active Webull session for mode=%s. /account, "
            "/positions and /orders will fail until scripts/webull_login.py "
            "has been run successfully for this mode and the container restarted.",
            settings.webull_mode,
        )

    order_manager = OrderManager(broker=broker, dry_run=settings.dry_run)
    if settings.dry_run:
        logger.warning(
            "DRY_RUN is enabled: orders will be logged but NOT sent to Webull. "
            "Set DRY_RUN=false once you've verified the MT5 -> bridge wiring."
        )

    return create_app(order_manager=order_manager, api_key=settings.bridge_api_key, mode=settings.webull_mode), settings


app, _settings = build_app()


if __name__ == "__main__":
    uvicorn.run(app, host=_settings.bridge_host, port=_settings.bridge_port)
