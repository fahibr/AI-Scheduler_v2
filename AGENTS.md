# AGENTS.md

Guidance for AI coding agents working in **AI Door Scheduler**.

Related docs: [README.md](README.md) · [PRD.md](PRD.md)

---

## Product in one paragraph

Internal Streamlit app that extracts door/shutter data from architectural schedule PDFs (Elevation or Structured Table) via Azure OpenAI, builds an editable Excel table, maps Door Mark → Door Type, and exports country AAOS import workbooks. Access is email-only with optional Supabase users/regions/admin.

**Run:** `streamlit run main.py` (prefer this over `streamlit_app.py`).

---

## Repo map

| Path | Own this when… |
|------|----------------|
| `main.py` | UI flow, session state, Steps 1–3 wiring, sidebar settings |
| `door_scheduler/extractor.py` | Analyze / vision / refine pipeline, `_normalize_door`, qty/item coercion |
| `door_scheduler/text_extract.py` | PDF text-layer parse, `merge_door_records`, materials/fire sanitizers used from text |
| `door_scheduler/table.py` | Structured DataFrame / Excel columns, dedupe, merge tables |
| `door_scheduler/prompts.py` | Load `Prompt-version/<id>/`; format → version resolve |
| `door_scheduler/schedule_formats.py` | Elevation vs Structured Table ids/labels |
| `door_scheduler/auth.py` / `regions.py` / `supabase_client.py` | Login, Admin, region grants |
| `door_scheduler/mapper.py` | Project Excel Door Mark mapping |
| `door_scheduler/aaos_template.py` | Country template discovery + column mapping |
| `door_scheduler/pdf_images.py` | PDF → page images, page selection parsing |
| `Prompt-version/` | LLM prompt text (prefer editing here over hardcoding prompts in Python) |
| `supabase/schema.sql` | DB schema / seed admin |
| `Door_Schedule_Template/` | Country AAOS XLSX templates (do not invent fake templates) |

---

## Architecture rules

1. **UI stays thin** — `main.py` orchestrates; business logic lives in `door_scheduler/`.
2. **Format drives prompts** — Step 1 radio sets `schedule_format` → `resolve_prompt_version_for_format()` → `elevation` or `structured_table`. Do not assume `active_version.txt` alone selects the live pack when the UI radio is present.
3. **Structured Table is two (or three) passes** — `analyze_structured_table_page` (optional restructure, no data loss) → `extract_doors_from_page_image` → text enrich / optional refine.
4. **Normalize at the boundary** — All vision/analyze/refine JSON should go through `_normalize_door` (and helpers like `_format_quantity_by_level`, `_extract_item_number`) before the table.
5. **Human-in-the-loop** — Always allow table edit / Excel round-trip before AAOS; do not remove that path.
6. **No secrets in git** — `.env`, API keys, Supabase service role stay local. Never print secrets in logs or commits.

---

## Coding conventions

- Python 3.12+, type hints on public functions, `from __future__ import annotations` where the package already uses it.
- Match existing style: concise functions, explicit `None` for missing fields, pandas `Int64` for optional numeric Excel columns.
- Prefer small, targeted changes. Do not refactor unrelated modules in the same change.
- Do not add markdown docs the user did not ask for (except when they request README/PRD/AGENTS).
- Avoid drive-by dependency bumps; update `requirements.txt` only when a new import is required.
- Streamlit: use `st.session_state` keys already established; bump `structured_editor_version` when replacing the edited table so widgets reset correctly.

### Structured table columns

Canonical keys → Excel headers live in `door_scheduler/table.py` (`COLUMN_MAP` / `COLUMN_ORDER`). If you add a field:

1. Add to normalize output in `extractor.py`
2. Add to `COLUMN_MAP` / `COLUMN_ORDER`
3. Update `Prompt-version/structured_table/schema_hint.txt` (and analyze/vision/refine as needed)
4. Mention in README/PRD only if the user wants docs updated

Current important fields: `item_number`, `door_type`, sizes, fire/materials/configuration, `quantity_by_level`, `quantity`, location, description, remarks, page, `source_pdf`.

### Door type marks

`_DOOR_TYPE_RE` in `extractor.py` must accept real schedule marks (e.g. `D10A`, `D10GA`, `FDGA`, `RS1`). If extraction drops valid marks, fix the regex / normalize path — do not silently widen to arbitrary words (WOOD, FRAME, etc.).

### Quantity by level

Never leave Python `str(dict)` in the table (e.g. `"{'LEVEL 2': 5}"`). Always format via `_format_quantity_by_level` **before** generic `str()`.

### Merge behaviour

`merge_door_records(primary, secondary)`: secondary is applied first; primary fills only empty fields (except longer description/remarks rules). Structured Table prefers vision as primary over text. Know this before “fixing” overwrite bugs.

---

## Prompt editing

| Format | Folder | Required files |
|--------|--------|----------------|
| Elevation | `Prompt-version/elevation/` | `schema_hint`, `vision_*`, `refine_*` |
| Structured Table | `Prompt-version/structured_table/` | above + `analyze_*` |

- Keep `manifest.json` `files` list in sync when adding prompt files.
- After prompt edits, call `clear_prompt_cache()` or restart Streamlit (`load_prompts` is cached).
- Prefer instructing the model in prompts; use Python sanitizers for hard invariants (fire bleed, materials, qty format, item coerce).

Legacy `v1/` / `v2/` remain loadable; do not delete without an explicit request.

---

## Auth & Supabase

- Login: email only. Supabase path uses `app_users` + `login_events` (`supabase/schema.sql`).
- Regular users: **exactly one** region. Admins: all regions or a subset.
- Admin UI: sidebar **Admin** — create users with email, first/last name, role, region.
- Fallback without Supabase: `ALLOWED_EMAILS` / `ALLOWED_EMAIL_DOMAINS` / `ALLOWED_ADMIN_EMAILS`.

Do not introduce password auth or client-side exposure of the service role key.

---

## Environment

Required for extract:

- `ENDPOINT_URL`, `AZURE_OPENAI_API_KEY`, `DEPLOYMENT_NAME`, `AZURE_OPENAI_API_VERSION`

Common optional:

- `DOOR_API_MIN_INTERVAL`, `DOOR_PROMPT_VERSION` (fallback only)
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- allowlist vars above

---

## What good changes look like

- **Extraction quality:** reproduce on a real PDF page when possible; prefer format-specific prompt tweaks + normalize helpers over giant prompt rewrites.
- **UI:** preserve progress, token counts, merge/replace, clear table, corrected Excel upload.
- **AAOS:** suggest mapping; user must be able to override before download.
- **Tests:** there is little automated coverage — prefer focused scripts or smoke imports; do not add heavy frameworks unless asked.

---

## Do not

- Commit `.env`, exports with customer data, or sample schedule PDFs that are gitignored.
- Bypass `_normalize_door` for vision/analyze output.
- Drop `extra_fields` / `source_row_text` in the structured-table analyze contract (no data loss).
- Change git config, force-push, or commit unless the user asks.
- Use the product to process data outside the user’s intended ASSA ABLOY workflow context in docs/examples without care for confidentiality.

---

## Quick verification

```powershell
.\.venv\Scripts\activate
pip install -r requirements.txt
# .env configured
streamlit run main.py
```

Smoke import:

```powershell
python -c "from door_scheduler.extractor import _normalize_door; print(_normalize_door({'door_type':'D10GA','item_number':'1.00','width_mm':900,'height_mm':2100},1))"
```

Expect `item_number` `1` and a non-`None` door record.

---

## When unsure

1. Read [PRD.md](PRD.md) for product intent and acceptance criteria.
2. Read [README.md](README.md) for setup and user-facing behaviour.
3. Prefer extending existing modules over creating parallel pipelines.
