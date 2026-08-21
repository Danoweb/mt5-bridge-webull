#!/usr/bin/env python3
"""
One-time (or once-expired) Webull OpenAPI session bootstrap.

Unlike a typical username/password/MFA-code login, the official Webull
OpenAPI SDK authenticates purely via app key/secret and verifies the
session by pushing an approval request to your Webull mobile app --
there's no code to type in. Simply constructing the SDK's trade client
(which WebullBrokerClient does) triggers that flow and blocks for up to
~5 minutes waiting for you to approve it.

Run this once per mode, in the foreground (`docker compose run --rm
bridge-login`), so that wait happens somewhere you can see the "approve on
your phone" prompt -- not silently inside the long-running service's
startup. Once approved, the SDK saves its own verified session token
under that mode's token directory, and future restarts of the main
service reuse it without another approval, unless it later expires or is
revoked.

Bootstraps whichever mode is currently selected via WEBULL_MODE in .env.
Paper and live have entirely separate sessions, so to have both ready to
go, run this twice -- once per mode, e.g.:

    docker compose run --rm -e WEBULL_MODE=paper bridge-login
    docker compose run --rm -e WEBULL_MODE=live bridge-login
"""
import logging
import sys
from pathlib import Path

# Allow running this script directly (`python scripts/webull_login.py`)
# without having installed the `bridge` package -- add ../src to the import
# path, mirroring how the Docker image lays things out.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bridge.broker.webull_client import WebullBrokerClient  # noqa: E402
from bridge.config import get_settings  # noqa: E402
from bridge.logging_config import configure_logging  # noqa: E402


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    logger = logging.getLogger(__name__)

    credentials = settings.active_webull_credentials()

    if not credentials.app_key or not credentials.app_secret:
        env_prefix = "WEBULL_LIVE" if settings.webull_mode == "live" else "WEBULL_PAPER"
        logger.error(
            "%s_APP_KEY and %s_APP_SECRET must be set in .env before running this "
            "for mode=%s. Generate them at https://www.webull.com/center#openApiManagement "
            "(see README).",
            env_prefix,
            env_prefix,
            settings.webull_mode,
        )
        return 1

    logger.info(
        "Connecting to Webull OpenAPI (mode=%s). If this mode's session isn't already "
        "verified, open the Webull app on your phone now -- a login approval request "
        "should appear within a few seconds. You have about 5 minutes to approve it.",
        settings.webull_mode,
    )

    client = WebullBrokerClient(
        app_key=credentials.app_key,
        app_secret=credentials.app_secret,
        region_id=settings.webull_region_id,
        token_dir=credentials.token_dir,
        account_id=credentials.account_id,
        api_endpoint=credentials.api_endpoint,
        instrument_type=settings.webull_instrument_type,
    )

    if not client.is_connected():
        logger.error(
            "Could not establish a Webull session for mode=%s (see errors above). "
            "Common causes: approval request timed out or was not approved, or the "
            "app key/secret/region for this mode are wrong.",
            settings.webull_mode,
        )
        return 1

    logger.info(
        "Webull session established for mode=%s (account_id=%s). Token saved under %s -- "
        "you can now start the bridge normally (e.g. `docker compose up -d`).",
        settings.webull_mode,
        client.account_id,
        credentials.token_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
