# AI Door Scheduler

Extract door information from architectural door-schedule PDFs, map it to a project Excel door list, and export into a country-specific AAOS import template.

## Features

### Access & Admin
- **Email-only login** (no password) via Supabase user registry or `.env` allowlist
- **Regions** (MY, CH, VN, TW, TH, HK, PH, SG, ID) with per-user grants
- Sidebar navigation: **Extract** · **Admin** (admins only)
- Admin can register users (email, first/last name, role, region) and review login events

### Step 1 · Extract from PDF
- Choose schedule format before extract:
  - **Elevation Door Schedule** — drawing-grid / New… notes (`Prompt-version/elevation`)
  - **Structured Table Door Schedule** — tabular sheets; analyse/reorganise table first with no data loss, then extract (`Prompt-version/structured_table`)
- Upload **one or more** door-schedule PDFs
- Convert pages to images and extract with Azure OpenAI vision (+ optional text refine)
- Capture fields into an editable table / Excel:
  - **Item No.** (table ITEM / Item No. when present)
  - **Door Type**, width, height, thickness
  - Fire rating, door/frame material, configuration
  - **Qty by Level** and overall **Quantity**
  - Location, description / specification, remarks, page, source PDF
- Handles hinged doors and **roller shutters** (e.g. RS1 / FRS1): W×H without thickness, ignore shutter-box height, `m.s` → Mild steel, fire-rated shutter notes
- Select pages with ranges (`1-6`), lists (`1,8,10`), or mixed (`1-3,8,10`)
- Live progress: per-page status, elapsed time, ETA, and **token input/completion** usage
- **Extract & merge** for split PDFs (keep corrected rows, append new door types)
- Format-specific prompts shown in the sidebar

### Step 2 · Map Excel
- Upload a door list Excel (e.g. Binder4)
- Join **Door Mark** → extracted **Door Type**
- View matched/unmatched rows, edit, and download the complete schedule

### Step 3 · AAOS Template
- Choose a country template under `Door_Schedule_Template/`
- Auto-suggest column mapping (English and localized headers, e.g. Chinese for CH/TW)
- Edit mapping and preview, then download an AAOS import workbook (Schedule + Data sheets)

## Project layout

```text
AI-Door-Scheduler/
├── main.py                          # Streamlit UI (login, Extract, Admin, Steps 1–3)
├── streamlit_app.py                 # Alternate / legacy entry (prefer main.py)
├── requirements.txt
├── .env                             # Secrets (not committed)
├── supabase/
│   └── schema.sql                   # app_users, regions, login_events + seed admin
├── Prompt-version/                  # Format-specific LLM prompts
│   ├── active_version.txt           # Fallback default (elevation)
│   ├── elevation/                   # Elevation Door Schedule
│   ├── structured_table/            # Table schedules (+ analyze_* pass)
│   ├── v1/                          # Legacy baseline
│   └── v2/                          # Legacy generic multi-template
├── door_scheduler/
│   ├── auth.py                      # Login gate, sidebar nav, Admin panel
│   ├── regions.py                   # Region codes / labels
│   ├── supabase_client.py           # Supabase service-role client
│   ├── schedule_formats.py          # Elevation vs Structured Table
│   ├── pdf_images.py                # PDF → images + page selection
│   ├── prompts.py                   # Prompt version loader
│   ├── text_extract.py              # Text-layer mark/size discovery
│   ├── extractor.py                 # Analyze + vision + text enrich + refine
│   ├── table.py                     # Structured table / Excel helpers
│   ├── mapper.py                    # Excel Door Mark ↔ Door Type mapping
│   └── aaos_template.py             # Country AAOS template mapping
└── Door_Schedule_Template/
    ├── CH|HK|ID|MY|PH|SG|TH|TW|VN/
    └── …/AAOS_Excel_Door_Schedule_Import_Template.xlsx
```

## Requirements

- Python 3.12+ (tested with 3.13)
- Azure OpenAI resource with a vision-capable deployment
- Optional: Supabase project for multi-user registry and Admin tools

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
ENDPOINT_URL=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
DEPLOYMENT_NAME=<your-deployment-name>
AZURE_OPENAI_API_VERSION=2024-12-01-preview

# Optional
DOOR_API_MIN_INTERVAL=2.0
# Fallback when no schedule format is selected (UI format radio overrides this)
DOOR_PROMPT_VERSION=elevation

# Email-only login allowlist (either or both) — used when Supabase is not set
ALLOWED_EMAILS=you@company.com,colleague@company.com
ALLOWED_EMAIL_DOMAINS=company.com
ALLOWED_ADMIN_EMAILS=you@company.com

# Supabase user registry + login audit (recommended)
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY
```

## Authentication

### Option A — Supabase (users, regions, login monitor, admin)
1. Create a free project at [supabase.com](https://supabase.com).
2. In **SQL Editor**, run [`supabase/schema.sql`](supabase/schema.sql) (seeds `fahmi.ibrahim@assaabloy.com` as admin with **all regions**).
3. Copy **Project URL** and **service_role** key into `.env`.
4. `pip install -r requirements.txt` then `streamlit run main.py`.
5. Sign in as admin → sidebar **Admin** to create users (email, first name, last name, region) and review logins.

**Region rules**

| Role | Region access |
|------|----------------|
| `user` | Exactly **one** region (MY, CH, VN, TW, TH, HK, PH, SG, ID) |
| `admin` | **All regions**, or a granted subset |

Regions: Malaysia, China, Vietnam, Taiwan, Thailand, Hong Kong, Philippines, Singapore, Indonesia.

### Option B — `.env` allowlist (no database)
Set `ALLOWED_EMAILS` / `ALLOWED_EMAIL_DOMAINS`. Optional `ALLOWED_ADMIN_EMAILS` for the Admin tab (create-user / login history still need Supabase).

## Run the app

```powershell
.\.venv\Scripts\activate
streamlit run main.py
```

Open the URL shown in the terminal (default: http://localhost:8501).

Sign in with an allowed / registered email (no password). Use **Log out** in the sidebar to end the session.

### Step 1 page selection

| Setting | Example | Meaning |
|---------|---------|---------|
| Pages | `all` | Every page in each PDF |
| Pages | `1-6` | Pages 1 through 6 |
| Pages | `1,8,10` | Pages 1, 8, and 10 only |
| Pages | `1-3,8,10` | Combined range + list |

Page selection is applied independently to each uploaded PDF.

During extraction the UI shows:
- Progress bar and per-page log
- Elapsed time and ETA
- Token **input** and **completion** counts (per page and totals)

## Extraction notes

| Door family | Size pattern | Notes |
|-------------|--------------|--------|
| Hinged / sliding | `1100mm (W) x 2400mm (H) x 50mm thk` | Prefer description text over vision-panel sizes |
| Roller shutter | `3000mm (W) x 2400mm (H)` | Thickness often absent (`null`); ignore ~300mm shutter-box height on the drawing |
| Fire shutter | e.g. FRS1 + `2 hour fire rated roller shutter` | `fire_rating` from the note; `configuration` = `roller shutter` |
| Standard shutter | e.g. RS1 + `manual m.s roller shutter` | `door_material` = Mild steel; `fire_rating` = null |

**Structured Table** schedules:
- First-pass **analyze** may restructure merged/multi-tier headers into one row per door without dropping cells
- **Item No.** maps from ITEM / Item No. / No. (kept separate from Door Type / door code)
- **Qty by Level** is stored as readable text (e.g. `LEVEL 2: 5; LEVEL 3: 6`), including when the model returns a dict
- Marks with multi-letter suffixes (e.g. `D10GA`, `D10LA`) and letter-only codes (e.g. `FDGA`) are accepted

Text enrichment runs after vision so PDF text-layer phrases fill gaps. A second LLM refine pass runs only when fields are still missing (shutters do not force thickness/frame refine).

## Step 3 country templates

| Code | Country |
|------|---------|
| CH | China |
| HK | Hong Kong |
| ID | Indonesia |
| MY | Malaysia |
| PH | Philippines |
| SG | Singapore |
| TH | Thailand |
| TW | Taiwan |
| VN | Vietnam |

Add more countries as:

```text
Door_Schedule_Template/<COUNTRY>/*.xlsx
```

They appear automatically in the Step 3 dropdown. Suggested mappings work across English and localized headers.

## Prompts

LLM prompts are versioned under `Prompt-version/`. The Step 1 **schedule format** radio selects the prompt folder:

| Format (UI) | Prompt folder | Passes |
|-------------|---------------|--------|
| Elevation Door Schedule | `elevation/` | vision + refine |
| Structured Table Door Schedule | `structured_table/` | **analyze** + vision + refine |

```text
Prompt-version/
  active_version.txt       # Fallback default (elevation)
  elevation/
    manifest.json
    schema_hint.txt
    vision_*.txt
    refine_*.txt
  structured_table/
    manifest.json
    schema_hint.txt
    analyze_*.txt          # Table layout / restructure pass
    vision_*.txt
    refine_*.txt
  v1/ · v2/                # Legacy versions (still loadable)
```

To change prompts without losing history:

1. Copy a folder (e.g. `structured_table` → `structured_table_v2`)
2. Edit files and update `manifest.json`
3. Point `schedule_formats.py` / `prompts.py` at the new id, or set `DOOR_PROMPT_VERSION` for the fallback

The active format prompt folder is shown in the sidebar.

## Typical workflow

1. Sign in → choose region (if applicable)
2. **Step 1** — Pick Elevation or Structured Table, extract door types from PDF schedules
3. Review / edit the table (Item No., sizes, qty by level, description…) and download Excel if needed
4. **Step 2** — Map onto the project door list Excel by Door Mark
5. **Step 3** — Map the complete schedule into the AAOS import template for your country

## Notes

- Keep `.env` private; do not commit API keys or the Supabase service-role key
- Extraction quality depends on drawing clarity and the Azure model deployment
- Unmatched Step 2 rows usually mean those door marks were not on the PDF pages you extracted
- Generated files under `exports/` and local sample PDFs/Excel are ignored by git (templates under `Door_Schedule_Template/` are kept)
