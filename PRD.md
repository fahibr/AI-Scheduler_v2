# Product Requirements Document (PRD)

**Product:** AI Door Scheduler  
**Document type:** Product Requirements Document  
**Version:** 1.0  
**Status:** Current product (as implemented)  
**Primary entry:** `streamlit run main.py`  
**Related docs:** [README.md](README.md)

---

## 1. Overview

### 1.1 Problem

Architectural door schedules arrive as PDFs in inconsistent formats (elevation drawings with “New…” notes, or dense multi-header tables). Project teams must manually retype door types, sizes, materials, quantities, and notes into Excel door lists and country-specific **AAOS** import templates. That work is slow, error-prone, and hard to scale across ASSA ABLOY regions.

### 1.2 Solution

**AI Door Scheduler** is an internal Streamlit application that:

1. Extracts door / shutter data from schedule PDFs using Azure OpenAI vision (and supporting text passes).
2. Produces an editable structured table and Excel download.
3. Maps extracted door types onto a project door-list Excel.
4. Fills a country AAOS import workbook via configurable column mapping.

Access is email-only, with optional Supabase-backed users, regions, and admin tooling.

### 1.3 Goals

| Goal | Description |
|------|-------------|
| G1 | Reduce manual transcription from PDF door schedules into structured data |
| G2 | Support both elevation-style and structured-table schedule layouts |
| G3 | Preserve schedule information (sizes, fire rating, materials, qty by level, item numbers, descriptions) |
| G4 | Feed downstream AAOS import templates per country / region |
| G5 | Control access by email and region with lightweight admin operations |

### 1.4 Non-goals (out of scope for current product)

- Public self-service signup or password-based auth
- Fully automated AAOS upload into production ERP without human review
- CAD / Revit native file import
- Real-time multi-user collaborative editing of the same job
- Guaranteed 100% extraction accuracy on poor scans or non-schedule drawings

---

## 2. Users & personas

| Persona | Role in product | Needs |
|---------|-----------------|-------|
| **Scheduler / estimator** | Regular `user` | Extract PDFs for their region, edit table, map Excel, export AAOS |
| **Regional lead / power user** | Regular `user` or limited `admin` | Same as above; may handle multiple projects in one region |
| **Platform admin** | `admin` | Register users, assign regions, review login activity |
| **Developer / IT** | Ops | Configure Azure OpenAI, Supabase, `.env`, deploy Streamlit |

---

## 3. Access & regions

### 3.1 Authentication

| Requirement | Detail |
|-------------|--------|
| AR-1 | Login is **email-only** (no password) |
| AR-2 | With Supabase configured: only active rows in `app_users` may sign in |
| AR-3 | Without Supabase: allowlist via `ALLOWED_EMAILS` and/or `ALLOWED_EMAIL_DOMAINS` |
| AR-4 | Successful and failed login attempts may be recorded in `login_events` when Supabase is available |
| AR-5 | User can **Log out** from the sidebar |

### 3.2 Roles & regions

| Requirement | Detail |
|-------------|--------|
| AR-6 | Roles: `user`, `admin` |
| AR-7 | Regions: MY, CH, VN, TW, TH, HK, PH, SG, ID |
| AR-8 | Regular users must have **exactly one** granted region |
| AR-9 | Admins may have **all regions** or a granted subset |
| AR-10 | Active region is selected in-session when the user has access to more than one |

### 3.3 Admin

| Requirement | Detail |
|-------------|--------|
| AR-11 | Sidebar shows **Admin** only for admins |
| AR-12 | Admin can create/update users: email, first name, last name, role, region assignment |
| AR-13 | Admin can view recent login events (when Supabase is configured) |

---

## 4. Functional requirements

### 4.1 Navigation & shell

| ID | Requirement |
|----|-------------|
| FR-1 | App shell shows product title, Azure readiness status, and prompt/format cues in the sidebar |
| FR-2 | Authenticated users navigate **Extract** (main workflow); admins also navigate **Admin** |
| FR-3 | Raster DPI and page-selection controls are available in the sidebar |

### 4.2 Step 1 — Extract from PDF

| ID | Requirement |
|----|-------------|
| FR-10 | User must select **Door schedule format** before extract: Elevation or Structured Table |
| FR-11 | Format selection loads the matching prompt pack (`elevation` or `structured_table`) |
| FR-12 | User can upload one or more PDFs |
| FR-13 | User can restrict pages: `all`, ranges (`1-6`), lists (`1,8,10`), or mixed (`1-3,8,10`) per PDF |
| FR-14 | System converts selected pages to images and calls Azure OpenAI |
| FR-15 | **Elevation** path: vision extract (+ text enrich; refine when fields still missing) |
| FR-16 | **Structured Table** path: **analyze** (layout / optional restructure, no data loss) → vision extract → enrich / refine |
| FR-17 | Progress UI shows stage, page, elapsed time, ETA, and token input/completion usage |
| FR-18 | Results appear in an editable structured table |
| FR-19 | User can download structured Excel and optionally re-upload a corrected Excel |
| FR-20 | User can **Extract & merge** additional/split PDFs while keeping corrected rows for existing door types |
| FR-21 | User can clear the current table |

#### Extracted data fields (structured output)

| Field | Notes |
|-------|--------|
| Item No. | From ITEM / Item No. / No. when present (tables) |
| Door Type | Schedule mark (e.g. FD1, D10A, D10GA, RS1, FRS1, FDGA) |
| Width / Height / Thickness (mm) | Prefer leaf/opening sizes; reject vision-panel and shutter-box heights |
| Fire Rating | From that door’s own fire note; do not bleed neighbour FD ratings |
| Door Material / Frame Material | Leaf vs frame; finishes are not door material |
| Configuration | e.g. single leaf, double leaf, roller shutter |
| Qty by Level | Human-readable breakdown (e.g. `LEVEL 2: 5; LEVEL 3: 6`) |
| Quantity | Overall total when available |
| Location | Room/area only |
| Description / Specification | Full row / New… / specification text for that mark |
| Remarks | REMARKS / NOTE / schedule remarks |
| Page / Source PDF | Provenance |

### 4.3 Step 2 — Map project Excel

| ID | Requirement |
|----|-------------|
| FR-30 | User uploads a project door-list Excel |
| FR-31 | System joins project **Door Mark** to extracted **Door Type** |
| FR-32 | UI shows matched / unmatched rows for review and edit |
| FR-33 | User can download the complete mapped schedule |

### 4.4 Step 3 — AAOS template export

| ID | Requirement |
|----|-------------|
| FR-40 | User selects a country template from `Door_Schedule_Template/<COUNTRY>/` |
| FR-41 | System suggests column mapping (English and localized headers where applicable) |
| FR-42 | User can edit mapping and preview |
| FR-43 | User downloads an AAOS import workbook (Schedule + Data sheets as implemented by the template helper) |

Supported country codes today: **CH, HK, ID, MY, PH, SG, TH, TW, VN**.

---

## 5. User journeys

### 5.1 Primary — extract and export

```text
Sign in (email)
  → Select region (if applicable)
  → Extract: choose format → upload PDF(s) → set pages → Extract
  → Review / edit structured table (and/or download ↔ re-upload Excel)
  → Map Excel (Door Mark ↔ Door Type)
  → AAOS: choose country → confirm mapping → download import file
```

### 5.2 Split PDF schedule

```text
Extract PDF part 1 → correct table
  → Upload PDF part 2 → Extract & merge (keep corrections)
  → Continue to map / AAOS
```

### 5.3 Admin onboarding

```text
Admin signs in → Admin
  → Create user (email, name, role, region)
  → User signs in with email and works in granted region(s)
```

---

## 6. Prompt & AI behaviour requirements

| ID | Requirement |
|----|-------------|
| AI-1 | Prompts live under versioned folders in `Prompt-version/` |
| AI-2 | UI schedule format selects `elevation` or `structured_table` (not a silent global default alone) |
| AI-3 | Structured-table **analyze** must not drop unrecognized columns; preserve via `extra_fields` / `source_row_text` then fold into description/remarks when needed |
| AI-4 | One logical door row / mark per output object; do not merge different marks |
| AI-5 | Do not invent cell values that are not on the page |
| AI-6 | Qty-by-level dict/list outputs from the model must normalize to readable text in the app table |
| AI-7 | API calls respect configurable minimum interval (`DOOR_API_MIN_INTERVAL`) |

---

## 7. Non-functional requirements

| ID | Category | Requirement |
|----|----------|-------------|
| NFR-1 | Security | Secrets only in `.env` / environment; never commit keys |
| NFR-2 | Security | Prefer Supabase **service_role** server-side only (Streamlit process); enable RLS on tables as per schema |
| NFR-3 | Privacy | Login audit stores email and optional user-agent; avoid unnecessary PII |
| NFR-4 | Reliability | Human-in-the-loop edit before AAOS download is required product behaviour |
| NFR-5 | Observability | Surface token usage and extraction progress in UI |
| NFR-6 | Portability | Python 3.12+; Windows-friendly setup documented in README |
| NFR-7 | Extensibility | New countries via new folders under `Door_Schedule_Template/` |
| NFR-8 | Extensibility | New/edited prompts via new `Prompt-version/<id>/` folders |

---

## 8. Technical architecture (summary)

```text
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Streamlit  │────▶│  door_scheduler  │────▶│  Azure OpenAI   │
│  main.py    │     │  extract/table/  │     │  vision + LLM   │
└──────┬──────┘     │  map/aaos        │     └─────────────────┘
       │            └────────┬─────────┘
       │                     │
       ▼                     ▼
┌─────────────┐     ┌──────────────────┐
│  Supabase   │     │ Prompt-version/  │
│  users /    │     │ elevation |      │
│  logins     │     │ structured_table │
└─────────────┘     └──────────────────┘
```

| Layer | Components |
|-------|------------|
| UI | `main.py` (login, Extract, Admin, Steps 1–3) |
| Auth | `auth.py`, `regions.py`, `supabase_client.py` |
| Extraction | `pdf_images.py`, `extractor.py`, `text_extract.py`, `prompts.py`, `schedule_formats.py` |
| Data shaping | `table.py`, `mapper.py`, `aaos_template.py` |
| Data store | Optional Supabase (`app_users`, `login_events`) |
| Config | `.env` (Azure, allowlists, Supabase, rate limit, prompt fallback) |

---

## 9. Success metrics

| Metric | Target / measure |
|--------|------------------|
| Time to structured table | Materially faster than full manual transcription for typical schedules |
| Field coverage | Item No. (when present), door type, sizes, materials, fire, qty, description populated when visible on page |
| Edit rate | Corrections expected; product success = fewer corrections over time, not zero |
| Downstream readiness | Mapped Excel and AAOS file accepted by regional import process after human review |
| Access control | Only allowed emails / active users can run extractions; region rules enforced |

---

## 10. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Model misreads dense / low-quality PDFs | Human edit table; DPI control; page selection; text-layer enrich |
| Cross-row bleed (wrong fire/qty/description) | Format-specific prompts; sanitize helpers; structured-table analyze isolation |
| Same door mark on multiple rows | Deduplicate by door type in table build; merge keeps richer row — operators should verify Item No. / section context |
| Token / cost growth | Progress + token counters; page limits; API min interval |
| Prompt drift | Versioned prompt folders; format radio selects pack |

---

## 11. Acceptance criteria (release checklist)

- [ ] Email login works with Supabase **or** `.env` allowlist  
- [ ] Admin can create a user with first/last name and one region  
- [ ] Elevation extract produces editable table for a sample elevation PDF  
- [ ] Structured Table extract runs analyze + extract; Item No. and Qty by Level appear when present on the PDF  
- [ ] Split PDF merge keeps corrected door types and adds new ones  
- [ ] Step 2 Door Mark mapping produces matched/unmatched visibility  
- [ ] Step 3 country template download succeeds for at least one region (e.g. MY)  
- [ ] No secrets committed; README setup is sufficient for a new engineer  

---

## 12. Future considerations (not committed)

- Stronger SSO (Microsoft Entra ID) instead of email-only gate  
- Persist extraction jobs / history per user and region  
- Confidence scores or cell-level provenance in the UI  
- Deduplicate / retain rows by Item No. + Door Type when marks repeat across sections  
- Automated regression set of golden PDFs per format and country  

---

## 13. Document control

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-10-03 | Initial PRD aligned with implemented AI Door Scheduler V2 |
