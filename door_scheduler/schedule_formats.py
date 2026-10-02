"""Door schedule format identifiers and UI labels."""

from __future__ import annotations

# Prompt-version/<id>/ folders
SCHEDULE_FORMAT_ELEVATION = "elevation"
SCHEDULE_FORMAT_STRUCTURED_TABLE = "structured_table"

SCHEDULE_FORMATS: dict[str, dict[str, str]] = {
    SCHEDULE_FORMAT_ELEVATION: {
        "label": "Elevation Door Schedule",
        "help": (
            "Drawing-style schedules: door elevations / type symbols with "
            "New… specification notes beside or under each mark."
        ),
    },
    SCHEDULE_FORMAT_STRUCTURED_TABLE: {
        "label": "Structured Table Door Schedule",
        "help": (
            "Tabular schedules (rows/columns). The agent first analyses the "
            "table layout, may reorganise columns/rows without losing data, "
            "then extracts door types."
        ),
    },
}


def normalize_schedule_format(value: str | None) -> str:
    raw = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "elevation": SCHEDULE_FORMAT_ELEVATION,
        "elevation_door_schedule": SCHEDULE_FORMAT_ELEVATION,
        "structured_table": SCHEDULE_FORMAT_STRUCTURED_TABLE,
        "structured": SCHEDULE_FORMAT_STRUCTURED_TABLE,
        "table": SCHEDULE_FORMAT_STRUCTURED_TABLE,
        "structured_table_door_schedule": SCHEDULE_FORMAT_STRUCTURED_TABLE,
    }
    return aliases.get(raw, SCHEDULE_FORMAT_ELEVATION)


def schedule_format_label(format_id: str) -> str:
    fmt = normalize_schedule_format(format_id)
    return SCHEDULE_FORMATS.get(fmt, {}).get("label", fmt)


def schedule_format_options() -> list[tuple[str, str]]:
    return [(key, meta["label"]) for key, meta in SCHEDULE_FORMATS.items()]
