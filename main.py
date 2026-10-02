"""
AI Door Scheduler — Streamlit app.

Extract door types and requirements from architectural door-schedule PDFs.

Run:
  streamlit run main.py
"""

from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from door_scheduler import (
    ProgressEvent,
    build_structured_table,
    clear_prompt_cache,
    dataframe_to_excel_bytes,
    extract_doors_from_pdfs,
    list_prompt_versions,
    load_prompts,
    load_structured_excel,
    merge_structured_tables,
    parse_page_selection,
    total_door_count,
)
from door_scheduler.auth import (
    active_region,
    is_admin,
    render_admin_panel,
    render_auth_sidebar,
    render_login_gate,
    render_sidebar_nav,
)
from door_scheduler.prompts import resolve_prompt_version_for_format
from door_scheduler.regions import region_label
from door_scheduler.schedule_formats import (
    SCHEDULE_FORMAT_ELEVATION,
    SCHEDULE_FORMATS,
    normalize_schedule_format,
    schedule_format_label,
)

load_dotenv()

ROOT = Path(__file__).resolve().parent
EXPORTS = ROOT / "exports"
EXPORTS.mkdir(exist_ok=True)


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "structured_df": None,
        "extract_log": [],
        "extract_usage": None,
        "last_extract_meta": None,
        "structured_editor_version": 0,
        "app_view": "extract",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _azure_ready() -> tuple[bool, str]:
    missing = [
        name
        for name in ("ENDPOINT_URL", "AZURE_OPENAI_API_KEY", "DEPLOYMENT_NAME")
        if not os.getenv(name, "").strip()
    ]
    if missing:
        return False, "Missing in .env: " + ", ".join(missing)
    return True, os.getenv("DEPLOYMENT_NAME", "")


def _format_eta(elapsed: float, current: int, total: int) -> str:
    if current <= 0 or total <= 0:
        return "—"
    rate = elapsed / current
    remaining = max(total - current, 0) * rate
    return str(timedelta(seconds=int(remaining)))


def _render_sidebar() -> dict[str, Any]:
    st.sidebar.title("AI Door Scheduler")
    st.sidebar.caption("Extract door types from PDF schedules")
    render_sidebar_nav()

    ready, detail = _azure_ready()
    if ready:
        st.sidebar.success(f"Azure OpenAI · `{detail}`")
    else:
        st.sidebar.error(detail)

    try:
        fmt = normalize_schedule_format(
            st.session_state.get("schedule_format") or SCHEDULE_FORMAT_ELEVATION
        )
        prompt_folder = resolve_prompt_version_for_format(fmt)
        prompts = load_prompts(prompt_folder)
        desc = (prompts.manifest or {}).get("description", "")
        st.sidebar.markdown(f"**Format prompts** `{prompt_folder}`")
        if desc:
            st.sidebar.caption(desc)
        versions = list_prompt_versions()
        if versions:
            st.sidebar.caption("Installed: " + ", ".join(versions))
    except Exception as exc:  # noqa: BLE001
        st.sidebar.warning(f"Prompt load issue: {exc}")
        prompt_folder = "?"

    st.sidebar.divider()
    dpi = st.sidebar.slider("Raster DPI", min_value=120, max_value=300, value=180, step=20)
    page_spec = st.sidebar.text_input(
        "Pages",
        value="all",
        help="all · 1-6 · 1,8,10 · 1-3,8,10 (applied to each PDF)",
    )
    st.sidebar.caption(f"API min interval: {os.getenv('DOOR_API_MIN_INTERVAL', '2.0')}s")

    return {
        "dpi": dpi,
        "page_spec": page_spec,
        "prompt_version": prompt_folder,
        "schedule_format": normalize_schedule_format(
            st.session_state.get("schedule_format") or SCHEDULE_FORMAT_ELEVATION
        ),
    }


def _progress_ui() -> dict[str, Any]:
    """Create live progress widgets (bar + stage + metrics + log)."""
    st.markdown("##### Extraction progress")
    progress = st.progress(0, text="Starting… 0%")
    stage = st.empty()
    metrics = st.empty()
    log_box = st.empty()
    return {
        "progress": progress,
        "stage": stage,
        "metrics": metrics,
        "log_box": log_box,
        "log_lines": [],
        "doors_found": 0,
    }


def _on_progress_factory(ui: dict[str, Any], started: float):
    def _handler(event: ProgressEvent) -> None:
        total = max(event.total or 0, 1)
        current = min(event.current or 0, total)
        # During convert/start keep a small visible fraction so the bar moves
        if event.stage in {"start", "convert", "file"} and current == 0:
            frac = 0.02
        else:
            frac = min(current / total, 1.0)
        pct = int(round(frac * 100))
        elapsed = time.monotonic() - started
        eta = _format_eta(elapsed, current if current else 1, total)

        if event.doors_found:
            ui["doors_found"] = ui.get("doors_found", 0) + int(event.doors_found)

        stage_label = {
            "start": "Starting",
            "convert": "Converting PDF → images",
            "file": "Opening file",
            "analyze": "Analysing table",
            "extract": "Vision extract",
            "refine": "Refining fields",
            "page_done": "Page complete",
            "done": "Finished",
        }.get(event.stage, event.stage)

        ui["progress"].progress(
            frac,
            text=f"{pct}% · {stage_label} · page {current}/{total}",
        )
        ui["stage"].info(event.message)
        ui["metrics"].markdown(
            f"| Elapsed | ETA | Pages | Door types found | Tokens in | Tokens out |\n"
            f"|---|---|---|---|---|---|\n"
            f"| `{timedelta(seconds=int(elapsed))}` | `{eta}` | "
            f"`{current}/{total}` | `{ui.get('doors_found', 0)}` | "
            f"`{event.prompt_tokens_total}` | `{event.completion_tokens_total}` |"
        )
        ui["log_lines"].append(event.message)
        ui["log_box"].code("\n".join(ui["log_lines"][-50:]), language=None)

    return _handler


def step_extract(settings: dict[str, Any]) -> None:
    st.subheader("Extract door types from PDF")
    st.markdown(
        "Upload one or more door-schedule PDFs (including **split PDFs** of the same "
        "schedule). The agent captures **door type**, sizes, fire rating, materials, "
        "configuration, **description/specification**, **remarks**, "
        "**qty by level**, and **overall total**."
    )

    format_ids = list(SCHEDULE_FORMATS.keys())
    current_fmt = normalize_schedule_format(
        settings.get("schedule_format")
        or st.session_state.get("schedule_format")
        or SCHEDULE_FORMAT_ELEVATION
    )
    selected_fmt = st.radio(
        "Door schedule format",
        options=format_ids,
        index=format_ids.index(current_fmt)
        if current_fmt in format_ids
        else 0,
        format_func=schedule_format_label,
        horizontal=True,
        key="schedule_format",
        help=SCHEDULE_FORMATS[current_fmt]["help"],
    )
    st.caption(SCHEDULE_FORMATS[normalize_schedule_format(selected_fmt)]["help"])
    settings["schedule_format"] = normalize_schedule_format(selected_fmt)
    settings["prompt_version"] = resolve_prompt_version_for_format(
        settings["schedule_format"]
    )

    has_existing = (
        st.session_state.structured_df is not None
        and not st.session_state.structured_df.empty
    )
    if has_existing:
        st.info(
            f"Current table has **{len(st.session_state.structured_df)}** door type(s). "
            "You can extract another PDF and **merge** it while keeping your corrections, "
            "or replace the table entirely."
        )
        merge_mode = st.radio(
            "When extracting more PDFs",
            options=[
                "Append / merge (keep corrected rows)",
                "Replace entire table",
            ],
            index=0,
            horizontal=True,
            key="extract_merge_mode",
            help=(
                "Append/merge keeps your edited or uploaded corrections for existing "
                "Door Types, adds new types from the new PDF, and fills only empty fields. "
                "Use this for split door-schedule PDFs."
            ),
        )
    else:
        merge_mode = "Replace entire table"

    uploads = st.file_uploader(
        "Door schedule PDF(s)",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_uploads",
    )

    col_a, col_b, col_c = st.columns([1.2, 1.2, 2])
    with col_a:
        run_label = (
            "Extract & merge"
            if has_existing and merge_mode.startswith("Append")
            else "Extract door types"
        )
        run = st.button(run_label, type="primary", use_container_width=True)
    with col_b:
        clear = st.button(
            "Clear table",
            use_container_width=True,
            disabled=not has_existing,
            help="Remove the current structured table so the next extract starts fresh.",
        )

    if clear and has_existing:
        st.session_state.structured_df = None
        st.session_state.extract_log = []
        st.session_state.extract_usage = None
        st.session_state.last_extract_meta = None
        st.session_state.structured_editor_version += 1
        st.session_state.pop("last_corrected_upload", None)
        st.success("Structured table cleared.")
        st.rerun()

    if run:
        ready, detail = _azure_ready()
        if not ready:
            st.error(detail)
            return
        if not uploads:
            st.warning("Please upload at least one PDF.")
            return

        try:
            page_numbers = parse_page_selection(settings["page_spec"])
        except ValueError as exc:
            st.error(f"Invalid page selection: {exc}")
            return

        pdf_files = [(f.name, f.getvalue()) for f in uploads]
        ui = _progress_ui()
        started = time.monotonic()
        handler = _on_progress_factory(ui, started)

        try:
            doors, pages, usage = extract_doors_from_pdfs(
                pdf_files,
                dpi=settings["dpi"],
                page_numbers=page_numbers,
                on_progress=handler,
                schedule_format=settings.get("schedule_format"),
                prompt_version=settings.get("prompt_version"),
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Extraction failed: {exc}")
            return

        new_df = build_structured_table(doors)
        prefer_existing = has_existing and merge_mode.startswith("Append")
        if prefer_existing:
            # Keep in-editor corrections currently shown
            existing_df = st.session_state.structured_df
            df = merge_structured_tables(
                existing_df,
                new_df,
                prefer_existing=True,
            )
            added = len(df) - len(existing_df)
            action_note = (
                f"Merged into existing table · kept corrections · "
                f"**{max(added, 0)}** new door type(s) added"
            )
        else:
            df = new_df
            action_note = "Replaced structured table"

        overall_doors = total_door_count(df)
        prev_meta = st.session_state.last_extract_meta or {}
        prev_files = list(prev_meta.get("files") or [])
        new_files = [n for n, _ in pdf_files]
        if prefer_existing:
            files = list(dict.fromkeys(prev_files + new_files))
            pages_total = int(prev_meta.get("pages") or 0) + len(pages)
            prev_usage = st.session_state.extract_usage or {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
            usage_totals = {
                "prompt_tokens": prev_usage.get("prompt_tokens", 0) + usage.prompt_tokens,
                "completion_tokens": prev_usage.get("completion_tokens", 0)
                + usage.completion_tokens,
                "total_tokens": prev_usage.get("total_tokens", 0) + usage.total_tokens,
            }
        else:
            files = new_files
            pages_total = len(pages)
            usage_totals = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }

        st.session_state.structured_df = df
        st.session_state.structured_editor_version += 1
        st.session_state.extract_log = list(ui["log_lines"])
        st.session_state.extract_usage = usage_totals
        st.session_state.last_extract_meta = {
            "files": files,
            "pages": pages_total,
            "door_types": len(df),
            "door_units": overall_doors,
            "prompt": settings["prompt_version"],
            "schedule_format": settings.get("schedule_format"),
            "elapsed_s": int(time.monotonic() - started),
        }
        ui["progress"].progress(1.0, text="100% · Finished")
        st.success(
            f"{action_note}. Now **{len(df)}** door type(s) · "
            f"**{overall_doors}** total door(s) from **{pages_total}** page(s) "
            f"in {timedelta(seconds=int(time.monotonic() - started))}."
        )

    df = st.session_state.structured_df
    meta = st.session_state.last_extract_meta
    usage = st.session_state.extract_usage

    if df is None:
        st.info(
            "No extraction yet. Upload one or more PDFs (split schedules welcome) "
            "and click **Extract door types**."
        )
        return

    if meta:
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Door types", meta.get("door_types", meta.get("doors", len(df))))
        m2.metric("Total doors", meta.get("door_units", total_door_count(df)))
        m3.metric("Pages", meta["pages"])
        m4.metric("PDFs", len(meta["files"]))
        m5.metric(
            "Tokens",
            usage["total_tokens"] if usage else 0,
            help=(
                f"in {usage['prompt_tokens']} / out {usage['completion_tokens']}"
                if usage
                else None
            ),
        )
        with st.expander("Source PDFs"):
            st.write(", ".join(meta.get("files") or []))

    st.markdown("#### Structured door schedule")
    st.warning(
        "**Disclaimer:** AI extraction can make mistakes. Please **double-check** "
        "all extracted data (door types, sizes, quantities, materials, descriptions, "
        "and remarks) against the original PDF before use. "
        "You can edit the table here, or **download the structured Excel**, correct it, "
        "then **upload the corrected file** below to replace the results. "
        "For **split PDFs**, keep your corrections and use **Extract & merge** to add "
        "the next file without losing edited rows."
    )
    st.caption(
        "**Qty by Level** = count per floor · **Quantity** = overall total · "
        "**Description / Specification** = schedule note · "
        "**Remarks** = REMARKS / notes for that door type."
    )
    upload_msg = st.session_state.pop("corrected_upload_message", None)
    if upload_msg:
        st.success(upload_msg)

    edited = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key=f"structured_editor_{st.session_state.structured_editor_version}",
        hide_index=True,
        column_config={
            "Qty by Level": st.column_config.TextColumn(
                "Qty by Level",
                help="Per-level counts, e.g. Level 1: 24; Level 2: 5",
                width="medium",
            ),
            "Quantity": st.column_config.NumberColumn(
                "Quantity",
                help="Overall TOTAL for this door type",
            ),
            "Description / Specification": st.column_config.TextColumn(
                "Description / Specification",
                help="Full description or specification from the door schedule",
                width="large",
            ),
            "Remarks": st.column_config.TextColumn(
                "Remarks",
                help="Remarks / notes for this door type (e.g. REFER TO KEY PLAN)",
                width="medium",
            ),
        },
    )
    st.session_state.structured_df = edited
    if st.session_state.last_extract_meta:
        st.session_state.last_extract_meta["door_units"] = total_door_count(edited)
        st.session_state.last_extract_meta["door_types"] = len(edited)

    xlsx = dataframe_to_excel_bytes(edited)
    dl_col, up_col = st.columns([1, 2])
    with dl_col:
        st.download_button(
            "Download structured Excel",
            data=xlsx,
            file_name="door_schedule_structured.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with up_col:
        corrected = st.file_uploader(
            "Upload corrected structured Excel",
            type=["xlsx", "xls"],
            accept_multiple_files=False,
            key="corrected_structured_upload",
            help="Upload an edited copy of the downloaded structured Excel to replace the table above.",
        )
        if corrected is not None:
            upload_token = f"{corrected.name}:{corrected.size}"
            if st.session_state.get("last_corrected_upload") != upload_token:
                try:
                    loaded = load_structured_excel(corrected.getvalue())
                    st.session_state.structured_df = loaded
                    st.session_state.structured_editor_version += 1
                    st.session_state.last_corrected_upload = upload_token
                    if st.session_state.last_extract_meta is None:
                        st.session_state.last_extract_meta = {
                            "files": [corrected.name],
                            "pages": 0,
                            "door_types": len(loaded),
                            "door_units": total_door_count(loaded),
                            "prompt": "uploaded",
                            "elapsed_s": 0,
                        }
                    else:
                        st.session_state.last_extract_meta["door_types"] = len(loaded)
                        st.session_state.last_extract_meta["door_units"] = total_door_count(
                            loaded
                        )
                    st.session_state.corrected_upload_message = (
                        f"Loaded corrected Excel · **{len(loaded)}** door type(s) · "
                        f"**{total_door_count(loaded)}** total door(s)."
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not load corrected Excel: {exc}")

    if st.session_state.extract_log:
        with st.expander("Extraction log"):
            st.code("\n".join(st.session_state.extract_log), language=None)


def main() -> None:
    st.set_page_config(
        page_title="AI Door Scheduler",
        page_icon="🚪",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    clear_prompt_cache()
    _init_state()

    if not render_login_gate():
        return

    settings = _render_sidebar()
    render_auth_sidebar()

    view = st.session_state.get("app_view") or "extract"
    if view == "admin" and is_admin():
        st.title("Admin")
        st.caption("Register users, manage region access, and monitor logins.")
        render_admin_panel()
        return

    st.title("AI Door Scheduler")
    region = active_region()
    region_note = f" · Active region: **{region_label(region)}**" if region else ""
    st.caption(
        "Vision agent for architectural door schedules — handles varied table "
        "templates and terms (hinged, sliding, roller shutters, fire ratings)."
        + region_note
    )
    step_extract(settings)


if __name__ == "__main__":
    main()
