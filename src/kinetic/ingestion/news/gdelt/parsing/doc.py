from __future__ import annotations

from typing import Any, Dict, List


def extract_articles(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    arts = resp.get("articles")
    return arts if isinstance(arts, list) else []
