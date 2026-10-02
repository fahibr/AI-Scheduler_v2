"""Map uploaded door Excel rows to the extracted structured door table."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

from .table import dataframe_to_excel_bytes

DOOR_MARK_CANDIDATES = ("Door Mark", "DoorMark", "door_mark", "Mark")
STRUCTURED_JOIN_COLS = [
    "Door Type",
    "Width (mm)",
    "Height (mm)",
    "Thickness (mm)",
    "Fire Rating",
    "Door Material",
    "Frame Material",
    "Configuration",
    "Qty by Level",
    "Quantity",
    "Location",
    "Description / Specification",
    "Description",
    "Remarks",
    "Page",
    "Source PDF",
]


def _normalize_mark(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().upper()
    return text or None


def find_door_mark_column(columns: list[str]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in columns}
    for candidate in DOOR_MARK_CANDIDATES:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    for col in columns:
        if "door" in str(col).lower() and "mark" in str(col).lower():
            return col
    return None


def load_door_excel(file_bytes: bytes, sheet_name: str | int | None = 0) -> pd.DataFrame:
    """Load the uploaded door schedule Excel workbook."""
    return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name or 0)


def map_excel_to_structured(
    excel_df: pd.DataFrame,
    structured_df: pd.DataFrame,
    *,
    door_mark_column: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Left-join Excel rows to structured door types on Door Mark == Door Type.

    Returns (mapped dataframe, summary stats).
    """
    if excel_df is None or excel_df.empty:
        raise ValueError("Excel door schedule is empty.")
    if structured_df is None or structured_df.empty:
        raise ValueError("Structured door table is empty. Run Step 1 extraction first.")

    mark_col = door_mark_column or find_door_mark_column(list(excel_df.columns))
    if not mark_col:
        raise ValueError(
            "Could not find a 'Door Mark' column in the Excel file. "
            f"Columns found: {list(excel_df.columns)}"
        )
    if "Door Type" not in structured_df.columns:
        raise ValueError("Structured table is missing the 'Door Type' column.")

    left = excel_df.copy()
    left["_join_key"] = left[mark_col].map(_normalize_mark)

    right_cols = [c for c in STRUCTURED_JOIN_COLS if c in structured_df.columns]
    right = structured_df[right_cols].copy()
    right["_join_key"] = right["Door Type"].map(_normalize_mark)
    right = right.drop_duplicates(subset=["_join_key"], keep="first")

    # Avoid colliding Excel 'Page' / similar names with structured columns
    rename_structured = {}
    for col in right.columns:
        if col in {"_join_key", "Door Type"}:
            continue
        if col in left.columns:
            rename_structured[col] = f"{col} (PDF)"
    right = right.rename(columns=rename_structured)

    mapped = left.merge(right, on="_join_key", how="left", suffixes=("", "_pdf"))
    mapped["Match Status"] = mapped["Door Type"].notna().map(
        {True: "Matched", False: "Unmatched"}
    )

    # Prefer original Door Mark display; keep Door Type from PDF when matched
    preferred = [
        c
        for c in [
            "Sequence",
            "Level",
            mark_col,
            "Door Type",
            "Match Status",
            "Location (From Room)",
            "Location (To Room)",
            "Configuration of Door",
            "Qty",
            "Width (mm)",
            "Height (mm)",
            "Thickness (mm)",
            "Fire Rating",
            "Door Material",
            "Frame Material",
            "Configuration",
            "Configuration (PDF)",
            "Quantity",
            "Quantity (PDF)",
            "Location",
            "Location (PDF)",
            "Description",
            "Description (PDF)",
            "Page",
            "Page (PDF)",
            "Source PDF",
            "Remark",
        ]
        if c in mapped.columns
    ]
    extras = [c for c in mapped.columns if c not in preferred and c != "_join_key"]
    mapped = mapped[preferred + extras].drop(columns=["_join_key"], errors="ignore")

    matched = int((mapped["Match Status"] == "Matched").sum())
    unmatched = int((mapped["Match Status"] == "Unmatched").sum())
    unique_marks = mapped[mark_col].map(_normalize_mark).nunique(dropna=True)
    matched_types = (
        mapped.loc[mapped["Match Status"] == "Matched", "Door Type"].nunique()
        if matched
        else 0
    )
    summary = {
        "excel_rows": len(mapped),
        "matched_rows": matched,
        "unmatched_rows": unmatched,
        "unique_door_marks": int(unique_marks),
        "matched_door_types": int(matched_types),
        "door_mark_column": mark_col,
        "structured_types": int(structured_df["Door Type"].nunique()),
    }
    return mapped, summary


def mapped_dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    return dataframe_to_excel_bytes(df, sheet_name="Complete Door Schedule")
