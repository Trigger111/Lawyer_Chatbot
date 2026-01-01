# Lawyer_Chatbot

Telegram bot for a lawyer that collects leads in 3 flows (quick question, consultation booking, document review), saves everything to SQLite, notifies admins with inline actions and syncs leads to Google Sheets.

> Important: bot messages are informational and are not a legal opinion.

## Key features

### Lead intake flows
- **Quick question**: category -> short description (up to 500 chars) -> urgency -> consultation offer -> name -> contact -> optional email  
  - Optional **PDF attachments** (up to 2) are stored as Telegram `file_id`
- **Consultation booking**: format (phone / Telegram call / Zoom) -> duration (30 / 60) -> name -> contact -> optional email
- **Document review** (`/document`): document type -> upload files (document/photo) + short context -> review plan (express / call)

### Admin experience
- Automatic admin notifications about a new lead with buttons:
  - open lead card
  - show attachments
  - change status (in review / closed)
- Admin panel (`/admin`)
  - list new leads or all leads with pagination
  - filters: status, source, period (7/30/90 days or all time)
  - lead actions: update status, delete lead
  - CSV export to `data/leads_export_YYYYMMDD_HHMM.csv`

### Storage and data model
- SQLite database `data/bot.db` via **SQLAlchemy async**
- Tables:
  - `users` (telegram profile, last seen)
  - `leads` (source, category, brief, urgency, format, duration, slot, контакты)
  - `documents` (lead attachments, cascades on delete)
  - `message_logs` (inbound/outbound logging)
- Middleware upserts user and logs inbound messages automatically

### Google Sheets sync
After saving a lead, the bot appends a row to Google Sheets in a background thread (does not block polling).
Credentials support:
- path to service account JSON file
- raw JSON string
- base64(JSON)

## Tech stack
Python 3.12, aiogram 3, SQLAlchemy (async), aiosqlite, Google Sheets API (gspread + google-api-python-client), python-dotenv

## Project structure
- `main.py` - bot entrypoint
- `app/handlers.py` - user flows (FSM) + admin quick actions from notifications
- `app/admin.py` - admin panel (filters, lists, export)
- `app/db.py` - DB models + async engine + helpers
- `app/middlewares.py` - user upsert + message logging middleware
- `app/keyboards.py` - reply and inline keyboards
- `app/sheets.py` - Google Sheets append + worksheet formatting
- `data/` - SQLite DB and exports (should not be committed)

## Setup

### 1) Create venv and install dependencies
Windows PowerShell:
```bash
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
