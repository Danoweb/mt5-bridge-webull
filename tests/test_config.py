"""
Tests for paper/live credential selection. This is the one piece of pure
logic in config.py worth covering directly -- getting it wrong would mean
either mode silently uses the wrong credentials/token directory, which
Settings' own validation can't catch (both credential sets are Optional).
"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from bridge.config import Settings


def make_settings(**overrides) -> Settings:
    defaults = dict(
        BRIDGE_API_KEY="test",
        WEBULL_PAPER_APP_KEY="paper-key",
        WEBULL_PAPER_APP_SECRET="paper-secret",
        WEBULL_PAPER_ACCOUNT_ID="paper-acct",
        WEBULL_LIVE_APP_KEY="live-key",
        WEBULL_LIVE_APP_SECRET="live-secret",
        WEBULL_LIVE_ACCOUNT_ID="live-acct",
        WEBULL_TOKEN_DIR="/app/data/webull_token",
    )
    defaults.update(overrides)
    # _env_file=None: don't let a real .env in the test working directory
    # leak into these tests -- they must only reflect the values above.
    return Settings(_env_file=None, **defaults)


def test_defaults_to_paper_mode():
    settings = make_settings()
    assert settings.webull_mode == "paper"


def test_paper_mode_resolves_paper_credentials():
    settings = make_settings(WEBULL_MODE="paper")
    creds = settings.active_webull_credentials()

    assert creds.app_key == "paper-key"
    assert creds.app_secret == "paper-secret"
    assert creds.account_id == "paper-acct"
    assert creds.token_dir == Path("/app/data/webull_token/paper")


def test_live_mode_resolves_live_credentials():
    settings = make_settings(WEBULL_MODE="live")
    creds = settings.active_webull_credentials()

    assert creds.app_key == "live-key"
    assert creds.app_secret == "live-secret"
    assert creds.account_id == "live-acct"
    assert creds.token_dir == Path("/app/data/webull_token/live")


def test_paper_and_live_token_dirs_never_collide():
    paper_dir = make_settings(WEBULL_MODE="paper").active_webull_credentials().token_dir
    live_dir = make_settings(WEBULL_MODE="live").active_webull_credentials().token_dir
    assert paper_dir != live_dir


def test_mode_is_case_insensitive():
    assert make_settings(WEBULL_MODE="LIVE").webull_mode == "live"


def test_invalid_mode_rejected():
    with pytest.raises(ValidationError):
        make_settings(WEBULL_MODE="staging")
