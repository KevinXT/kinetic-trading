"""
News data provider integrations for kinetic-trading.

Currently implemented: GDELT DOC API client (query building, config-driven
requests, response parsing). Future providers would be added as sibling
subpackages alongside ``gdelt/``.

Depends on ``common`` for error types. Does not depend on ``pipeline_core``.
"""

from .gdelt import (
    GdeltDocClient as GdeltClient,
)
from .gdelt import (
    GdeltDocRequest,
    build_gdelt_query,
    doc_request_from_config,
    extract_articles,
    timeout_from_config,
)

__all__ = [
    "GdeltClient",
    "GdeltDocRequest",
    "timeout_from_config",
    "doc_request_from_config",
    "build_gdelt_query",
    "extract_articles",
]
