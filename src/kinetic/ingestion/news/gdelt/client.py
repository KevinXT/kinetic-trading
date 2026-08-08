from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from .config import timeout_from_config
from .endpoints import fetch_doc
from .schemas import GdeltDocRequest


class GdeltClient:
    def __init__(
        self,
        timeout_s: int = 30,
        session: Optional[requests.Session] = None,
    ):
        self.timeout_s = int(timeout_s)
        self.session = session or requests.Session()

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "GdeltClient":
        return cls(timeout_s=timeout_from_config(cfg))

    def close(self) -> None:
        self.session.close()

    def fetch_doc(self, req: GdeltDocRequest) -> Dict[str, Any]:
        return fetch_doc(self.session, req=req, timeout_s=self.timeout_s)


# Public alias used by package exports (see gdelt/__init__.py).
GdeltDocClient = GdeltClient
