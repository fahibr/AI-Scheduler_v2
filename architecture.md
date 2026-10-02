# Architecture

**Product:** AI Door Scheduler  
**Style:** Modular Streamlit application + Azure OpenAI extraction agents  
**Primary entry:** `streamlit run main.py`

Related docs: [README.md](README.md) · [PRD.md](PRD.md) · [AGENTS.md](AGENTS.md) · [Design_system.md](Design_system.md)

---

## 1. Purpose

AI Door Scheduler turns architectural door-schedule **PDFs** into an editable structured table / Excel, maps **Door Mark → Door Type** against a project door list, and fills country **AAOS** import templates. Extraction uses Azure OpenAI vision (and optional analyze/refine passes) with format-specific prompts. Access is email-only, optionally backed by Supabase.

---

## 2. High-level context

```text
┌──────────────┐   email    ┌─────────────────┐  optional   ┌────────────┐
│  End user    │───────────▶│  Streamlit UI   │────────────▶│  Supabase  │
│  (browser)   │            │  main.py        │  users/     │  app_users │
└──────────────┘            └────────┬────────┘  logins     │  login_ev. │
                                     │                      └────────────┘
                     extract / map / aaos
                                     │
                     ┌───────────────┼───────────────┐
                     ▼               ▼               ▼
              ┌────────────┐  ┌────────────┐  ┌──────────────┐
              │ door_      │  │ Prompt-    │  │ Door_Schedule│
              │ scheduler/ │  │ version/   │  │ _Template/   │
              └─────┬──────┘  └────────────┘  └──────────────┘
                    │
                    ▼
              ┌────────────┐
              │ Azure      │
              │ OpenAI     │
              │ (vision +  │
              │  chat JSON)│
              └────────────┘
```

---

## 3. Logical layers

| Layer | Responsibility | Main code |
|-------|----------------|-----------|
| **Presentation** | Login, nav, extract UI, Admin, progress, downloads | `main.py`, `door_scheduler/auth.py` |
| **Application** | Orchestrate multi-PDF extract, merge tables, map Excel, AAOS export | `main.py` + `door_scheduler/__init__.py` exports |
| **Domain / extraction** | PDF→images, analyze, vision, text enrich, refine, normalize | `extractor.py`, `text_extract.py`, `pdf_images.py` |
| **Prompting** | Format → prompt pack, template render | `prompts.py`, `schedule_formats.py`, `Prompt-version/` |
| **Data shaping** | DataFrame/Excel columns, Door Mark join, AAOS mapping | `table.py`, `mapper.py`, `aaos_template.py` |
| **Identity / tenancy** | Email gate, roles, regions | `auth.py`, `regions.py`, `supabase_client.py`, `supabase/schema.sql` |
| **Config / secrets** | Azure, allowlists, Supabase, rate limit | `.env` (not committed) |

---

## 4. Runtime views

```text
                    ┌─ render_login_gate() ─┐
                    │  not authenticated    │
                    └──────────┬────────────┘
                               │ OK
                    ┌──────────▼────────────┐
                    │ Sidebar: settings +   │
                    │ nav + auth            │
                    └──────────┬────────────┘
               ┌───────────────┴───────────────┐
               ▼                               ▼
        app_view=extract                 app_view=admin
        (default)                        (admin only)
               │                               │
               ▼                               ▼
        step_extract()                   render_admin_panel()
        structured table                 users / regions / logins
        (± map / AAOS in full flows)
```

`streamlit_app.py` is a legacy/alternate entry; **prefer `main.py`**.

---

## 5. Extraction pipeline

### 5.1 Format selection

```text
UI radio: Elevation | Structured Table
        │
        ▼
schedule_formats.normalize_schedule_format()
        │
        ▼
prompts.resolve_prompt_version_for_format()
        │
        ├── elevation          → Prompt-version/elevation/
        └── structured_table   → Prompt-version/structured_table/
                                    (includes analyze_*)
```

`DOOR_PROMPT_VERSION` / `active_version.txt` are **fallbacks** when format resolution is not used; the UI radio is authoritative in `main.py`.

### 5.2 Per-PDF / per-page flow

```text
PDF bytes
  │
  ├─► pdf_images.pdf_bytes_to_page_images (DPI, page selection)
  └─► pdf_images.pdf_page_texts (embedded text layer)
        │
        ▼
  for each page:
        │
        ├─[structured_table only]─► analyze_structured_table_page
        │                              (layout JSON: normalized_rows,
        │                               extra_fields, source_row_text)
        │
        ├─► extract_doors_from_page_image
        │      • vision LLM + schema_hint
        │      • optional normalized_table JSON in user message
        │      • merge vision doors ∪ analysis rows
        │
        ├─► extract_doors_from_page_text
        │      • mark/size discovery from text layer
        │
        ├─► merge_door_records
        │      • structured_table: vision primary, text fills gaps
        │      • elevation: text primary, vision fills gaps
        │
        ├─► enrich_doors_from_page_text
        │
        └─► refine_dimensions_with_text_llm  (only if fields still missing)
              │
              ▼
        list[door dict]  (already _normalize_door’d)
  │
  ▼
build_structured_table() → DataFrame / Excel
```

### 5.3 Normalize boundary

All model JSON that becomes a table row should pass through `_normalize_door` (and helpers):

| Concern | Helper / rule |
|---------|----------------|
| Mark validity | `_DOOR_TYPE_RE` (e.g. `D10A`, `D10GA`, `FDGA`) |
| Qty by level | `_format_quantity_by_level` (dict/list/`"{'LEVEL…'}"` → readable text) |
| Item No. | `_extract_item_number` / promote from `extra_fields` |
| Materials / fire | `sanitize_materials`, `sanitize_fire_rating` |
| Description extras | `_enrich_description_from_extras` before normalize when folding analyze rows |

### 5.4 Progress & rate limiting

- `ProgressEvent` callbacks drive Streamlit progress (stage, page, tokens, ETA).
- `DOOR_API_MIN_INTERVAL` spaces Azure calls between pages.
- Token usage aggregated as `TokenUsage` (prompt + completion).

---

## 6. Data model (extraction record)

Logical door record (Python `dict` → table columns):

| Key | Excel header | Notes |
|-----|--------------|--------|
| `item_number` | Item No. | Table ITEM when present |
| `door_type` | Door Type | Schedule mark |
| `width_mm` / `height_mm` / `thickness_mm` | Width/Height/Thickness (mm) | Integers |
| `fire_rating` | Fire Rating | Per-door; avoid neighbour bleed |
| `door_material` / `frame_material` | Door/Frame Material | |
| `configuration` | Configuration | e.g. single leaf, roller shutter |
| `quantity_by_level` | Qty by Level | Display string |
| `quantity` | Quantity | Overall total |
| `location` | Location | Room/area |
| `description` | Description / Specification | |
| `remarks` | Remarks | |
| `page` | Page | |
| `source_pdf` | Source PDF | May join multiple sources on merge |

`table.build_structured_table` deduplicates primarily by **Door Type** (keeps higher-score row) and sorts by Item No. when present.

---

## 7. Post-extract workflows

### 7.1 Merge / corrected Excel

```text
Existing structured_df  +  new extract
        │
        ▼
merge_structured_tables(prefer_existing=True)
  • keep corrected Door Types
  • fill empty cells from new extract
  • append new Door Types

Optional: load_structured_excel() replaces/updates from user-edited XLSX
```

### 7.2 Step 2 — Map Excel

```text
Project door-list XLSX  +  structured table
        │
        ▼
mapper.map_excel_to_structured
  Door Mark  ↔  Door Type
        │
        ▼
Matched / unmatched grid → download
```

### 7.3 Step 3 — AAOS

```text
Country folder Door_Schedule_Template/<CC>/
        │
        ▼
aaos_template.suggest_column_mapping
        │
        ▼
User edits mapping → apply_aaos_mapping → aaos_workbook_bytes
```

---

## 8. Auth & regions architecture

```text
render_login_gate
  │
  ├─ Supabase configured?
  │     YES → lookup app_users (active) → set session role/regions
  │           record login_events
  │     NO  → ALLOWED_EMAILS / ALLOWED_EMAIL_DOMAINS
  │
  ▼
session: email, role, regions, access_all_regions, active_region
  │
  ├─ user  → exactly one region
  └─ admin → all regions or subset; sees Admin nav
```

| Store | Tables / purpose |
|-------|------------------|
| Supabase | `app_users`, `login_events` (`supabase/schema.sql`) |
| Env fallback | Allowlists only; Admin write paths need Supabase |

`supabase_client` uses the **service role** from the Streamlit process (server-side). Do not expose that key to the browser.

---

## 9. Prompt pack architecture

```text
Prompt-version/
  active_version.txt          # fallback default
  elevation/                  # vision + refine
  structured_table/           # analyze + vision + refine
  v1/ · v2/                   # legacy
```

`PromptBundle` (`prompts.py`):

- Always: `schema_hint`, `vision_*`, `refine_*`
- Structured table: `analyze_*` → `supports_table_analyze`
- Render helpers inject `page_number`, `text_block`, `schema_hint`, `normalized_table_block`, `marks`

`load_prompts` is LRU-cached; call `clear_prompt_cache()` after prompt file edits (also done on app start in `main`).

---

## 10. External dependencies

| Dependency | Role |
|------------|------|
| **Streamlit** | UI, session state, file upload/download |
| **Azure OpenAI** (`openai` SDK) | Vision + JSON chat completions |
| **PyMuPDF** | PDF rasterize + text extract |
| **Pillow** | Image encode for vision data URLs |
| **pandas / openpyxl** | Tables and Excel I/O |
| **python-dotenv** | Local `.env` |
| **supabase** (optional) | User registry + login audit |

---

## 11. Configuration surface

| Variable | Used by |
|----------|---------|
| `ENDPOINT_URL`, `AZURE_OPENAI_API_KEY`, `DEPLOYMENT_NAME`, `AZURE_OPENAI_API_VERSION` | Azure client |
| `DOOR_API_MIN_INTERVAL` | Inter-call sleep |
| `DOOR_PROMPT_VERSION` | Fallback prompt folder |
| `ALLOWED_EMAILS`, `ALLOWED_EMAIL_DOMAINS`, `ALLOWED_ADMIN_EMAILS` | Auth without Supabase |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Auth + Admin |

---

## 12. Key module reference

| Module | Public role |
|--------|-------------|
| `extractor.py` | `extract_doors_from_pdf(s)`, analyze/vision/refine, normalize |
| `text_extract.py` | Text-layer doors, `merge_door_records`, material/fire helpers |
| `pdf_images.py` | Page images, `parse_page_selection` |
| `table.py` | `build_structured_table`, Excel download/load/merge |
| `mapper.py` | Project Excel Door Mark mapping |
| `aaos_template.py` | Country templates + column mapping + workbook bytes |
| `prompts.py` | Versioned prompt load/render |
| `schedule_formats.py` | Format ids and UI labels |
| `auth.py` | Login gate, sidebar nav/auth, Admin panel |
| `regions.py` | Region codes/labels |
| `supabase_client.py` | Service client factory |

---

## 13. Failure & resilience patterns

| Failure | Behaviour |
|---------|-----------|
| Missing Azure env | Sidebar/main error; extract blocked |
| Bad page selection | Error before extract |
| Analyze JSON parse fail | Empty analysis; vision may still run |
| Vision returns non-list doors | Fall back to analysis rows when present |
| Refine not needed | Skipped when fields already filled |
| Supabase down / unset | Env allowlist login; Admin write features warn |

Human edit of the structured table is the primary recovery path for model mistakes.

---

## 14. Deployment shape (typical)

```text
Developer / VM / internal host
  ├── Python venv + requirements.txt
  ├── .env (secrets)
  ├── streamlit run main.py
  ├── network egress to Azure OpenAI
  └── optional egress to Supabase
```

No separate API server in the current architecture: Streamlit process hosts UI and calls Azure/Supabase directly.

---

## 15. Extension points

| Extend | How |
|--------|-----|
| New schedule format | New `Prompt-version/<id>/` + entry in `schedule_formats.py` + resolve mapping |
| New extracted field | Normalize + `table.COLUMN_*` + prompt schema_hint |
| New country | Drop template under `Door_Schedule_Template/<CC>/` |
| Stronger SSO | Replace email gate in `auth.py` (keep region model) |
| Job history | New persistence store; UI today is session-scoped |

---

## 16. Document control

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-10-03 | Initial architecture aligned with AI Door Scheduler V2 codebase |
