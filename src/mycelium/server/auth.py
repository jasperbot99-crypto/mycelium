"""Server auth helpers."""

from __future__ import annotations

from fastapi import Header, HTTPException, status


def require_bearer_token(
    authorization: str | None,
    expected_token: str | None,
) -> None:
    if not expected_token:
        return

    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if parts[1] != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )


def auth_dependency_factory(expected_token: str | None):
    async def _auth(authorization: str | None = Header(default=None)) -> None:
        require_bearer_token(authorization, expected_token)

    return _auth
