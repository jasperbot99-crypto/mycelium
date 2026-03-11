"""Token provider primitives for static keys and OAuth-style refresh flows."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenProvider(Protocol):
    """Provider that returns a currently valid API token."""

    async def get_token(self) -> str:
        """Return a valid token (may refresh before returning)."""
        ...


@dataclass(frozen=True)
class StaticTokenProvider:
    """Token provider for static API keys."""

    token: str

    async def get_token(self) -> str:
        return self.token


@dataclass(frozen=True)
class EnvTokenProvider:
    """Token provider backed by an environment variable."""

    env_var: str

    async def get_token(self) -> str:
        token = os.environ.get(self.env_var)
        if not token:
            raise ValueError(
                f"Token required. Set env var {self.env_var} or inject token_provider."
            )
        return token


def build_token_provider(
    token: str | None,
    *,
    env_var: str,
    token_provider: TokenProvider | None = None,
    require_token: bool = False,
) -> TokenProvider:
    """Resolve token-provider precedence with backward compatibility.

    Precedence:
    1) explicit token_provider
    2) explicit token string
    3) env-backed provider
    """
    if token_provider is not None:
        return token_provider
    if token is not None and token.strip():
        return StaticTokenProvider(token.strip())
    if require_token and not os.environ.get(env_var):
        raise ValueError(
            f"API key required. Provide token_provider/token or set {env_var}."
        )
    return EnvTokenProvider(env_var=env_var)
