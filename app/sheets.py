# app/sheets.py
from __future__ import annotations

import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# --------- ENV ---------
GSHEET_ID = os.getenv("GSHEET_ID", "").strip()
GSHEET_SHEET = os.getenv("GSHEET_SHEET", "Leads").strip() or "Leads"
GSERVICE_JSON = os.getenv("GSERVICE_JSON", "").strip()
TZ = os.getenv("TZ", "Europe/Kyiv")

# --------- Заголовки и оформление ---------
HEADERS = [
    "🆔 id",
    "📅 Створено (локальний час)",
    "📌 Статус",
    "🔗 Джерело",
    "👤 Імʼя",
    "📞 Контакт",
    "✉️ Email",
    "🏷 Категорія/тип",
    "⏱ Терміновість",
    "🧭 Формат",
    "🕒 Тривалість, хв",
    "📝 Коротко",
]

# Колонка дат для форматирования (1-based индекс)
COL_DATE = 2


def _authorize():
    """Авторизация и клиенты gspread + Sheets API."""
    if not (GSHEET_ID and GSERVICE_JSON):
        raise RuntimeError("GSHEET_ID / GSERVICE_JSON не заданы в .env")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GSERVICE_JSON, scopes=scopes)
    gc = gspread.authorize(creds)
    api = build("sheets", "v4", credentials=creds)
    return gc, api


def _open_or_create_worksheet(gc) -> gspread.Worksheet:
    sh = gc.open_by_key(GSHEET_ID)
    try:
        ws = sh.worksheet(GSHEET_SHEET)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=GSHEET_SHEET, rows=200, cols=len(HEADERS))
    return ws


def _ensure_layout(ws: gspread.Worksheet, api):
    """Заголовки, заморозка строки, перенос, автоширина, формат даты."""
    # Заголовок
    first_row = ws.row_values(1)
    if first_row != HEADERS:
        ws.resize(rows=max(ws.row_count, 1), cols=len(HEADERS))
        ws.update("1:1", [HEADERS])

    # Заморозить 1 строку
    try:
        ws.freeze(rows=1)
    except Exception:
        pass

    # Перенос строк для всех колонок
    try:
        sheet_id = ws._properties["sheetId"]
        api.spreadsheets().batchUpdate(
            spreadsheetId=GSHEET_ID,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {"sheetId": sheet_id},
                            "cell": {
                                "userEnteredFormat": {
                                    "wrapStrategy": "WRAP"
                                }
                            },
                            "fields": "userEnteredFormat.wrapStrategy",
                        }
                    }
                ]
            },
        ).execute()
    except Exception as e:
        logging.debug("wrapStrategy failed: %r", e)

    # Формат даты для колонки B
    try:
        sheet_id = ws._properties["sheetId"]
        api.spreadsheets().batchUpdate(
            spreadsheetId=GSHEET_ID,
            body={
                "requests": [
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,  # со 2-й строки (0-based)
                                "startColumnIndex": COL_DATE - 1,
                                "endColumnIndex": COL_DATE,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "numberFormat": {
                                        "type": "DATE_TIME",
                                        "pattern": "yyyy-mm-dd hh:mm",
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.numberFormat",
                        }
                    }
                ]
            },
        ).execute()
    except Exception as e:
        logging.debug("date format failed: %r", e)

    # Авто-ширина всех колонок
    try:
        sheet_id = ws._properties["sheetId"]
        api.spreadsheets().batchUpdate(
            spreadsheetId=GSHEET_ID,
            body={
                "requests": [
                    {
                        "autoResizeDimensions": {
                            "dimensions": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": 0,
                                "endIndex": len(HEADERS),
                            }
                        }
                    }
                ]
            },
        ).execute()
    except Exception as e:
        logging.debug("autoResize failed: %r", e)


def _safe_text(v) -> str:
    return "" if v is None else str(v)


def _format_contact(v) -> str:
    """Сохраняем телефон как текст (Excel/Sheets не съест нули)."""
    s = _safe_text(v).strip()
    if not s:
        return ""
    if not s.startswith("'"):
        s = "'" + s
    return s


def _local_dt(dt: datetime) -> datetime:
    try:
        tz = ZoneInfo(TZ)
    except Exception:
        tz = ZoneInfo("Europe/Kyiv")
    try:
        return dt.astimezone(tz)
    except Exception:
        return dt


def _row_from_lead(lead) -> list[str]:
    """Собираем строку по нашему красивому хедеру."""
    created = _local_dt(getattr(lead, "created_at", datetime.utcnow()))
    return [
        str(lead.id),
        created.strftime("%Y-%m-%d %H:%M"),
        _safe_text(lead.status or "new"),
        _safe_text(lead.source),
        _safe_text(lead.name),
        _format_contact(lead.contact),
        _safe_text(lead.email),
        _safe_text(lead.category),
        _safe_text(lead.urgency),
        _safe_text(lead.consult_format),
        _safe_text(lead.duration),
        (_safe_text(lead.brief).replace("\n", " "))[:2000],
    ]


def append_lead_safe(lead) -> None:
    """
    Публичная функция: красиво добавляет строку в таблицу.
    Никогда не падает наружу — при ошибках пишет в лог.
    """
    try:
        gc, api = _authorize()
        ws = _open_or_create_worksheet(gc)
        _ensure_layout(ws, api)

        row = _row_from_lead(lead)
        ws.append_row(row, value_input_option="USER_ENTERED", table_range="A1")

        # Чуть-чуть авто-ширины после вставки (бывает полезно)
        try:
            sheet_id = ws._properties["sheetId"]
            api.spreadsheets().batchUpdate(
                spreadsheetId=GSHEET_ID,
                body={
                    "requests": [
                        {
                            "autoResizeDimensions": {
                                "dimensions": {
                                    "sheetId": sheet_id,
                                    "dimension": "COLUMNS",
                                    "startIndex": 0,
                                    "endIndex": len(HEADERS),
                                }
                            }
                        }
                    ]
                },
            ).execute()
        except Exception:
            pass

        logging.info("Sheets: lead #%s appended", getattr(lead, "id", "?"))
    except HttpError as e:
        logging.error("Sheets API HttpError: %s", e)
    except Exception as e:
        logging.error("Sheets append_lead_safe error: %r", e)
