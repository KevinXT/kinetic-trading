"""Local UI configuration loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

DEFAULT_CONFIG_PATH = Path("configs/research/semiconductor_relevance_annotation_ui_local.yaml")
UI_MODES = frozenset({"preflight", "annotator", "duplicate_reviewer", "adjudicator", "audit"})


@dataclass(frozen=True)
class UIConfig:
    database_path: Path
    default_mode: str
    bind_address: str
    show_full_body: bool
    maximum_body_display_characters: Optional[int]
    maximum_evidence_characters: int
    allow_external_links: bool
    allow_remote_images: bool
    pilot_config_path: Path
    artifact_root: Path
    local_corpus_root: Path
    local_export_root: Path
    require_rights_eligible: bool
    allow_full_body_in_local_ui: bool
    allow_full_body_in_logs: bool
    allow_full_body_in_exports: bool
    allow_real_content_in_git_tracked_paths: bool
    config_path: Path

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, config_path: Path) -> UIConfig:
        ui = data.get("ui") or {}
        pilot = data.get("pilot") or {}
        controls = data.get("content_controls") or {}
        mode = str(ui.get("default_mode", "preflight"))
        if mode not in UI_MODES:
            raise ValueError(f"unsupported ui mode {mode!r}")
        max_body = ui.get("maximum_body_display_characters")
        return cls(
            database_path=Path(
                str(ui.get("database_path", "data/local_only/relevance_annotation_ui.sqlite3"))
            ),
            default_mode=mode,
            bind_address=str(ui.get("bind_address", "127.0.0.1")),
            show_full_body=bool(ui.get("show_full_body", True)),
            maximum_body_display_characters=(
                None if max_body in (None, "", "null") else int(str(max_body))
            ),
            maximum_evidence_characters=int(ui.get("maximum_evidence_characters", 500)),
            allow_external_links=bool(ui.get("allow_external_links", False)),
            allow_remote_images=bool(ui.get("allow_remote_images", False)),
            pilot_config_path=Path(
                str(
                    pilot.get(
                        "config_path",
                        "configs/research/semiconductor_relevance_real_corpus_pilot_local.yaml",
                    )
                )
            ),
            artifact_root=Path(str(pilot.get("artifact_root", "experiments"))),
            local_corpus_root=Path(str(pilot.get("local_corpus_root", "data/real_corpus"))),
            local_export_root=Path(
                str(pilot.get("local_export_root", "data/local_only/relevance_exports"))
            ),
            require_rights_eligible=bool(controls.get("require_rights_eligible", True)),
            allow_full_body_in_local_ui=bool(controls.get("allow_full_body_in_local_ui", True)),
            allow_full_body_in_logs=bool(controls.get("allow_full_body_in_logs", False)),
            allow_full_body_in_exports=bool(controls.get("allow_full_body_in_exports", False)),
            allow_real_content_in_git_tracked_paths=bool(
                controls.get("allow_real_content_in_git_tracked_paths", False)
            ),
            config_path=config_path,
        )


def load_ui_config(path: str | Path | None = None) -> UIConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        # Sensible defaults when config file is absent (tests).
        return UIConfig.from_mapping({}, config_path=config_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"UI config must be a mapping: {config_path}")
    return UIConfig.from_mapping(data, config_path=config_path)
