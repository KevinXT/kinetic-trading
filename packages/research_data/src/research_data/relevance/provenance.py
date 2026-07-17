"""Article content provenance and rights statuses for real-corpus pilots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from market_data.domain.serialization import to_json_value

PROVENANCE_VERSION = "article-content-provenance-v1"

RIGHTS_STATUSES = frozenset(
    {
        "user_supplied_authorized",
        "open_license",
        "public_domain",
        "provider_licensed",
        "metadata_only",
        "rights_unclear",
        "redistribution_restricted",
        "research_use_restricted",
    }
)

ACCEPTABLE_RIGHTS_FOR_ELIGIBILITY = frozenset(
    {
        "user_supplied_authorized",
        "open_license",
        "public_domain",
        "provider_licensed",
        "metadata_only",
    }
)

BODY_COMMIT_FORBIDDEN_STATUSES = frozenset(
    {
        "rights_unclear",
        "redistribution_restricted",
        "research_use_restricted",
    }
)


def _req_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_dt(raw: Any, field_name: str) -> datetime:
    if isinstance(raw, datetime):
        return _utc(raw, field_name)
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return _utc(datetime.fromisoformat(text), field_name)


@dataclass(frozen=True)
class ArticleContentProvenanceV1:
    article_id: str
    content_origin: str
    provider_name: str
    source_url: str
    acquisition_method: str
    acquired_at: datetime
    rights_status: str
    license_name: Optional[str]
    license_reference: Optional[str]
    storage_allowed: bool
    research_use_allowed: bool
    redistribution_allowed: bool
    body_commit_allowed: bool
    retention_policy: str
    provenance_notes: Optional[str]
    provenance_version: str = PROVENANCE_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "article_id", _req_text(self.article_id, "article_id"))
        object.__setattr__(self, "content_origin", _req_text(self.content_origin, "content_origin"))
        object.__setattr__(self, "provider_name", _req_text(self.provider_name, "provider_name"))
        object.__setattr__(self, "source_url", _req_text(self.source_url, "source_url"))
        object.__setattr__(
            self,
            "acquisition_method",
            _req_text(self.acquisition_method, "acquisition_method"),
        )
        object.__setattr__(self, "acquired_at", _utc(self.acquired_at, "acquired_at"))
        if self.rights_status not in RIGHTS_STATUSES:
            raise ValueError(
                f"unsupported rights_status {self.rights_status!r}; "
                f"expected one of {sorted(RIGHTS_STATUSES)}"
            )
        object.__setattr__(
            self, "retention_policy", _req_text(self.retention_policy, "retention_policy")
        )
        if self.provenance_version != PROVENANCE_VERSION:
            raise ValueError(
                f"provenance_version must be {PROVENANCE_VERSION!r}, "
                f"got {self.provenance_version!r}"
            )
        if self.rights_status in BODY_COMMIT_FORBIDDEN_STATUSES and self.body_commit_allowed:
            raise ValueError(
                f"body_commit_allowed cannot be true when rights_status=" f"{self.rights_status!r}"
            )
        if (
            not self.research_use_allowed
            and self.rights_status in ACCEPTABLE_RIGHTS_FOR_ELIGIBILITY
        ):
            # Explicit flags override status membership for research eligibility.
            pass
        if self.license_name is not None:
            object.__setattr__(self, "license_name", str(self.license_name).strip() or None)
        if self.license_reference is not None:
            object.__setattr__(
                self, "license_reference", str(self.license_reference).strip() or None
            )
        if self.provenance_notes is not None:
            object.__setattr__(self, "provenance_notes", str(self.provenance_notes).strip() or None)

    @property
    def rights_acceptable_for_eligibility(self) -> bool:
        return (
            self.rights_status in ACCEPTABLE_RIGHTS_FOR_ELIGIBILITY
            and self.research_use_allowed
            and self.storage_allowed
        )

    def to_dict(self) -> dict[str, Any]:
        payload = to_json_value(self)
        assert isinstance(payload, dict)
        payload["rights_acceptable_for_eligibility"] = self.rights_acceptable_for_eligibility
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArticleContentProvenanceV1:
        return cls(
            article_id=str(data["article_id"]),
            content_origin=str(data["content_origin"]),
            provider_name=str(data["provider_name"]),
            source_url=str(data["source_url"]),
            acquisition_method=str(data["acquisition_method"]),
            acquired_at=_parse_dt(data["acquired_at"], "acquired_at"),
            rights_status=str(data["rights_status"]),
            license_name=(str(data["license_name"]) if data.get("license_name") else None),
            license_reference=(
                str(data["license_reference"]) if data.get("license_reference") else None
            ),
            storage_allowed=bool(data["storage_allowed"]),
            research_use_allowed=bool(data["research_use_allowed"]),
            redistribution_allowed=bool(data["redistribution_allowed"]),
            body_commit_allowed=bool(data["body_commit_allowed"]),
            retention_policy=str(data["retention_policy"]),
            provenance_notes=(
                str(data["provenance_notes"]) if data.get("provenance_notes") else None
            ),
            provenance_version=str(data.get("provenance_version", PROVENANCE_VERSION)),
        )


def load_provenance_jsonl(path: str) -> list[ArticleContentProvenanceV1]:
    from pathlib import Path

    rows: list[ArticleContentProvenanceV1] = []
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            continue
        import json

        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(ArticleContentProvenanceV1.from_dict(obj))
    rows.sort(key=lambda r: r.article_id)
    return rows
