"""Real-content controls: fail closed on accidental commit of private bodies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

GIT_TRACKED_FIXTURE_MARKERS = (
    "tests/fixtures/",
    "docs/research/",
    "configs/research/",
)


@dataclass(frozen=True)
class RealContentControlsV1:
    allow_full_body_in_run_artifacts: bool = False
    allow_full_body_in_annotation_batch: bool = True
    annotation_batch_output_is_local_only: bool = True
    allow_real_content_in_git_tracked_paths: bool = False
    maximum_report_excerpt_characters: int = 280
    synthetic_content_source: str = "synthetic_fixture"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> RealContentControlsV1:
        if not data:
            return cls()
        return cls(
            allow_full_body_in_run_artifacts=bool(
                data.get("allow_full_body_in_run_artifacts", False)
            ),
            allow_full_body_in_annotation_batch=bool(
                data.get("allow_full_body_in_annotation_batch", True)
            ),
            annotation_batch_output_is_local_only=bool(
                data.get("annotation_batch_output_is_local_only", True)
            ),
            allow_real_content_in_git_tracked_paths=bool(
                data.get("allow_real_content_in_git_tracked_paths", False)
            ),
            maximum_report_excerpt_characters=int(
                data.get("maximum_report_excerpt_characters", 280)
            ),
            synthetic_content_source=str(data.get("synthetic_content_source", "synthetic_fixture")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_full_body_in_run_artifacts": self.allow_full_body_in_run_artifacts,
            "allow_full_body_in_annotation_batch": self.allow_full_body_in_annotation_batch,
            "annotation_batch_output_is_local_only": self.annotation_batch_output_is_local_only,
            "allow_real_content_in_git_tracked_paths": (
                self.allow_real_content_in_git_tracked_paths
            ),
            "maximum_report_excerpt_characters": self.maximum_report_excerpt_characters,
            "synthetic_content_source": self.synthetic_content_source,
        }


def is_git_tracked_research_path(path: Path | str) -> bool:
    text = str(path).replace("\\", "/")
    return any(marker in text for marker in GIT_TRACKED_FIXTURE_MARKERS)


def assert_path_safe_for_real_content(
    path: Path | str,
    *,
    controls: RealContentControlsV1,
    contains_real_body: bool,
) -> None:
    if not contains_real_body:
        return
    if controls.allow_real_content_in_git_tracked_paths:
        return
    if is_git_tracked_research_path(path):
        raise ValueError(
            f"refusing to write real article content into git-tracked path: {path}. "
            "Set allow_real_content_in_git_tracked_paths only with explicit authorization."
        )


def truncate_excerpt(text: Optional[str], *, max_chars: int) -> Optional[str]:
    if text is None:
        return None
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max(0, max_chars - 1)] + "…"


def redact_body_for_artifact(
    body: Optional[str],
    *,
    content_source: str,
    controls: RealContentControlsV1,
    body_commit_allowed: bool,
) -> Optional[str]:
    """Return body for run artifacts, or None when redacted."""
    if body is None:
        return None
    is_synthetic = content_source == controls.synthetic_content_source
    if is_synthetic:
        return body
    if not body_commit_allowed:
        return None
    if controls.allow_full_body_in_run_artifacts:
        return body
    return None


def annotation_batch_may_include_body(
    *,
    content_source: str,
    controls: RealContentControlsV1,
    output_path: Path | str,
    body_commit_allowed: bool,
) -> bool:
    is_synthetic = content_source == controls.synthetic_content_source
    if is_synthetic:
        return True
    if not body_commit_allowed:
        return False
    if not controls.allow_full_body_in_annotation_batch:
        return False
    assert_path_safe_for_real_content(output_path, controls=controls, contains_real_body=True)
    if controls.annotation_batch_output_is_local_only and is_git_tracked_research_path(output_path):
        raise ValueError(
            "annotation batches with real bodies must remain local-only; "
            f"refusing git-tracked path {output_path}"
        )
    return True


def report_excerpt(
    text: Optional[str],
    *,
    content_source: str,
    controls: RealContentControlsV1,
) -> Optional[str]:
    if text is None:
        return None
    if content_source == controls.synthetic_content_source:
        return truncate_excerpt(text, max_chars=controls.maximum_report_excerpt_characters)
    # Real content: short excerpt only, never full body by default.
    return truncate_excerpt(text, max_chars=controls.maximum_report_excerpt_characters)


def any_real_bodies(content_sources: Sequence[str], controls: RealContentControlsV1) -> bool:
    return any(src != controls.synthetic_content_source for src in content_sources)
