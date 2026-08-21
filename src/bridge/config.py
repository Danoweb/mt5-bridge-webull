"""
Centralized configuration for the bridge service.

Everything here is sourced from environment variables (see .env.example)
so the same Docker image can be reconfigured per-deployment without a
rebuild. We use pydantic-settings so misconfiguration (missing required
value, wrong type) fails loudly at startup instead of causing a confusing
error later when a trade is being placed.
"""
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WebullCredentials(NamedTuple):
    """The resolved app key/secret/account/endpoint/token-dir for whichever
    mode (paper or live) is currently active. See Settings.active_webull_credentials."""

    app_key: Optional[str]
    app_secret: Optional[str]
    account_id: Optional[str]
    api_endpoint: Optional[str]
    token_dir: Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Bridge HTTP server -------------------------------------------------
    bridge_host: str = Field(default="0.0.0.0", alias="BRIDGE_HOST")
    bridge_port: int = Field(default=5000, alias="BRIDGE_PORT")

    # Shared secret the MT5 Expert Advisor must send as the `X-API-Key`
    # header on every request. This is the *only* thing standing between
    # the internet and your brokerage account once this service is exposed
    # publicly (e.g. via Cloudflare Tunnel), so it is required rather than
    # defaulting to something guessable.
    bridge_api_key: str = Field(alias="BRIDGE_API_KEY")

    # --- Webull OpenAPI credentials / session --------------------------------
    # Which Webull environment to trade against. "paper" hits Webull's
    # sandbox/simulated-trading endpoint (real order flow, fake money);
    # "live" hits production with real money. Defaults to "paper" so a
    # fresh checkout can never accidentally place a real order just
    # because DRY_RUN was forgotten -- see the README's recommended
    # go-live progression (paper -> live+DRY_RUN=true -> live).
    webull_mode: str = Field(default="paper", alias="WEBULL_MODE")

    # Paper and live are entirely separate app keys/accounts (Webull
    # issues them separately), so they're configured as two independent
    # credential sets rather than one set that gets pointed at different
    # endpoints. This also means both can be bootstrapped (see
    # scripts/webull_login.py) ahead of time and left ready to go, and a
    # mistake in one mode's credentials can't affect the other.
    webull_paper_app_key: Optional[str] = Field(default=None, alias="WEBULL_PAPER_APP_KEY")
    webull_paper_app_secret: Optional[str] = Field(default=None, alias="WEBULL_PAPER_APP_SECRET")
    webull_paper_account_id: Optional[str] = Field(default=None, alias="WEBULL_PAPER_ACCOUNT_ID")
    # Webull's sandbox/paper endpoint. Overridable in case Webull changes
    # it or you're pointed at a region-specific variant.
    webull_paper_api_endpoint: Optional[str] = Field(
        default="api.sandbox.webull.com", alias="WEBULL_PAPER_API_ENDPOINT"
    )

    webull_live_app_key: Optional[str] = Field(default=None, alias="WEBULL_LIVE_APP_KEY")
    webull_live_app_secret: Optional[str] = Field(default=None, alias="WEBULL_LIVE_APP_SECRET")
    webull_live_account_id: Optional[str] = Field(default=None, alias="WEBULL_LIVE_ACCOUNT_ID")
    # None = use the SDK's production default endpoint.
    webull_live_api_endpoint: Optional[str] = Field(default=None, alias="WEBULL_LIVE_API_ENDPOINT")

    # Lowercase region code the SDK expects (e.g. "us", "hk"). Also used
    # (uppercased) as the `market` field on outgoing orders. Shared across
    # both modes since it reflects your account's region, not the
    # paper/live choice.
    webull_region_id: str = Field(default="us", alias="WEBULL_REGION_ID")

    # "EQUITY" is the only instrument type this bridge constructs orders
    # for; options/crypto/futures use a different order payload shape the
    # MT5 EA doesn't produce. Exposed as a setting rather than hardcoded
    # in case a future version needs to override it per-deployment.
    webull_instrument_type: str = Field(default="EQUITY", alias="WEBULL_INSTRUMENT_TYPE")

    # Base directory for the SDK's persisted session tokens (see
    # scripts/webull_login.py). Must be inside the mounted `./data` volume
    # so a container restart doesn't require re-approving on your phone.
    # active_webull_credentials() appends a "paper"/"live" subdirectory so
    # the two modes' sessions never collide.
    webull_token_dir: Path = Field(default=Path("/app/data/webull_token"), alias="WEBULL_TOKEN_DIR")

    @field_validator("webull_mode")
    @classmethod
    def _validate_webull_mode(cls, v: str) -> str:
        lower = v.lower()
        if lower not in {"paper", "live"}:
            raise ValueError(f"WEBULL_MODE must be 'paper' or 'live', got {v!r}")
        return lower

    def active_webull_credentials(self) -> WebullCredentials:
        """Resolves which credential set to use based on WEBULL_MODE."""
        if self.webull_mode == "live":
            return WebullCredentials(
                app_key=self.webull_live_app_key,
                app_secret=self.webull_live_app_secret,
                account_id=self.webull_live_account_id,
                api_endpoint=self.webull_live_api_endpoint,
                token_dir=self.webull_token_dir / "live",
            )
        return WebullCredentials(
            app_key=self.webull_paper_app_key,
            app_secret=self.webull_paper_app_secret,
            account_id=self.webull_paper_account_id,
            api_endpoint=self.webull_paper_api_endpoint,
            token_dir=self.webull_token_dir / "paper",
        )

    # --- Trading behaviour ----------------------------------------------------
    # When true, the OrderManager logs exactly what it *would* send to
    # Webull but never actually calls the broker. This lets a user wire up
    # MT5 -> bridge end-to-end and watch the logs before risking real money.
    dry_run: bool = Field(default=True, alias="DRY_RUN")

    # Optional path to a JSON file mapping MT5 symbol names to Webull
    # ticker symbols, e.g. {"US500": "SPY"}. Many MT5 brokers use synthetic
    # or suffixed symbol names (AAPL.US, US30, etc.) that don't exist as-is
    # on Webull, so a 1:1 default mapping is rarely correct for anything but
    # plain equities.
    symbol_map_file: Optional[Path] = Field(default=None, alias="SYMBOL_MAP_FILE")

    # --- Logging ---------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file: Optional[Path] = Field(default=None, alias="LOG_FILE")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        # Fail at startup rather than silently falling back to a default
        # logging level if someone typos LOG_LEVEL in their .env.
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(valid)}, got {v!r}")
        return upper


@lru_cache
def get_settings() -> Settings:
    """
    Settings are cached (singleton) because constructing them re-reads and
    re-validates the environment/`.env` file, which is pure overhead once
    the process has started -- env vars don't change at runtime.
    """
    return Settings()
