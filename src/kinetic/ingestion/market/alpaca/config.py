"""Configuration and credential resolution for Alpaca market data."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from kinetic.ingestion.market.alpaca.errors import AlpacaConfigError

_INTEGER_STRING = re.compile(r"^-?[0-9]+$")
_FLOAT_STRING = re.compile(r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")


def normalize_api_origin(base_url: str) -> str:
    """
    Normalize a non-secret API origin for cache identity and diagnostics.

    Collapses equivalent trailing-slash and host/scheme casing. Strips default
    ports (:80 / :443). Rejects credentials, query strings, and fragments so they
    cannot enter cache keys.
    """
    text = base_url.strip()
    if not text:
        raise AlpacaConfigError("Alpaca base_url must not be empty")
    parts = urlsplit(text)
    if parts.username is not None or parts.password is not None:
        raise AlpacaConfigError("Alpaca base_url must not contain credentials")
    if parts.query:
        raise AlpacaConfigError("Alpaca base_url must not include a query string")
    if parts.fragment:
        raise AlpacaConfigError("Alpaca base_url must not include a URL fragment")
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise AlpacaConfigError(
            f"Alpaca base_url must be an absolute http(s) URL, got {base_url!r}"
        )
    host = parts.hostname
    if host is None:
        raise AlpacaConfigError(
            f"Alpaca base_url must be an absolute http(s) URL, got {base_url!r}"
        )
    port = parts.port
    if port is not None and not (
        (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80)
    ):
        netloc = f"{host.lower()}:{port}"
    else:
        netloc = host.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


def parse_strict_int(
    value: object,
    field_name: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parse an integer without accepting booleans or truncated floats."""
    if isinstance(value, bool) or isinstance(value, float):
        raise AlpacaConfigError(
            f"{field_name} must be an integer, got {value!r} ({type(value).__name__})"
        )
    if isinstance(value, str):
        text = value.strip()
        if not _INTEGER_STRING.fullmatch(text):
            raise AlpacaConfigError(f"{field_name} must be an integer string, got {value!r}")
        parsed = int(text)
    elif isinstance(value, int):
        parsed = value
    else:
        raise AlpacaConfigError(
            f"{field_name} must be an integer, got {value!r} ({type(value).__name__})"
        )
    if minimum is not None and parsed < minimum:
        raise AlpacaConfigError(f"{field_name} must be >= {minimum}, got {parsed}")
    if maximum is not None and parsed > maximum:
        raise AlpacaConfigError(f"{field_name} must be <= {maximum}, got {parsed}")
    return parsed


def parse_strict_float(
    value: object,
    field_name: str,
    *,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    """Parse a finite float without accepting booleans."""
    if isinstance(value, bool):
        raise AlpacaConfigError(
            f"{field_name} must be a number, got {value!r} ({type(value).__name__})"
        )
    if isinstance(value, str):
        text = value.strip()
        if not _FLOAT_STRING.fullmatch(text):
            raise AlpacaConfigError(f"{field_name} must be a numeric string, got {value!r}")
        parsed = float(text)
    elif isinstance(value, (int, float)):
        parsed = float(value)
    else:
        raise AlpacaConfigError(
            f"{field_name} must be a number, got {value!r} ({type(value).__name__})"
        )
    if not math.isfinite(parsed):
        raise AlpacaConfigError(f"{field_name} must be finite, got {value!r}")
    if minimum is not None and parsed < minimum:
        raise AlpacaConfigError(f"{field_name} must be >= {minimum}, got {parsed}")
    if exclusive_minimum is not None and parsed <= exclusive_minimum:
        raise AlpacaConfigError(f"{field_name} must be > {exclusive_minimum}, got {parsed}")
    return parsed


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", normalize_api_origin(self.base_url))
        object.__setattr__(self, "key_id_env", self.key_id_env.strip())
        object.__setattr__(self, "secret_key_env", self.secret_key_env.strip())
        self.validate()

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> AlpacaProviderConfig:
        defaults = cls()
        return cls(
            base_url=str(config.get("base_url", defaults.base_url)),
            key_id_env=str(config.get("key_id_env", defaults.key_id_env)),
            secret_key_env=str(config.get("secret_key_env", defaults.secret_key_env)),
            timeout_seconds=parse_strict_float(
                config.get("timeout_seconds", defaults.timeout_seconds),
                "providers.market.alpaca.timeout_seconds",
                exclusive_minimum=0.0,
            ),
            maximum_attempts=parse_strict_int(
                config.get("maximum_attempts", defaults.maximum_attempts),
                "providers.market.alpaca.maximum_attempts",
                minimum=1,
            ),
            initial_backoff_seconds=parse_strict_float(
                config.get("initial_backoff_seconds", defaults.initial_backoff_seconds),
                "providers.market.alpaca.initial_backoff_seconds",
                minimum=0.0,
            ),
            maximum_backoff_seconds=parse_strict_float(
                config.get("maximum_backoff_seconds", defaults.maximum_backoff_seconds),
                "providers.market.alpaca.maximum_backoff_seconds",
                minimum=0.0,
            ),
            maximum_pages=parse_strict_int(
                config.get("maximum_pages", defaults.maximum_pages),
                "providers.market.alpaca.maximum_pages",
                minimum=1,
            ),
        )

    def validate(self) -> None:
        if not self.key_id_env or not self.secret_key_env:
            raise AlpacaConfigError("Alpaca credential environment-variable names are required")
        if self.timeout_seconds <= 0:
            raise AlpacaConfigError("Alpaca timeout_seconds must be positive")
        if (
            isinstance(self.maximum_attempts, bool)
            or not isinstance(self.maximum_attempts, int)
            or self.maximum_attempts < 1
        ):
            raise AlpacaConfigError("Alpaca maximum_attempts must be at least 1")
        if self.initial_backoff_seconds < 0 or self.maximum_backoff_seconds < 0:
            raise AlpacaConfigError("Alpaca backoff values must be non-negative")
        if self.initial_backoff_seconds > self.maximum_backoff_seconds:
            raise AlpacaConfigError(
                "Alpaca initial_backoff_seconds must not exceed maximum_backoff_seconds"
            )
        if (
            isinstance(self.maximum_pages, bool)
            or not isinstance(self.maximum_pages, int)
            or self.maximum_pages < 1
        ):
            raise AlpacaConfigError("Alpaca maximum_pages must be at least 1")

    @property
    def api_origin(self) -> str:
        return normalize_api_origin(self.base_url)

    @property
    def endpoint(self) -> str:
        return f"{self.api_origin}/v2/stocks/bars"

    def safe_summary(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "api_origin": self.api_origin,
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
