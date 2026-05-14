from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GdeltDocRequest:
    base_url: str
    query: str
    mode: str = "artlist"
    format: str = "json"
    timespan: Optional[str] = "24h"
    maxrecords: int = 50
    sort: str = "datedesc"
