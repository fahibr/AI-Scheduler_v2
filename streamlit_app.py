"""Streamlit app: extract door schedule from PDF, then map to Excel for a complete schedule."""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st

from door_scheduler import (
    ProgressEvent,
    TokenUsage,
    aaos_workbook_bytes,
    apply_aaos_mapping,
    build_structured_table,
    country_display_name,
    dataframe_to_excel_bytes,
    extract_doors_from_pdfs,
    find_template_path,
    list_countries,
    list_country_templates,
    load_door_excel,
    load_schedule_columns,
    map_excel_to_structured,
    mapped_dataframe_to_excel_bytes,
    mapping_coverage,
    mapping_dict_from_editor,
    mapping_table_for_editor,
    parse_page_selection,
    suggest_column_mapping,
)

SAMPLE_PDF = Path(__file__).resolve().parent / (
    "CombinePDF_450 - WP03-MTB_TD_DOOR SCHEDULE (GENERAL) 14.pdf"
)
SAMPLE_EXCEL = Path(r"c:\Users\fahibr\Downloads\Binder4_door_schedule.xlsx")
if not SAMPLE_EXCEL.exists():
    SAMPLE_EXCEL = Path(__file__).resolve().parent / "Binder4_door_schedule.xlsx"

st.set_page_config(
    page_title="AI Door Scheduler",
    page_icon=":material/door_front:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      :root {
        --ink: #0f2744;
        --steel: #1f4e79;
        --mist: #e8eef5;
        --accent: #c45c26;
        --active: #0b3d6e;
      }
      .stApp {
        background:
          radial-gradient(1200px 500px at 10% -10%, #d9e6f5 0%, transparent 55%),
          linear-gradient(180deg, #f7f9fc 0%, #eef2f7 100%);
      }
      h1, h2, h3 { color: var(--ink) !important; letter-spacing: -0.02em; }
      div[data-testid="stMetric"] {
        background: white;
        border: 1px solid #d5dee9;
        border-radius: 10px;
        padding: 0.6rem 0.9rem;
      }
      /* High-visibility step navigation */
      .step-nav-banner {
        background: #0f2744;
        color: #ffffff;
        border-radius: 12px;
        padding: 0.85rem 1.1rem;
        margin: 0.25rem 0 0.85rem 0;
        font-size: 0.95rem;
        font-weight: 600;
        letter-spacing: 0.01em;
      }
      .step-nav-banner span.muted {
        color: #b8c7d9;
        font-weight: 500;
      }
      /* Large, high-contrast buttons in the main step row */
      section.main div[data-testid="stHorizontalBlock"] button {
        min-height: 3.4rem !important;
        font-size: 1.05rem !important;
        font-weight: 700 !important;
        border-width: 2px !important;
      }
      section.main div[data-testid="stHorizontalBlock"] button[kind="secondary"] {
        background-color: #ffffff !important;
        border-color: #0f2744 !important;
        color: #0f2744 !important;
      }
      section.main div[data-testid="stHorizontalBlock"] button[kind="secondary"]:hover {
        background-color: #e8eef5 !important;
        border-color: #c45c26 !important;
      }
      section.main div[data-testid="stHorizontalBlock"] button[kind="primary"] {
        background-color: #0b3d6e !important;
        border-color: #0b3d6e !important;
        color: #ffffff !important;
        box-shadow: 0 2px 8px rgba(15, 39, 68, 0.28);
      }
      /* Sidebar step jump links */
      section[data-testid="stSidebar"] button {
        font-weight: 600 !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def _init_state() -> None:
    defaults = {
        "doors_df": None,
        "page_images": [],
        "status_log": [],
        "source_name": None,
        "excel_df": None,
        "excel_name": None,
        "mapped_df": None,
        "map_summary": None,
        "doors_editor_version": 0,
        "mapped_editor_version": 0,
        "aaos_country": None,
        "aaos_template_path": None,
        "aaos_columns": None,
        "aaos_column_mapping": None,
        "aaos_df": None,
        "aaos_editor_version": 0,
        "aaos_map_editor_version": 0,
        "extraction_elapsed": None,
        "token_usage": None,
        "current_step": 1,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


STEP_LABELS = {
    1: "Step 1 · Extract from PDF",
    2: "Step 2 · Map Excel",
    3: "Step 3 · AAOS Template",
}


def _render_step_nav() -> None:
    """Large, high-contrast step buttons (replaces hard-to-see tabs)."""
    current = int(st.session_state.get("current_step") or 1)
    st.markdown(
        f'<div class="step-nav-banner">'
        f'Workflow · <span class="muted">you are on</span> {STEP_LABELS.get(current, current)}'
        f"</div>",
        unsafe_allow_html=True,
    )
    cols = st.columns(3, gap="medium")
    specs = [
        (1, "1 · Extract PDF", ":material/picture_as_pdf:"),
        (2, "2 · Map Excel", ":material/table_chart:"),
        (3, "3 · AAOS Template", ":material/file_upload:"),
    ]
    for col, (step, label, icon) in zip(cols, specs):
        with col:
            clicked = st.button(
                label,
                key=f"nav_step_{step}",
                type="primary" if current == step else "secondary",
                use_container_width=True,
                icon=icon,
            )
            if clicked and current != step:
                st.session_state.current_step = step
                st.rerun()
    st.divider()


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}h {mins:02d}m {secs:02d}s"
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def _run_extraction(
    pdf_files: list[tuple[str, bytes]],
    dpi: int,
    page_selection: str,
) -> None:
    progress = st.progress(0, text="Starting…")
    status = st.empty()
    timing = st.empty()
    log_box = st.empty()
    logs: list[str] = []
    started = time.monotonic()
    page_done_times: list[float] = []
    last_page_stamp = started
    total_pages = 0
    prompt_total = 0
    completion_total = 0

    def on_progress(event: ProgressEvent) -> None:
        nonlocal last_page_stamp, total_pages, prompt_total, completion_total
        now = time.monotonic()
        elapsed = now - started
        if event.total:
            total_pages = event.total
        prompt_total = event.prompt_tokens_total
        completion_total = event.completion_tokens_total

        if event.stage == "page_done":
            page_done_times.append(now - last_page_stamp)
            last_page_stamp = now

        current = event.current or 0
        total = event.total or max(total_pages, 1)
        if event.stage == "page_done":
            completed = current
        elif event.stage in {"start", "file", "convert"}:
            completed = current
        else:
            completed = max(0, current - 1)
        fraction = 1.0 if event.stage == "done" else min(0.99, completed / max(total, 1))

        avg = (
            sum(page_done_times) / len(page_done_times) if page_done_times else None
        )
        remaining_pages = max(0, total - completed)
        eta_text = "—"
        if avg is not None and remaining_pages > 0 and event.stage != "done":
            eta_text = _format_duration(avg * remaining_pages)
        elif event.stage == "done":
            eta_text = "0s"

        stamp = _format_duration(elapsed)
        line = f"[{stamp}] {event.message}"
        logs.append(line)

        progress.progress(
            fraction,
            text=f"{event.message}  |  {completed}/{total} pages",
        )
        status.info(event.message)
        timing.markdown(
            f"**Elapsed:** {_format_duration(elapsed)}  ·  "
            f"**ETA:** {eta_text}  ·  "
            f"**Pages:** {completed}/{total}"
            + (
                f"  ·  **Avg/page:** {_format_duration(avg)}"
                if avg is not None
                else ""
            )
            + f"  \n**Tokens — input:** {prompt_total:,}  ·  "
            f"**completion:** {completion_total:,}  ·  "
            f"**total:** {prompt_total + completion_total:,}"
            + (
                f"  ·  **this page in/out:** "
                f"{event.prompt_tokens:,}/{event.completion_tokens:,}"
                if event.stage == "page_done" and (event.prompt_tokens or event.completion_tokens)
                else ""
            )
        )
        log_box.code("\n".join(logs[-20:]), language=None)

    doors, pages, usage = extract_doors_from_pdfs(
        pdf_files,
        dpi=dpi,
        page_selection=page_selection,
        on_progress=on_progress,
    )
    df = build_structured_table(doors)
    names = ", ".join(name for name, _ in pdf_files)
    elapsed_total = time.monotonic() - started
    st.session_state.doors_df = df
    st.session_state.page_images = pages
    st.session_state.status_log = logs
    st.session_state.extraction_elapsed = elapsed_total
    st.session_state.token_usage = usage
    st.session_state.source_name = names
    st.session_state.mapped_df = None
    st.session_state.map_summary = None
    st.session_state.aaos_df = None
    st.session_state.doors_editor_version += 1
    st.session_state.mapped_editor_version += 1
    progress.progress(1.0, text="Done")
    timing.markdown(
        f"**Elapsed:** {_format_duration(elapsed_total)}  ·  "
        f"**ETA:** 0s  ·  "
        f"**Pages:** {len(pages)}/{len(pages)}  \n"
        f"**Tokens — input:** {usage.prompt_tokens:,}  ·  "
        f"**completion:** {usage.completion_tokens:,}  ·  "
        f"**total:** {usage.total_tokens:,}"
    )
    status.success(
        f"Extracted {len(df)} door type(s) from {len(pdf_files)} PDF(s) "
        f"across {len(pages)} page(s) in {_format_duration(elapsed_total)} "
        f"(tokens in {usage.prompt_tokens:,} / out {usage.completion_tokens:,})."
    )


def _run_mapping(excel_bytes: bytes, excel_name: str) -> None:
    excel_df = load_door_excel(excel_bytes)
    # Use the latest edited Step 1 table for mapping
    mapped_df, summary = map_excel_to_structured(excel_df, st.session_state.doors_df)
    st.session_state.excel_df = excel_df
    st.session_state.excel_name = excel_name
    st.session_state.mapped_df = mapped_df
    st.session_state.map_summary = summary
    st.session_state.mapped_editor_version += 1


def _refresh_map_summary(mapped_df) -> None:
    if mapped_df is None or mapped_df.empty or "Match Status" not in mapped_df.columns:
        return
    matched = int((mapped_df["Match Status"] == "Matched").sum())
    unmatched = int((mapped_df["Match Status"] == "Unmatched").sum())
    matched_types = (
        int(mapped_df.loc[mapped_df["Match Status"] == "Matched", "Door Type"].nunique())
        if "Door Type" in mapped_df.columns and matched
        else 0
    )
    summary = dict(st.session_state.map_summary or {})
    summary.update(
        {
            "excel_rows": len(mapped_df),
            "matched_rows": matched,
            "unmatched_rows": unmatched,
            "matched_door_types": matched_types,
        }
    )
    st.session_state.map_summary = summary


_init_state()

st.title("AI Door Scheduler")
st.caption(
    "Step 1: extract from PDF → Step 2: map Excel Door Mark to Door Type → "
    "Step 3: map complete schedule columns into the country AAOS import template. "
    "Tables are editable before Excel download."
)

_render_step_nav()

# Sidebar always available (Step 1 controls)
with st.sidebar:
    st.header("Step 1 · PDF")
    uploaded = st.file_uploader(
        "Door schedule PDF(s)",
        type=["pdf"],
        accept_multiple_files=True,
        key="pdf_uploader",
    )
    use_sample = st.checkbox(
        "Include sample PDF in project folder",
        value=(not uploaded) and SAMPLE_PDF.exists(),
        key="use_sample_pdf",
    )
    dpi = st.slider("Image DPI (PDF → image)", min_value=120, max_value=300, value=200, step=20)
    page_selection = st.text_input(
        "Pages to extract",
        value="all",
        help=(
            "Leave as all for every page. "
            "Use a range like 1-6, a list like 1,8,10, or combine like 1-3,8,10. "
            "Selection is applied to each uploaded PDF."
        ),
        key="page_selection",
    )
    try:
        from door_scheduler import list_prompt_versions, resolve_active_version

        active_prompt = resolve_active_version()
        st.caption(
            f"Prompt version: `{active_prompt}` "
            f"(available: {', '.join(list_prompt_versions()) or 'none'})"
        )
    except Exception:  # noqa: BLE001
        st.caption("Prompt version: unavailable")
    run = st.button("Extract door schedule", type="primary", use_container_width=True)

    st.divider()
    st.caption("Jump to step")
    for step, label in STEP_LABELS.items():
        if st.button(
            label,
            key=f"sidebar_step_{step}",
            type="primary" if st.session_state.current_step == step else "secondary",
            use_container_width=True,
        ):
            if st.session_state.current_step != step:
                st.session_state.current_step = step
                st.rerun()

if st.session_state.current_step == 1:
    if run:
        pdf_files: list[tuple[str, bytes]] = []
        if uploaded:
            for file in uploaded:
                pdf_files.append((file.name, file.getvalue()))
        if use_sample and SAMPLE_PDF.exists():
            sample_name = SAMPLE_PDF.name
            if sample_name not in {name for name, _ in pdf_files}:
                pdf_files.append((sample_name, SAMPLE_PDF.read_bytes()))

        if not pdf_files:
            st.error("Please upload one or more PDFs or enable the sample file.")
        else:
            try:
                selected_pages = parse_page_selection(page_selection)
                if selected_pages is not None:
                    st.caption(
                        f"Page selection: {', '.join(str(p) for p in selected_pages)} "
                        f"(applied to each PDF)"
                    )
                with st.spinner("Running door extraction agent…"):
                    _run_extraction(pdf_files, dpi=dpi, page_selection=page_selection)
            except ValueError as exc:
                st.error(f"Invalid page selection: {exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Extraction failed: {exc}")

    df = st.session_state.doors_df
    pages = st.session_state.page_images

    if df is not None:
        st.subheader("Structured door table")
        st.caption("Edit cells directly. Add or remove rows as needed — download uses your edited table.")
        edited_doors = st.data_editor(
            df,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            key=f"doors_editor_{st.session_state.doors_editor_version}",
        )
        st.session_state.doors_df = edited_doors
        df = edited_doors

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Door types", len(df))
        c2.metric(
            "With full size",
            int(df[["Width (mm)", "Height (mm)", "Thickness (mm)"]].notna().all(axis=1).sum())
            if len(df) and all(
                col in df.columns for col in ("Width (mm)", "Height (mm)", "Thickness (mm)")
            )
            else 0,
        )
        c3.metric("Pages scanned", len(pages))
        elapsed = st.session_state.get("extraction_elapsed")
        c4.metric(
            "Time taken",
            _format_duration(elapsed) if elapsed is not None else "—",
        )
        usage = st.session_state.get("token_usage")
        if isinstance(usage, TokenUsage):
            c5.metric("Tokens in / out", f"{usage.prompt_tokens:,} / {usage.completion_tokens:,}")
        else:
            c5.metric("Tokens in / out", "—")

        excel_bytes = dataframe_to_excel_bytes(df)
        pdf_count = (
            int(df["Source PDF"].nunique())
            if "Source PDF" in df.columns and df["Source PDF"].notna().any()
            else 1
        )
        safe_stem = "door_schedule_multi" if pdf_count > 1 else Path(
            st.session_state.source_name or "door_schedule"
        ).stem
        if "," in safe_stem:
            safe_stem = "door_schedule_combined"
        st.download_button(
            label="Download structured Excel",
            data=excel_bytes,
            file_name=f"{safe_stem}_structured.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="dl_structured",
        )

        with st.expander("Page images used for extraction", expanded=False):
            for page in pages:
                label = f"Page {page.page_number}"
                if page.source_name:
                    label = f"{page.source_name} · {label}"
                st.markdown(f"**{label}**")
                st.image(page.image, width="stretch")

        if st.session_state.status_log:
            with st.expander("Extraction log (per page)", expanded=False):
                elapsed = st.session_state.get("extraction_elapsed")
                if elapsed is not None:
                    st.caption(f"Total time: {_format_duration(elapsed)}")
                usage = st.session_state.get("token_usage")
                if isinstance(usage, TokenUsage):
                    st.caption(
                        f"Tokens — input: {usage.prompt_tokens:,} · "
                        f"completion: {usage.completion_tokens:,} · "
                        f"total: {usage.total_tokens:,}"
                    )
                st.code("\n".join(st.session_state.status_log), language=None)
    else:
        st.info(
            "Upload one or more door schedule PDFs (or use the sample), set pages "
            "(e.g. `1-6` or `1,8,10`), then click **Extract door schedule**."
        )

elif st.session_state.current_step == 2:
    st.subheader("Map Excel Door Mark → extracted Door Type")
    st.write(
        "Upload a door schedule Excel file. Each **Door Mark** is joined to the Step 1 "
        "**Door Type** so width, height, thickness, and materials complete the schedule."
    )

    if st.session_state.doors_df is None:
        st.warning("Complete Step 1 first so there is a structured door table to map against.")
    else:
        excel_upload = st.file_uploader(
            "Door schedule Excel",
            type=["xlsx", "xls"],
            key="excel_uploader",
        )
        use_sample_excel = st.checkbox(
            "Use sample Excel (Binder4_door_schedule.xlsx)",
            value=excel_upload is None and SAMPLE_EXCEL.exists(),
            key="use_sample_excel",
        )
        map_btn = st.button("Map to complete door schedule", type="primary")

        if map_btn:
            excel_bytes: bytes | None = None
            excel_name = ""
            if excel_upload is not None:
                excel_bytes = excel_upload.getvalue()
                excel_name = excel_upload.name
            elif use_sample_excel and SAMPLE_EXCEL.exists():
                excel_bytes = SAMPLE_EXCEL.read_bytes()
                excel_name = SAMPLE_EXCEL.name
            else:
                st.error("Please upload an Excel file or enable the sample Excel.")

            if excel_bytes:
                try:
                    with st.spinner("Mapping Door Mark to Door Type…"):
                        _run_mapping(excel_bytes, excel_name)
                    st.success(
                        f"Mapped {st.session_state.map_summary['matched_rows']} of "
                        f"{st.session_state.map_summary['excel_rows']} Excel rows."
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Mapping failed: {exc}")

        mapped = st.session_state.mapped_df
        summary = st.session_state.map_summary
        if mapped is not None and summary is not None:
            show_unmatched_only = st.checkbox("Show unmatched rows only", value=False)
            st.caption(
                "Edit cells before download. Add/remove rows when viewing the full table. "
                "Unmatched-only view is editable for those rows (row add/remove disabled)."
            )

            editor_version = st.session_state.mapped_editor_version
            if show_unmatched_only and "Match Status" in mapped.columns:
                view_df = mapped[mapped["Match Status"] == "Unmatched"].copy()
                edited_view = st.data_editor(
                    view_df,
                    width="stretch",
                    hide_index=True,
                    num_rows="fixed",
                    key=f"mapped_editor_unmatched_{editor_version}",
                )
                updated = mapped.copy()
                updated.loc[edited_view.index, edited_view.columns] = edited_view
                st.session_state.mapped_df = updated
                mapped = updated
            else:
                edited_mapped = st.data_editor(
                    mapped,
                    width="stretch",
                    hide_index=True,
                    num_rows="dynamic",
                    key=f"mapped_editor_full_{editor_version}",
                )
                st.session_state.mapped_df = edited_mapped
                mapped = edited_mapped

            _refresh_map_summary(mapped)
            summary = st.session_state.map_summary

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Excel rows", summary["excel_rows"])
            m2.metric("Matched", summary["matched_rows"])
            m3.metric("Unmatched", summary["unmatched_rows"])
            m4.metric("Matched door types", summary["matched_door_types"])

            mapped_bytes = mapped_dataframe_to_excel_bytes(mapped)
            stem = Path(st.session_state.excel_name or "door_schedule").stem
            st.download_button(
                label="Download complete door schedule Excel",
                data=mapped_bytes,
                file_name=f"{stem}_complete_mapped.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                key="dl_mapped",
            )
        elif st.session_state.doors_df is not None:
            st.info(
                "Upload Excel (or use the sample), then click **Map to complete door schedule**."
            )

elif st.session_state.current_step == 3:
    st.subheader("Map complete door schedule → AAOS country template")
    st.write(
        "Choose a country template under `Door_Schedule_Template` "
        "(supports EN and localized headers such as CH/TW). "
        "Review suggested column mapping, edit if needed, then download the AAOS import file."
    )

    if st.session_state.mapped_df is None:
        st.warning("Complete Step 2 first so there is a complete door schedule to map.")
    else:
        templates = list_country_templates()
        if not templates:
            st.error("No country folders found under Door_Schedule_Template.")
        else:
            labels = [t["label"] for t in templates]
            codes = [t["code"] for t in templates]
            default_idx = codes.index("MY") if "MY" in codes else 0
            selected_label = st.selectbox(
                "Country template",
                labels,
                index=default_idx,
                help="Templates found in Door_Schedule_Template/<COUNTRY>/",
            )
            meta = next(t for t in templates if t["label"] == selected_label)
            country = meta["code"]
            template_path = Path(meta["path"])
            st.caption(
                f"Template: `{meta['file_name']}` · "
                f"{meta['column_count']} Schedule columns · "
                f"{country_display_name(country)}"
            )

            source_cols = list(st.session_state.mapped_df.columns)
            aaos_cols = meta["columns"] or load_schedule_columns(template_path)

            if (
                st.session_state.aaos_country != country
                or st.session_state.aaos_column_mapping is None
            ):
                st.session_state.aaos_country = country
                st.session_state.aaos_template_path = str(template_path)
                st.session_state.aaos_columns = aaos_cols
                st.session_state.aaos_column_mapping = suggest_column_mapping(
                    source_cols, aaos_cols, country=country
                )
                st.session_state.aaos_df = None
                st.session_state.aaos_map_editor_version += 1
                st.session_state.aaos_editor_version += 1

            coverage = mapping_coverage(st.session_state.aaos_column_mapping)
            m1, m2, m3 = st.columns(3)
            m1.metric("AAOS columns", coverage["total"])
            m2.metric("Auto-mapped", coverage["mapped_count"])
            m3.metric("Unmapped", coverage["unmapped_count"])
            if coverage["unmapped"]:
                with st.expander("Unmapped AAOS columns", expanded=False):
                    st.write(", ".join(coverage["unmapped"]))

            st.markdown("##### Column mapping")
            st.caption(
                "Suggested mapping uses cross-country aliases (including Chinese headers). "
                "Adjust Source Column as needed, or leave blank to skip."
            )
            map_table = mapping_table_for_editor(st.session_state.aaos_column_mapping)
            source_options = [""] + source_cols
            edited_map = st.data_editor(
                map_table,
                width="stretch",
                hide_index=True,
                num_rows="fixed",
                disabled=["AAOS Column", "Field"],
                column_config={
                    "AAOS Column": st.column_config.TextColumn("AAOS Column", disabled=True),
                    "Field": st.column_config.TextColumn(
                        "Logical field",
                        disabled=True,
                        help="Normalized field used for multi-country alias matching.",
                    ),
                    "Source Column": st.column_config.SelectboxColumn(
                        "Source Column",
                        options=source_options,
                        required=False,
                    ),
                },
                key=f"aaos_map_editor_{st.session_state.aaos_map_editor_version}",
            )
            st.session_state.aaos_column_mapping = mapping_dict_from_editor(edited_map)

            col_a, col_b = st.columns([1, 1])
            with col_a:
                apply_btn = st.button("Apply AAOS mapping", type="primary")
            with col_b:
                if st.button("Reset suggested mapping"):
                    st.session_state.aaos_column_mapping = suggest_column_mapping(
                        source_cols, aaos_cols, country=country
                    )
                    st.session_state.aaos_map_editor_version += 1
                    st.rerun()

            if apply_btn:
                try:
                    aaos_df = apply_aaos_mapping(
                        st.session_state.mapped_df,
                        st.session_state.aaos_column_mapping,
                        aaos_cols,
                    )
                    st.session_state.aaos_df = aaos_df
                    st.session_state.aaos_editor_version += 1
                    st.success(
                        f"Mapped {len(aaos_df)} row(s) into "
                        f"{country_display_name(country)} ({country}) AAOS Schedule format."
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"AAOS mapping failed: {exc}")

            aaos_df = st.session_state.aaos_df
            if aaos_df is not None:
                st.markdown("##### AAOS Schedule preview (editable)")
                edited_aaos = st.data_editor(
                    aaos_df,
                    width="stretch",
                    hide_index=True,
                    num_rows="dynamic",
                    key=f"aaos_editor_{st.session_state.aaos_editor_version}",
                )
                st.session_state.aaos_df = edited_aaos
                aaos_df = edited_aaos

                a1, a2, a3 = st.columns(3)
                a1.metric("AAOS rows", len(aaos_df))
                mapped_count = sum(
                    1 for v in st.session_state.aaos_column_mapping.values() if v
                )
                a2.metric("Mapped columns", mapped_count)
                a3.metric("Country", f"{country} · {country_display_name(country)}")

                try:
                    out_bytes = aaos_workbook_bytes(
                        aaos_df,
                        Path(st.session_state.aaos_template_path or template_path),
                    )
                    st.download_button(
                        label="Download AAOS import Excel",
                        data=out_bytes,
                        file_name=f"AAOS_Door_Schedule_Import_{country}.xlsx",
                        mime=(
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                        type="primary",
                        key="dl_aaos",
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not build AAOS workbook: {exc}")
            else:
                st.info("Adjust the column map, then click **Apply AAOS mapping**.")
