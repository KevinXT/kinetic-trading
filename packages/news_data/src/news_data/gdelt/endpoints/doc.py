from __future__ import annotations

import logging
import time
from typing import Any, Dict

import requests
from common.errors import DataSourceError

from ..schemas import GdeltDocRequest

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 6  # seconds; GDELT rate-limits to 1 req per 5s


def fetch(
    session: requests.Session,
    *,
    req: GdeltDocRequest,
    timeout_s: int,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "query": req.query,
        "mode": req.mode,
        "format": req.format,
        "maxrecords": int(req.maxrecords),
        "sort": req.sort,
    }

    if req.timespan:
        params["timespan"] = req.timespan

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(req.base_url, params=params, timeout=int(timeout_s))
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF_BASE * attempt
                logger.warning(
                    "attempt %d/%d failed (%s), retrying in %ds",
                    attempt,
                    MAX_RETRIES,
                    type(exc).__name__,
                    wait,
                )
                time.sleep(wait)
                continue
            raise DataSourceError(
                f"GDELT DOC request failed after {MAX_RETRIES} attempts: {exc}"
            ) from exc

        if r.status_code == 429 and attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF_BASE * attempt
            logger.warning(
                "attempt %d/%d got 429 rate-limited, retrying in %ds",
                attempt,
                MAX_RETRIES,
                wait,
            )
            time.sleep(wait)
            continue

        break

    if not r.ok:
        snippet = (r.text or "")[:600]
        raise DataSourceError(
            f"GDELT DOC HTTP {r.status_code} {r.reason}\n"
            f"URL={r.url}\n"
            f"Body snippet:\n{snippet}"
        )

    # Ensure JSON
    ct = (r.headers.get("Content-Type") or "").lower()
    text = r.text or ""

    if "json" not in ct and not text.lstrip().startswith(("{", "[")):
        snippet = text[:800]
        raise DataSourceError(
            f"GDELT returned non-JSON response\n"
            f"Content-Type={ct!r}\n"
            f"URL={r.url}\n"
            f"Body snippet:\n{snippet}"
        )

    try:
        return r.json()
    except Exception as e:
        snippet = text[:800]
        raise DataSourceError(
            f"Failed to parse JSON from GDELT\nURL={r.url}\nBody snippet:\n{snippet}"
        ) from e
