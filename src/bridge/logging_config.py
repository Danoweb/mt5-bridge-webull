"""
Logging setup for the bridge.

The user explicitly wants generous logging: this is a service that moves
real money, and it typically runs unattended on a home server or small
cloud VM. When something goes wrong (a rejected order, a stale Webull
session, a malformed request from the MT5 EA) the logs are the only way to
diagnose it after the fact, so every meaningful decision point in the code
logs *what* happened and *why*.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bridge.config import Settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_logging(settings: Settings) -> None:
    """
    Configure the root logger once, at process startup.

    We attach handlers to the *root* logger (rather than a package-specific
    one) so that log output from third-party libraries we depend on (e.g.
    uvicorn, the underlying webull client) is captured with the same
    format and destination -- important because a Webull-side error often
    surfaces as a log line from inside that library, not from our code.
    """
    root = logging.getLogger()
    root.setLevel(settings.log_level)

    # Clear any handlers a previous call (e.g. in tests that reconfigure
    # logging multiple times) may have attached, to avoid duplicate log
    # lines.
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    if settings.log_file:
        # Rotating so a long-running deployment doesn't silently fill the
        # disk over months of unattended operation.
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.log_file, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Never let a secret slip into the logs: the Webull app secret and the
    # bridge API key must never be logged, even at DEBUG. Rather than trust
    # every call site to remember that, we install a filter that redacts
    # them anywhere they'd otherwise appear.
    redactor = _SecretRedactor(settings)
    for handler in root.handlers:
        handler.addFilter(redactor)


class _SecretRedactor(logging.Filter):
    """Scrubs known secret values out of log records before they're emitted."""

    def __init__(self, settings: Settings):
        super().__init__()
        self._secrets = [
            s
            for s in (
                settings.webull_paper_app_secret,
                settings.webull_live_app_secret,
                settings.bridge_api_key,
            )
            if s
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets and isinstance(record.msg, str):
            for secret in self._secrets:
                if secret in record.msg:
                    record.msg = record.msg.replace(secret, "***REDACTED***")
        return True
