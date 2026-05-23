from __future__ import annotations

from typing import Any, Dict

from .schemas import GdeltDocRequest


def _gdelt_root(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return (
        cfg.get("providers", {}).get("news", {}).get("gdelt", {})
        if isinstance(cfg, dict)
        else {}
    )


def timeout_from_config(cfg: Dict[str, Any], default: int = 30) -> int:
    gd = _gdelt_root(cfg)
    return int(gd.get("timeout_s", default))


def doc_request_from_config(cfg: Dict[str, Any], *, query: str) -> GdeltDocRequest:
    gd = _gdelt_root(cfg)
    doc = gd.get("doc", {})

    return GdeltDocRequest(
        base_url=doc["base_url"],
        query=query,
        mode=doc.get("mode", "artlist"),
        format=doc.get("format", "json"),
        timespan=doc.get("timespan", "24h"),
        maxrecords=int(doc.get("maxrecords", 50)),
        sort=doc.get("sort", "datedesc"),
    )
