"""Normalize extracted door records into a readable table / Excel workbook."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

COLUMN_ORDER = [
    "Item No.",
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
    "Remarks",
    "Page",
    "Source PDF",
]

COLUMN_MAP = {
    "item_number": "Item No.",
    "door_type": "Door Type",
    "width_mm": "Width (mm)",
    "height_mm": "Height (mm)",
    "thickness_mm": "Thickness (mm)",
    "fire_rating": "Fire Rating",
    "door_material": "Door Material",
    "frame_material": "Frame Material",
    "configuration": "Configuration",
    "quantity_by_level": "Qty by Level",
    "quantity": "Quantity",
    "location": "Location",
    "description": "Description / Specification",
    "remarks": "Remarks",
    "page": "Page",
    "source_pdf": "Source PDF",
}


def _door_type_sort_key(value: Any) -> tuple:
    text = str(value or "")
    match = __import__("re").match(r"^([A-Z]+)(\d+)([A-Z]*)$", text)
    if not match:
        # Letter-only marks (e.g. FDGA) sort by full text
        return (text, 0, "")
    prefix, number, suffix = match.groups()
    return (prefix, int(number), suffix)


def _item_number_sort_key(value: Any) -> tuple:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return (1, 10**9, "")
    match = __import__("re").match(r"^(\d+)", text)
    if match:
        return (0, int(match.group(1)), text)
    return (0, 10**9, text)


def build_structured_table(doors: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a clean, sorted dataframe from raw extraction records."""
    if not doors:
        return pd.DataFrame(columns=COLUMN_ORDER)

    frame = pd.DataFrame(doors).rename(columns=COLUMN_MAP)

    # Deduplicate by door type, preferring rows with more filled dimension fields
    for col in ("Width (mm)", "Height (mm)", "Thickness (mm)", "Quantity", "Page"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    score_cols = ["Width (mm)", "Height (mm)", "Thickness (mm)", "Quantity"]
    present = [c for c in score_cols if c in frame.columns]
    frame["_score"] = frame[present].notna().sum(axis=1) if present else 0
    if "Qty by Level" in frame.columns:
        frame["_score"] = frame["_score"] + frame["Qty by Level"].notna().astype(int)
    if "Description / Specification" in frame.columns:
        frame["_score"] = frame["_score"] + frame["Description / Specification"].notna().astype(int)
    if "Item No." in frame.columns:
        frame["_score"] = frame["_score"] + frame["Item No."].notna().astype(int)

    # Always collapse to one row per Door Type (split PDFs of one schedule)
    if "Source PDF" in frame.columns:
        def _join_sources(series: pd.Series) -> Any:
            vals = [
                str(v).strip()
                for v in series.tolist()
                if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip()
            ]
            unique = list(dict.fromkeys(vals))
            return "; ".join(unique) if unique else pd.NA

        sources = (
            frame.sort_values(["Door Type", "_score"], ascending=[True, False])
            .groupby("Door Type", dropna=False)["Source PDF"]
            .agg(_join_sources)
        )
    else:
        sources = None

    frame = (
        frame.sort_values(["Door Type", "_score"], ascending=[True, False])
        .drop_duplicates(subset=["Door Type"], keep="first")
        .drop(columns=["_score"])
    )
    if sources is not None:
        frame = frame.set_index("Door Type")
        frame["Source PDF"] = sources
        frame = frame.reset_index()

    frame = frame.reindex(columns=[c for c in COLUMN_ORDER if c in frame.columns])
    if "Item No." in frame.columns and frame["Item No."].notna().any():
        frame = frame.sort_values(
            by=["Item No.", "Door Type"],
            key=lambda s: s.map(
                _item_number_sort_key if s.name == "Item No." else _door_type_sort_key
            ),
        ).reset_index(drop=True)
    else:
        frame = frame.sort_values(
            by="Door Type",
            key=lambda s: s.map(_door_type_sort_key),
        ).reset_index(drop=True)

    # Nice integer display where possible
    for col in ("Width (mm)", "Height (mm)", "Thickness (mm)", "Quantity", "Page"):
        if col in frame.columns:
            frame[col] = frame[col].astype("Int64")

    return frame


def total_door_count(df: pd.DataFrame) -> int:
    """Sum of overall Quantity across door types (falls back to row count)."""
    if df is None or df.empty:
        return 0
    if "Quantity" in df.columns and df["Quantity"].notna().any():
        return int(pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).sum())
    return len(df)


def load_structured_excel(file_bytes: bytes) -> pd.DataFrame:
    """
    Load a corrected structured door-schedule Excel back into the app table.

    Accepts the same headers as the download, plus legacy 'Description'.
    """
    frame = pd.read_excel(BytesIO(file_bytes), sheet_name=0)
    if frame is None or frame.empty:
        raise ValueError("Uploaded Excel is empty.")

    rename = {}
    for col in frame.columns:
        name = str(col).strip()
        if name == "Description" and "Description / Specification" not in frame.columns:
            rename[col] = "Description / Specification"
        elif name != col:
            rename[col] = name
    if rename:
        frame = frame.rename(columns=rename)

    if "Door Type" not in frame.columns:
        raise ValueError(
            "Uploaded Excel must include a 'Door Type' column. "
            f"Found: {list(frame.columns)}"
        )

    # Keep known columns first, then any extra user columns
    ordered = [c for c in COLUMN_ORDER if c in frame.columns]
    extras = [c for c in frame.columns if c not in ordered]
    frame = frame.reindex(columns=ordered + extras)

    for col in ("Width (mm)", "Height (mm)", "Thickness (mm)", "Quantity", "Page"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("Int64")

    return frame.reset_index(drop=True)


def merge_structured_tables(
    existing: pd.DataFrame | None,
    new: pd.DataFrame | None,
    *,
    prefer_existing: bool = True,
) -> pd.DataFrame:
    """
    Merge two structured schedules by Door Type.

    Used when the user extracts another / split PDF after correcting the table.
    - Existing corrected rows for a Door Type are kept (prefer_existing=True).
    - Empty fields on an existing row can be filled from the new extraction.
    - New Door Types from the latest extraction are appended.
    """
    if existing is None or existing.empty:
        return new.copy() if new is not None else pd.DataFrame(columns=COLUMN_ORDER)
    if new is None or new.empty:
        return existing.copy()

    left = existing.copy()
    right = new.copy()
    for frame in (left, right):
        if "Door Type" not in frame.columns:
            raise ValueError("Both tables must include a 'Door Type' column.")
        frame["_join_key"] = (
            frame["Door Type"].astype(str).str.strip().str.upper()
        )

    # Drop blank keys
    left = left[left["_join_key"].ne("") & left["_join_key"].ne("NAN")]
    right = right[right["_join_key"].ne("") & right["_join_key"].ne("NAN")]

    left_keys = set(left["_join_key"])
    right_by_key = {
        key: row for key, row in right.set_index("_join_key").iterrows()
    }

    merged_rows: list[dict[str, Any]] = []
    all_columns = list(dict.fromkeys(list(left.columns) + list(right.columns)))
    all_columns = [c for c in all_columns if c != "_join_key"]

    for _, row in left.iterrows():
        key = row["_join_key"]
        combined = {c: row.get(c) for c in all_columns}
        if key in right_by_key:
            incoming = right_by_key[key]
            for col in all_columns:
                existing_val = combined.get(col)
                new_val = incoming.get(col) if col in incoming.index else None
                existing_empty = (
                    existing_val is None
                    or (isinstance(existing_val, float) and pd.isna(existing_val))
                    or str(existing_val).strip() == ""
                )
                new_empty = (
                    new_val is None
                    or (isinstance(new_val, float) and pd.isna(new_val))
                    or str(new_val).strip() == ""
                )
                if prefer_existing:
                    if existing_empty and not new_empty:
                        combined[col] = new_val
                else:
                    if not new_empty:
                        combined[col] = new_val
                    elif existing_empty:
                        combined[col] = existing_val
        merged_rows.append(combined)

    for key, incoming in right_by_key.items():
        if key in left_keys:
            continue
        merged_rows.append({c: incoming.get(c) if c in incoming.index else None for c in all_columns})

    result = pd.DataFrame(merged_rows)
    ordered = [c for c in COLUMN_ORDER if c in result.columns]
    extras = [c for c in result.columns if c not in ordered]
    result = result.reindex(columns=ordered + extras)

    for col in ("Width (mm)", "Height (mm)", "Thickness (mm)", "Quantity", "Page"):
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").astype("Int64")

    if "Door Type" in result.columns:
        result = result.sort_values(
            by="Door Type",
            key=lambda s: s.map(_door_type_sort_key),
        ).reset_index(drop=True)
    return result


def dataframe_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Door Schedule") -> bytes:
    """Serialize the structured table to an .xlsx byte payload."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export = df.copy()
        export.to_excel(writer, index=False, sheet_name=sheet_name)
        worksheet = writer.sheets[sheet_name]
        for idx, column in enumerate(export.columns, start=1):
            values = [
                "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
                for v in export.iloc[:, idx - 1].tolist()[:200]
            ]
            max_len = max([len(str(column))] + [len(v) for v in values])
            worksheet.column_dimensions[worksheet.cell(1, idx).column_letter].width = min(
                max_len + 2, 56
            )
    return buffer.getvalue()
