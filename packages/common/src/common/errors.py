"""
Shared exception hierarchy for the kinetic-trading project.

All project-specific errors inherit from TradingSystemError, which lives here
so that any package can raise and catch typed errors without depending on a
higher-level package. Modules may define their own subclasses, but the base
types belong in ``common`` to keep the dependency direction clean.
"""


class TradingSystemError(Exception):
    """
    Base class for all custom errors in this project.

    Catch this at the top-level runner/CLI if you want to handle
    all known system errors cleanly.
    """

    pass


class ConfigError(TradingSystemError):
    """
    Raised when configuration files (YAML) are missing, invalid,
    or have the wrong structure.
    """

    pass


class PipelineError(TradingSystemError):
    """
    Raised when the pipeline engine fails (step wiring, execution order,
    missing steps, etc.).
    """

    pass


class DataSourceError(TradingSystemError):
    """
    Raised when an external data provider fails (GDELT, market feeds, APIs).
    """

    pass


class ExecutionError(TradingSystemError):
    """
    Raised when trade execution or broker communication fails.
    """

    pass


class DuplicateTaskError(TradingSystemError):
    """Raised when registering a pipeline task name that is already registered."""

    pass


class CostGuardrailError(TradingSystemError):
    """
    Raised when a cloud query is blocked by a cost guardrail.

    Examples: estimated bytes exceed ``maximum_bytes_billed``, estimated cost
    exceeds a cap, or real execution was requested without the required typed
    confirmation value.
    """

    pass
