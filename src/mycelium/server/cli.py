"""CLI entrypoint for Mycelium server mode."""

from __future__ import annotations

import os

import uvicorn

from mycelium.config import MyceliumConfig
from mycelium.server.app import create_app


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def build_config_from_env() -> MyceliumConfig:
    return MyceliumConfig(
        database_url=os.getenv("MYCELIUM_DATABASE_URL", "postgresql://localhost:5432/mycelium_dev"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        server_host=os.getenv("MYCELIUM_SERVER_HOST", "127.0.0.1"),
        server_port=int(os.getenv("MYCELIUM_SERVER_PORT", "8080")),
        server_api_key=os.getenv("MYCELIUM_SERVER_API_KEY"),
        server_request_timeout_s=float(os.getenv("MYCELIUM_SERVER_REQUEST_TIMEOUT_S", "30.0")),
        server_cors_allow_origins=[
            value.strip()
            for value in os.getenv("MYCELIUM_SERVER_CORS_ORIGINS", "").split(",")
            if value.strip()
        ],
        benchmark_enabled=_env_bool("MYCELIUM_BENCHMARK_ENABLED"),
        adaptive_learning_cycle_interval_hours=int(
            os.getenv("MYCELIUM_ADAPTIVE_LEARNING_CYCLE_HOURS", "6")
        ),
    )


def main() -> None:
    config = build_config_from_env()
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.server_host,
        port=config.server_port,
        log_level="info",
        timeout_keep_alive=int(config.server_request_timeout_s),
    )


if __name__ == "__main__":
    main()
