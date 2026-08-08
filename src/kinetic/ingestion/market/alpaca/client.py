"""HTTP client for Alpaca multi-symbol historical stock bars."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

import requests

from kinetic.ingestion.market.alpaca.config import AlpacaCredentials, AlpacaProviderConfig
from kinetic.ingestion.market.alpaca.errors import (
    AlpacaAuthenticationError,
    AlpacaInvalidRequestError,
    AlpacaMaximumPagesError,
    AlpacaPaginationLoopError,
    AlpacaPayloadError,
    AlpacaPermissionError,
    AlpacaRateLimitError,
    AlpacaTransientError,
)
from kinetic.ingestion.market.alpaca.raw_models import AlpacaBarsPage, parse_alpaca_bars_page
from kinetic.ingestion.requests import BarsRequest

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_TIMEFRAME = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>Min|Hour|Day|Week|Month)$")


class ResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str
    reason: str

    def json(self) -> object: ...


class Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, object],
        timeout: float,
    ) -> ResponseLike: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AlpacaRawPages:
    pages: tuple[AlpacaBarsPage, ...]
    retry_count: int


def _rfc3339(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def validate_alpaca_request(request: BarsRequest) -> None:
    match = _TIMEFRAME.fullmatch(request.timeframe)
    if match is None:
        raise AlpacaInvalidRequestError(f"unsupported Alpaca timeframe {request.timeframe!r}")
    count = int(match.group("count"))
    unit = match.group("unit")
    maximum = {"Min": 59, "Hour": 23, "Day": 1, "Week": 1, "Month": 12}[unit]
    if count > maximum:
        raise AlpacaInvalidRequestError(f"unsupported Alpaca timeframe {request.timeframe!r}")
    if request.adjustment not in {"raw", "split", "dividend", "all"}:
        raise AlpacaInvalidRequestError(f"unsupported Alpaca adjustment {request.adjustment!r}")
    if request.currency is not None and len(request.currency) != 3:
        raise AlpacaInvalidRequestError("Alpaca currency must be a three-letter code")


def build_alpaca_params(
    request: BarsRequest, *, page_token: str | None = None
) -> dict[str, object]:
    validate_alpaca_request(request)
    params: dict[str, object] = {
        "symbols": ",".join(request.symbols),
        "timeframe": request.timeframe,
        "start": _rfc3339(request.start),
        "end": _rfc3339(request.end),
        "limit": request.limit,
        "adjustment": request.adjustment,
        "feed": request.feed,
        "sort": request.sort,
    }
    if request.asof is not None:
        params["asof"] = request.asof.isoformat()
    if request.currency is not None:
        params["currency"] = request.currency
    if page_token is not None:
        params["page_token"] = page_token
    return params


class AlpacaBarsClient:
    """Fetch and validate every page for one historical bars request."""

    def __init__(
        self,
        config: AlpacaProviderConfig,
        credentials: AlpacaCredentials,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._credentials = credentials
        self._transport = cast(Transport, transport or requests.Session())
        self._sleep = sleep

    def __repr__(self) -> str:
        return (
            "AlpacaBarsClient("
            f"endpoint={self.config.endpoint!r}, timeout_seconds={self.config.timeout_seconds!r})"
        )

    def close(self) -> None:
        self._transport.close()

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._credentials.key_id,
            "APCA-API-SECRET-KEY": self._credentials.secret_key,
        }

    def _backoff(self, attempt: int) -> float:
        return float(
            min(
                self.config.initial_backoff_seconds * (2 ** (attempt - 1)),
                self.config.maximum_backoff_seconds,
            )
        )

    def _retry_after(self, response: ResponseLike, attempt: int) -> float:
        raw_value = response.headers.get("Retry-After")
        if raw_value is not None:
            try:
                value = float(raw_value)
            except ValueError:
                value = -1
            if value >= 0:
                return min(value, self.config.maximum_backoff_seconds)
        return self._backoff(attempt)

    def _safe_provider_message(self, response: ResponseLike) -> str:
        try:
            payload = response.json()
        except Exception:
            return response.reason or "request failed"
        if isinstance(payload, dict):
            code = payload.get("code")
            message = payload.get("message")
            parts = []
            if isinstance(code, (str, int)):
                parts.append(f"code={code}")
            if isinstance(message, str):
                safe_message = message.replace(self._credentials.key_id, "<redacted>")
                safe_message = safe_message.replace(self._credentials.secret_key, "<redacted>")
                parts.append(f"message={safe_message}")
            if parts:
                return " ".join(parts)
        return response.reason or "request failed"

    def _raise_http_error(self, response: ResponseLike, request: BarsRequest) -> None:
        context = (
            f"operation=historical_stock_bars status={response.status_code} "
            f"feed={request.feed!r} symbols={','.join(request.symbols)!r} "
            f"{self._safe_provider_message(response)}"
        )
        if response.status_code == 401:
            raise AlpacaAuthenticationError(f"Alpaca authentication failed: {context}")
        if response.status_code == 403:
            raise AlpacaPermissionError(
                f"Alpaca denied the requested feed {request.feed!r}: {context}"
            )
        if response.status_code == 429:
            raise AlpacaRateLimitError(f"Alpaca rate limit exhausted: {context}")
        if response.status_code in _RETRYABLE_STATUSES:
            raise AlpacaTransientError(f"Alpaca transient failure exhausted retries: {context}")
        raise AlpacaInvalidRequestError(f"Alpaca request failed: {context}")

    def _request_page(
        self, request: BarsRequest, page_token: str | None
    ) -> tuple[AlpacaBarsPage, int]:
        params = build_alpaca_params(request, page_token=page_token)
        retry_count = 0
        for attempt in range(1, self.config.maximum_attempts + 1):
            try:
                response = self._transport.get(
                    self.config.endpoint,
                    headers=self._headers(),
                    params=params,
                    timeout=self.config.timeout_seconds,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.config.maximum_attempts:
                    raise AlpacaTransientError(
                        "Alpaca historical_stock_bars transport failure exhausted retries "
                        f"for feed={request.feed!r} symbols={','.join(request.symbols)!r}"
                    ) from exc
                retry_count += 1
                wait = self._backoff(attempt)
                logger.warning(
                    "provider=alpaca operation=historical_stock_bars "
                    "attempt=%d/%d transport_error=%s retry_in_seconds=%s",
                    attempt,
                    self.config.maximum_attempts,
                    type(exc).__name__,
                    wait,
                )
                self._sleep(wait)
                continue

            if response.status_code in _RETRYABLE_STATUSES:
                if attempt == self.config.maximum_attempts:
                    self._raise_http_error(response, request)
                retry_count += 1
                wait = self._retry_after(response, attempt)
                logger.warning(
                    "provider=alpaca operation=historical_stock_bars "
                    "attempt=%d/%d status=%d retry_in_seconds=%s",
                    attempt,
                    self.config.maximum_attempts,
                    response.status_code,
                    wait,
                )
                self._sleep(wait)
                continue

            if not 200 <= response.status_code < 300:
                self._raise_http_error(response, request)

            try:
                payload = response.json()
            except Exception as exc:
                raise AlpacaPayloadError(
                    "Alpaca historical_stock_bars returned invalid JSON"
                ) from exc
            return parse_alpaca_bars_page(payload), retry_count

        raise AssertionError("retry loop must return or raise")

    def fetch_pages(self, request: BarsRequest) -> AlpacaRawPages:
        validate_alpaca_request(request)
        pages: list[AlpacaBarsPage] = []
        seen_tokens: set[str] = set()
        page_token: str | None = None
        retry_count = 0

        while True:
            page, page_retries = self._request_page(request, page_token)
            retry_count += page_retries
            pages.append(page)
            next_token = page.next_page_token
            if not next_token:
                break
            if next_token in seen_tokens:
                raise AlpacaPaginationLoopError(
                    f"Alpaca repeated a pagination token after {len(pages)} page(s)"
                )
            seen_tokens.add(next_token)
            if len(pages) >= self.config.maximum_pages:
                raise AlpacaMaximumPagesError(
                    "Alpaca historical_stock_bars exceeded maximum_pages="
                    f"{self.config.maximum_pages}"
                )
            page_token = next_token

        return AlpacaRawPages(pages=tuple(pages), retry_count=retry_count)
