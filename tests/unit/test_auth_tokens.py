"""Unit tests for token-provider helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mycelium.auth.tokens import EnvTokenProvider, StaticTokenProvider, build_token_provider


@pytest.mark.asyncio
async def test_static_token_provider_returns_token() -> None:
    provider = StaticTokenProvider("abc")
    assert await provider.get_token() == "abc"


@pytest.mark.asyncio
async def test_env_token_provider_reads_env() -> None:
    with patch.dict("os.environ", {"OPENAI_API_KEY": "env-token"}, clear=True):
        provider = EnvTokenProvider("OPENAI_API_KEY")
        assert await provider.get_token() == "env-token"


def test_build_token_provider_fail_fast_when_required() -> None:
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="API key required"),
    ):
        _ = build_token_provider(
            None,
            env_var="OPENAI_API_KEY",
            token_provider=None,
            require_token=True,
        )
