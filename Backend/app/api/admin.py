"""Admin authentication shared by HR-only endpoints."""

import secrets

from fastapi import Header, HTTPException, Request


def require_admin(request: Request, x_admin_token: str = Header(default="")) -> None:
    """Allow the request only with the configured ADMIN_API_TOKEN; 404 when admin access is disabled."""
    token = request.app.state.settings.admin_api_token
    expected = token.get_secret_value() if token else ""
    if not expected:
        raise HTTPException(status_code=404, detail="Not found.")
    if not secrets.compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token.")
