"""Configuration and credential resolution for Alpaca market data."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from market_data.providers.alpaca.errors import AlpacaConfigError


@dataclass(frozen=True, slots=True)
class AlpacaProviderConfig:
    base_url: str = "https://data.alpaca.markets"
    key_id_env: str = "ALPACA_API_KEY_ID"
    secret_key_env: str = "ALPACA_API_SECRET_KEY"
    timeout_seconds: float = 30.0
    maximum_attempts: int = 4
    initial_backoff_seconds: float = 1.0
    maximum_backoff_seconds: float = 30.0
    maximum_pages: int = 100

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> AlpacaProviderConfig:
        defaults = cls()
        instance = cls(
            base_url=str(config.get("base_url", defaults.base_url)),
            key_id_env=str(config.get("key_id_env", defaults.key_id_env)),
            secret_key_env=str(config.get("secret_key_env", defaults.secret_key_env)),
            timeout_seconds=float(config.get("timeout_seconds", defaults.timeout_seconds)),
            maximum_attempts=int(config.get("maximum_attempts", defaults.maximum_attempts)),
            initial_backoff_seconds=float(
                config.get("initial_backoff_seconds", defaults.initial_backoff_seconds)
            ),
            maximum_backoff_seconds=float(
                config.get("maximum_backoff_seconds", defaults.maximum_backoff_seconds)
            ),
            maximum_pages=int(config.get("maximum_pages", defaults.maximum_pages)),
        )
        instance.validate()
        return instance

    def validate(self) -> None:
        if not self.base_url.strip():
            raise AlpacaConfigError("Alpaca base_url must not be empty")
        if not self.key_id_env.strip() or not self.secret_key_env.strip():
            raise AlpacaConfigError("Alpaca credential environment-variable names are required")
        if self.timeout_seconds <= 0:
            raise AlpacaConfigError("Alpaca timeout_seconds must be positive")
        if self.maximum_attempts < 1:
            raise AlpacaConfigError("Alpaca maximum_attempts must be at least 1")
        if self.initial_backoff_seconds < 0 or self.maximum_backoff_seconds < 0:
            raise AlpacaConfigError("Alpaca backoff values must be non-negative")
        if self.initial_backoff_seconds > self.maximum_backoff_seconds:
            raise AlpacaConfigError(
                "Alpaca initial_backoff_seconds must not exceed maximum_backoff_seconds"
            )
        if self.maximum_pages < 1:
            raise AlpacaConfigError("Alpaca maximum_pages must be at least 1")

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}/v2/stocks/bars"

    def safe_summary(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "key_id_env": self.key_id_env,
            "secret_key_env": self.secret_key_env,
            "timeout_seconds": self.timeout_seconds,
            "maximum_attempts": self.maximum_attempts,
            "initial_backoff_seconds": self.initial_backoff_seconds,
            "maximum_backoff_seconds": self.maximum_backoff_seconds,
            "maximum_pages": self.maximum_pages,
        }


@dataclass(frozen=True, slots=True)
class AlpacaCredentials:
    key_id: str
    secret_key: str

    def __repr__(self) -> str:
        return "AlpacaCredentials(key_id=<redacted>, secret_key=<redacted>)"


def resolve_credentials(
    config: AlpacaProviderConfig,
    environ: Mapping[str, str] | None = None,
) -> AlpacaCredentials:
    values = os.environ if environ is None else environ
    key_id = values.get(config.key_id_env, "")
    secret_key = values.get(config.secret_key_env, "")
    missing = [
        name
        for name, value in (
            (config.key_id_env, key_id),
            (config.secret_key_env, secret_key),
        )
        if not value
    ]
    if missing:
        raise AlpacaConfigError(
            "missing required Alpaca environment variable(s): " + ", ".join(missing)
        )
    return AlpacaCredentials(key_id=key_id, secret_key=secret_key)
