"""
API key authentication for the bridge's HTTP endpoints.

Once this service is exposed to the internet (see README "Exposing the
bridge publicly"), the API key is the only thing preventing an outside
party from placing trades on your Webull account. Every route except
/health requires it.
"""
import hmac
import logging

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)


def make_api_key_dependency(expected_key: str):
    """
    Returns a FastAPI dependency bound to `expected_key`. Built as a
    factory (rather than reading settings globally inside the dependency)
    so tests can wire up a fake app with a known test key without touching
    process-wide settings/env vars.
    """

    def verify_api_key(x_api_key: str = Header(default="")) -> None:
        # hmac.compare_digest runs in constant time regardless of where the
        # strings first differ, which prevents an attacker from using
        # response-time differences to guess the key one character at a
        # time (a timing attack). A plain `==` comparison is vulnerable to
        # this for secrets, even though it's fine for non-secret data.
        if not hmac.compare_digest(x_api_key, expected_key):
            logger.warning("Rejected request with invalid or missing API key.")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    return verify_api_key
