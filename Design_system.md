# Design System

**Product:** AI Door Scheduler  
**UI stack:** Streamlit (wide layout, expanded sidebar)  
**Audience:** Internal ASSA ABLOY schedulers / admins (task-focused, not marketing)

This document describes the **visual and interaction system** used by the app today, plus rules for keeping future UI changes consistent. Prefer clarity and scanability over decoration.

Related docs: [README.md](README.md) · [PRD.md](PRD.md) · [AGENTS.md](AGENTS.md)

---

## 1. Design principles

| Principle | Practice |
|-----------|----------|
| **Task first** | One clear job per screen region (login, extract, admin). Avoid dashboard clutter in the first viewport. |
| **Progressive disclosure** | Primary controls visible; logs, source lists, and long help live in expanders or captions. |
| **Human-in-the-loop** | Extraction results are always editable; warnings remind users to review before AAOS. |
| **Status is visible** | Azure readiness, auth backend, region, tokens, and errors use Streamlit status components—not custom toast stacks. |
| **Wide working surface** | `layout="wide"` for tables and multi-column toolbars; sidebar holds settings and identity. |
| **Trust over brand theater** | Default Streamlit theme is acceptable. Do not introduce decorative purple gradients, glow, or marketing-hero layouts on operational screens. |

---

## 2. Page shell

### 2.1 Config

```python
st.set_page_config(
    page_title="AI Door Scheduler",
    page_icon="🚪",
    layout="wide",
    initial_sidebar_state="expanded",
)
```

| Token | Value | Notes |
|-------|--------|------|
| Page title | `AI Door Scheduler` | Browser tab |
| Page icon | Door emoji | Lightweight product cue |
| Layout | `wide` | Tables need horizontal space |
| Sidebar | Expanded by default | Settings + nav + identity |

### 2.2 Information architecture

```text
┌─ Sidebar ─────────────────────────────────┐  ┌─ Main ────────────────────────────┐
│ Product title + tagline                   │  │ Page title                         │
│ Azure status                              │  │ Caption (product or admin intent)  │
│ Format / prompt cue                       │  │                                    │
│ DPI + page selection                      │  │ Primary workflow content           │
│ ───                                       │  │ (Extract | Admin)                   │
│ Navigation (Extract / Admin)              │  │                                    │
│ ───                                       │  │                                    │
│ Signed in · role · regions · Log out      │  │                                    │
└───────────────────────────────────────────┘  └────────────────────────────────────┘
```

**Views**

| View | Who | Main title | Purpose |
|------|-----|------------|---------|
| Login gate | Anonymous | AI Door Scheduler | Email-only continue |
| Extract | Authenticated | AI Door Scheduler | PDF extract → table |
| Admin | Admin only | Admin | Users, regions, logins |

Do not bury Admin in a nested menu; use sidebar radio **Navigation** (Extract / Admin).

---

## 3. Typography hierarchy

Streamlit’s default type ramp. Use levels consistently:

| Level | Streamlit API | Use for |
|-------|---------------|---------|
| Page title | `st.title` | One per main view |
| Section | `st.subheader` | Major workflow blocks (e.g. “Extract door types from PDF”) |
| Subsection | `st.markdown("#### …")` | Nested blocks (Register user, Structured door schedule) |
| Micro-heading | `st.markdown("##### …")` | Inline panels (Extraction progress) |
| Body | `st.markdown` | Short instructional copy (1–3 sentences) |
| Secondary | `st.caption` | Help text, region note, auth footnotes, API interval |
| Monospace | `` `code` `` in markdown, or `st.code` | Prompt folder ids, deployment names, extraction log |

**Copy rules**

- Prefer plain language: “Door schedule format”, not jargon-first headlines.
- Help that depends on format stays as `st.caption` under the control.
- Bold sparingly for **field names** and **counts** (`**26** door type(s)`).

---

## 4. Color & feedback

Use Streamlit semantic components—do not invent a parallel color palette unless introducing `.streamlit/config.toml` later.

| Intent | Component | When |
|--------|-----------|------|
| Success | `st.success` | Extract finished, user saved, table cleared, Azure ready (sidebar) |
| Info | `st.info` | Empty state, merge context, Supabase account hint |
| Warning | `st.warning` | Missing PDF, allowlist mode, “review before AAOS”, Admin needs Supabase |
| Error | `st.error` | Auth failure, Azure missing, extract/load failure, forbidden Admin |
| Neutral split | `st.divider` / `---` | Sidebar sections; Admin user cards |

**Sidebar status pattern**

- Azure OK → `st.sidebar.success("Azure OpenAI · `{deployment}`")`
- Azure missing → `st.sidebar.error(…)`
- Prompt issue → `st.sidebar.warning(…)`

---

## 5. Layout & spacing

| Pattern | Spec |
|---------|------|
| Primary actions | `st.columns([1.2, 1.2, 2])` — buttons left, flexible space right |
| Download + upload | `st.columns([1, 2])` — download compact, upload wider |
| Metrics strip | Equal `st.columns(5)` after a successful extract |
| Forms | `st.form` + `use_container_width=True` on primary submit |
| Dense Admin lists | Per-user block separated by `st.markdown("---")` |

**Density:** Operational UI may be information-dense (tables, metrics). Keep **first screen of a view** to: title, one caption, then the primary control group—no secondary marketing strips.

---

## 6. Components

### 6.1 Buttons

| Kind | Streamlit | Usage |
|------|-----------|--------|
| Primary | `type="primary"` | Extract, Continue (login), create-user submit |
| Secondary | default | Clear table, Save, Activate/Deactivate, Log out |
| Full width | `use_container_width=True` | Sidebar Log out; paired action columns |

Labels are verbs: **Extract door types**, **Extract & merge**, **Clear table**, **Continue**, **Log out**.

### 6.2 Inputs

| Control | Usage |
|---------|--------|
| `st.radio` (horizontal) | Schedule format; merge vs replace; nav |
| `st.file_uploader` | PDFs (multi); corrected Excel |
| `st.slider` | Raster DPI (sidebar) |
| `st.text_input` | Pages; login email; Admin fields |
| `st.selectbox` | Active region; Admin region/role |
| `st.data_editor` | Structured schedule table (edit in place) |
| `st.form` | Login; Admin register user (batch submit) |

Always attach `help=` or a following `st.caption` for non-obvious controls (format, merge mode, page selection).

### 6.3 Metrics

After extract, show a compact strip (door types, total doors, pages, tokens, etc.). Metrics are **readouts**, not CTAs.

### 6.4 Expand / collapse

| Expander | Content |
|----------|---------|
| Source PDFs | List of files used in last run |
| Extraction log | Monospace progress lines |

Default collapsed so the table stays the hero of the results area.

### 6.5 Tables

Structured schedule presentation:

- Editable grid via Streamlit data editor when available.
- Column order and headers come from `door_scheduler/table.py` (e.g. **Item No.**, **Door Type**, **Qty by Level**).
- Prefer whole-number display for item numbers and mm fields.
- Warn above the table that values should be reviewed before AAOS.

Do not wrap the main results table in decorative cards or nested colored panels.

---

## 7. Sidebar design

**Order (top → bottom)**

1. Product title + one-line caption  
2. Azure status  
3. Format / prompt folder cue  
4. DPI + page selection + API interval caption  
5. Divider  
6. Navigation radio (Extract / Admin if allowed)  
7. Divider  
8. Signed-in identity (display name, email, role, auth mode, regions, active region)  
9. Log out  

**Sidebar copy tone:** short labels, `Role · admin`, `Auth · Supabase`, `Active · Malaysia`.

---

## 8. Login & Admin screens

### Login

- Title + caption (“Sign in with your work email”).
- Single primary form field + **Continue**.
- One info/warning about how accounts are managed.
- Footnote: no password; access by email and regions.

### Admin

- Page title **Admin** + caption stating purpose.
- Sections in order: Available regions → Register user → Registered users → Recent logins.
- Destructive/stateful actions (Deactivate) are secondary buttons, not primary.

---

## 9. Content & terminology

| Prefer | Avoid |
|--------|--------|
| Door type / mark | “SKU”, “SKU code” |
| Item No. | Mixing with Door Type |
| Qty by Level | Raw Python dict strings in UI copy |
| Structured Table / Elevation | Internal folder ids in user-facing sentences (ids OK in sidebar tech cue) |
| Extract & merge | “Upsert pipeline” |

Region labels use human names via `region_label()` (e.g. Malaysia), codes (MY) OK in Admin tables.

---

## 10. Motion & illustration

- No custom animation required.
- Streamlit’s native progress bar during extract is the only motion expectation.
- Do not add Lottie, hero imagery, or background patterns to operational views.

---

## 11. Theming (optional future)

If introducing `.streamlit/config.toml`:

- Keep contrast high for tables and forms.
- Primary accent should remain restrained (corporate-neutral blue/teal)—**not** purple-on-white gradient themes.
- Light theme default for spreadsheet-like work; dark mode only if explicitly requested and table contrast is verified.

Until then, rely on Streamlit defaults + semantic status components.

---

## 12. Accessibility & usability

| Rule | Detail |
|------|--------|
| Labels | Every control has a visible label; don’t rely on placeholder alone |
| Errors | Actionable (“Missing in .env: …”, “Upload at least one PDF”) |
| Keyboard | Prefer forms for login/register so Enter submits |
| Width | Full-width primary buttons in narrow columns |
| Review cue | Persistent warning before treating extract as final |

---

## 13. Checklist for UI changes

- [ ] Correct hierarchy (`title` → `subheader` → `####` / caption)  
- [ ] Primary action uses `type="primary"`; destructive/secondary do not  
- [ ] Long or noisy content in expander or caption  
- [ ] Status uses success/info/warning/error appropriately  
- [ ] Sidebar order preserved (product → settings → nav → identity)  
- [ ] Table columns stay aligned with `table.py` headers  
- [ ] No marketing-hero layout on Extract/Admin  
- [ ] Copy uses product terminology (Door Type, Item No., Qty by Level)  

---

## 14. Document control

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-10-03 | Initial design system aligned with Streamlit UI in `main.py` / `auth.py` |
