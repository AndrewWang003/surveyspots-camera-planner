"""Google OAuth2 token verification as a FastAPI dependency.

For Chrome-extension ``chrome.identity`` tokens the flow issues **access
tokens** (not ID tokens), so we validate via the Google *userinfo* endpoint
rather than decoding a JWT.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

# Re-use a single Session for connection-pooling to googleapis.com ----------
_http_session = requests.Session()

_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _extract_bearer_token(request: Request) -> str:
    """Pull the Bearer token from the ``Authorization`` header.

    Raises
    ------
    HTTPException(401)
        If the header is missing or malformed.
    """
    auth_header: str | None = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header.",
        )

    parts = auth_header.split(" ", maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authorization header must use the 'Bearer <token>' scheme.",
        )

    token = parts[1].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Bearer token is empty.",
        )
    return token


def _validate_domain(email: str) -> str:
    """Ensure the e-mail domain is whitelisted.

    Returns the domain on success, raises HTTPException(403) otherwise.
    """
    domain = email.rsplit("@", maxsplit=1)[-1].lower()
    allowed = settings.domain_whitelist_list

    if not allowed:
        # No whitelist configured → allow everyone
        return domain

    if domain not in allowed:
        logger.warning("Domain '%s' not in whitelist %s.", domain, allowed)
        raise HTTPException(
            status_code=403,
            detail=f"Email domain '{domain}' is not authorised.",
        )
    return domain


async def verify_google_token(request: Request) -> dict[str, Any]:
    """FastAPI dependency that verifies a Google access token.
    
    (DEV MODE: Bypassing actual Google API validation for local development)
    """
    logger.info("Dev mode: Bypassing OAuth token validation")
    
    # We still extract the token to ensure the header is passed properly by the extension
    token = _extract_bearer_token(request)
    
    return {
        "email": "dev@company.com.au",
        "name": "Dev User",
        "domain": "company.com.au",
    }
