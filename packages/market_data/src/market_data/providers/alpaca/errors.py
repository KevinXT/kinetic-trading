"""Alpaca-specific failures kept below the provider boundary."""

from common.errors import ConfigError, DataSourceError


class AlpacaConfigError(ConfigError):
    """Raised before HTTP when Alpaca configuration is invalid."""


class AlpacaError(DataSourceError):
    """Base class for Alpaca data-provider failures."""


class AlpacaAuthenticationError(AlpacaError):
    """Raised for rejected credentials."""


class AlpacaPermissionError(AlpacaError):
    """Raised when the requested feed or data is not entitled."""


class AlpacaInvalidRequestError(AlpacaError):
    """Raised for a non-retryable request failure."""


class AlpacaRateLimitError(AlpacaError):
    """Raised when rate-limit retries are exhausted."""


class AlpacaTransientError(AlpacaError):
    """Raised when transport or server retries are exhausted."""


class AlpacaPayloadError(AlpacaError):
    """Raised for invalid JSON or malformed provider payloads."""


class AlpacaPaginationLoopError(AlpacaError):
    """Raised when Alpaca repeats a pagination token."""


class AlpacaMaximumPagesError(AlpacaError):
    """Raised when a response exceeds the configured page guard."""


class AlpacaNormalizationConflictError(AlpacaError):
    """Raised when one response contains conflicting bars for one logical key."""
