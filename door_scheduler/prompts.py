"""Load versioned / format-specific LLM prompts from the Prompt-version/ folder."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .schedule_formats import (
    SCHEDULE_FORMAT_ELEVATION,
    SCHEDULE_FORMAT_STRUCTURED_TABLE,
    normalize_schedule_format,
)

PROMPT_ROOT = Path(__file__).resolve().parent.parent / "Prompt-version"
ACTIVE_VERSION_FILE = PROMPT_ROOT / "active_version.txt"


@dataclass(frozen=True)
class PromptBundle:
    version: str
    schema_hint: str
    vision_system: str
    vision_user: str
    refine_system: str
    refine_user: str
    manifest: dict
    analyze_system: str | None = None
    analyze_user: str | None = None
    schedule_format: str = SCHEDULE_FORMAT_ELEVATION

    @property
    def supports_table_analyze(self) -> bool:
        return bool(self.analyze_system and self.analyze_user)

    def render_vision_user(
        self,
        *,
        page_number: int,
        text_block: str = "",
        normalized_table_block: str = "",
    ) -> str:
        return self.vision_user.format(
            page_number=page_number,
            text_block=text_block,
            schema_hint=self.schema_hint,
            normalized_table_block=normalized_table_block or "",
        )

    def render_analyze_user(self, *, page_number: int, text_block: str = "") -> str:
        if not self.analyze_user:
            raise RuntimeError(
                f"Prompt bundle '{self.version}' has no analyze_user template."
            )
        return self.analyze_user.format(
            page_number=page_number,
            text_block=text_block,
        )

    def render_refine_user(self, *, marks: list[str] | str, page_text: str) -> str:
        marks_text = marks if isinstance(marks, str) else str(marks)
        return self.refine_user.format(
            marks=marks_text,
            schema_hint=self.schema_hint,
            page_text=page_text,
        )


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def resolve_active_version(prompt_root: Path | None = None) -> str:
    """
    Resolve which prompt version / format folder to use.

    Priority:
      1. DOOR_PROMPT_VERSION env var
      2. Prompt-version/active_version.txt
      3. elevation if present, else highest vN folder
    """
    root = prompt_root or PROMPT_ROOT
    env_version = os.getenv("DOOR_PROMPT_VERSION", "").strip()
    if env_version:
        return env_version

    active_file = root / "active_version.txt"
    if active_file.exists():
        version = active_file.read_text(encoding="utf-8").strip()
        if version and (root / version).is_dir():
            return version

    if (root / SCHEDULE_FORMAT_ELEVATION).is_dir():
        return SCHEDULE_FORMAT_ELEVATION

    versions = sorted(
        p.name for p in root.iterdir() if p.is_dir() and p.name.startswith("v")
    )
    if not versions:
        raise FileNotFoundError(f"No prompt versions found under {root}")
    return versions[-1]


def list_prompt_versions(prompt_root: Path | None = None) -> list[str]:
    root = prompt_root or PROMPT_ROOT
    if not root.exists():
        return []
    skip = {"__pycache__"}
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and p.name not in skip and not p.name.startswith(".")
    )


def resolve_prompt_version_for_format(schedule_format: str | None) -> str:
    """Map UI schedule format → Prompt-version folder name."""
    fmt = normalize_schedule_format(schedule_format)
    root = PROMPT_ROOT
    if (root / fmt).is_dir():
        return fmt
    # Fallbacks for older checkouts
    if fmt == SCHEDULE_FORMAT_ELEVATION and (root / "v2").is_dir():
        return "v2"
    if fmt == SCHEDULE_FORMAT_STRUCTURED_TABLE and (root / "v2").is_dir():
        return "v2"
    return resolve_active_version(root)


@lru_cache(maxsize=16)
def load_prompts(version: str | None = None) -> PromptBundle:
    """Load a prompt bundle. Pass version=None to use the active version."""
    root = PROMPT_ROOT
    ver = version or resolve_active_version(root)
    folder = root / ver
    if not folder.exists():
        raise FileNotFoundError(
            f"Prompt version '{ver}' not found at {folder}. "
            f"Available: {list_prompt_versions(root)}"
        )

    manifest_path = folder / "manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required = {
        "schema_hint": folder / "schema_hint.txt",
        "vision_system": folder / "vision_system.txt",
        "vision_user": folder / "vision_user.txt",
        "refine_system": folder / "refine_system.txt",
        "refine_user": folder / "refine_user.txt",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"Prompt version '{ver}' is missing files: {', '.join(missing)}"
        )

    analyze_system_path = folder / "analyze_system.txt"
    analyze_user_path = folder / "analyze_user.txt"
    analyze_system = (
        _read_text(analyze_system_path) if analyze_system_path.exists() else None
    )
    analyze_user = _read_text(analyze_user_path) if analyze_user_path.exists() else None

    schedule_format = normalize_schedule_format(
        str(manifest.get("format") or ver)
    )

    return PromptBundle(
        version=ver,
        schema_hint=_read_text(required["schema_hint"]),
        vision_system=_read_text(required["vision_system"]),
        vision_user=_read_text(required["vision_user"]),
        refine_system=_read_text(required["refine_system"]),
        refine_user=_read_text(required["refine_user"]),
        manifest=manifest,
        analyze_system=analyze_system,
        analyze_user=analyze_user,
        schedule_format=schedule_format,
    )


def clear_prompt_cache() -> None:
    load_prompts.cache_clear()
