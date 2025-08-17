# app/admin.py
from __future__ import annotations
import os, re, csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)

from sqlalchemy import select, desc
from .db import async_session, Lead

router = Router(name="admin")

# Адміни з .env (fallback для локального тесту)
ADMIN_IDS = {int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_IDS", ""))} or {438150673}

# Таймзона для відображення
TZ = os.getenv("TZ", "Europe/Kyiv")
TZ_INFO = ZoneInfo(TZ)

# ----------------- Лейбли для фільтрів -----------------
STATUS_LABELS = {
    "any":       "🗂 Усі статуси",
    "new":       "🆕 Нові",
    "in_review": "🟡 В роботі",
    "scheduled": "🗓 Заплановано",
    "closed":    "✅ Закриті",
}
SOURCE_LABELS = {
    "any":      "🌐 Усі джерела",
    "quick":    "⚡️ Швидке питання",
    "booking":  "📞 Запис",
    "document": "📄 Документи",
}
PERIOD_LABELS = {
    "7d":  "📆 7 днів",
    "30d": "📆 30 днів",
    "90d": "📆 90 днів",
    "all": "♾️ За весь час",
}

# Для рендеру карточки (без зайвих емодзі)
RENDER_STATUS = {
    "new": "новий",
    "in_review": "в роботі",
    "scheduled": "заплановано",
    "closed": "закритий",
}
RENDER_SOURCE = {
    "quick": "Швидке питання",
    "booking": "Запис",
    "document": "Документи",
}

# ----------------- Фільтри (in-memory) -----------------
_FILTERS: dict[int, dict[str, str]] = defaultdict(
    lambda: {"status": "any", "source": "any", "period": "30d"}
)

def get_filters(uid: int) -> dict[str, str]:
    return dict(_FILTERS[uid])

def set_filter(uid: int, key: str, value: str) -> None:
    f = _FILTERS[uid]
    f[key] = value
    _FILTERS[uid] = f

def clear_filters(uid: int) -> None:
    if uid in _FILTERS:
        del _FILTERS[uid]

def period_to_days(period: str) -> int | None:
    return {"7d": 7, "30d": 30, "90d": 90}.get(period)

def filters_human(f: dict[str, str]) -> str:
    return (
        f"Статус: {STATUS_LABELS.get(f['status'], f['status'])} • "
        f"Джерело: {SOURCE_LABELS.get(f['source'], f['source'])} • "
        f"Період: {PERIOD_LABELS.get(f['period'], f['period'])}"
    )

def mark(text: str, selected: bool) -> str:
    return f"✅ {text}" if selected else text

def kb_filters(uid: int) -> InlineKeyboardMarkup:
    f = get_filters(uid)
    return InlineKeyboardMarkup(inline_keyboard=[
        # Статус
        [
            InlineKeyboardButton(
                text=mark(STATUS_LABELS["any"], f["status"] == "any"),
                callback_data="admin:filters:set:status:any"
            ),
        ],
        [
            InlineKeyboardButton(
                text=mark(STATUS_LABELS["new"], f["status"] == "new"),
                callback_data="admin:filters:set:status:new"
            ),
            InlineKeyboardButton(
                text=mark(STATUS_LABELS["in_review"], f["status"] == "in_review"),
                callback_data="admin:filters:set:status:in_review"
            ),
        ],
        [
            InlineKeyboardButton(
                text=mark(STATUS_LABELS["scheduled"], f["status"] == "scheduled"),
                callback_data="admin:filters:set:status:scheduled"
            ),
            InlineKeyboardButton(
                text=mark(STATUS_LABELS["closed"], f["status"] == "closed"),
                callback_data="admin:filters:set:status:closed"
            ),
        ],
        # Джерело
        [
            InlineKeyboardButton(
                text=mark(SOURCE_LABELS["any"], f["source"] == "any"),
                callback_data="admin:filters:set:source:any"
            ),
        ],
        [
            InlineKeyboardButton(
                text=mark(SOURCE_LABELS["quick"], f["source"] == "quick"),
                callback_data="admin:filters:set:source:quick"
            ),
            InlineKeyboardButton(
                text=mark(SOURCE_LABELS["booking"], f["source"] == "booking"),
                callback_data="admin:filters:set:source:booking"
            ),
            InlineKeyboardButton(
                text=mark(SOURCE_LABELS["document"], f["source"] == "document"),
                callback_data="admin:filters:set:source:document"
            ),
        ],
        # Період
        [
            InlineKeyboardButton(
                text=mark(PERIOD_LABELS["7d"], f["period"] == "7d"),
                callback_data="admin:filters:set:period:7d"
            ),
            InlineKeyboardButton(
                text=mark(PERIOD_LABELS["30d"], f["period"] == "30d"),
                callback_data="admin:filters:set:period:30d"
            ),
            InlineKeyboardButton(
                text=mark(PERIOD_LABELS["90d"], f["period"] == "90d"),
                callback_data="admin:filters:set:period:90d"
            ),
            InlineKeyboardButton(
                text=mark(PERIOD_LABELS["all"], f["period"] == "all"),
                callback_data="admin:filters:set:period:all"
            ),
        ],
        [
            InlineKeyboardButton(text="📄 Показати всі", callback_data="admin:list:all:0"),
        ],
        [
            InlineKeyboardButton(text="🧼 Очистити", callback_data="admin:filters:clear"),
            InlineKeyboardButton(text="🏠 Меню", callback_data="admin:menu"),
        ],
    ])

# ----------------- helpers -----------------
def is_admin(event) -> bool:
    u = getattr(event, "from_user", None)
    return bool(u and u.id in ADMIN_IDS)

def kb_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🆕 Нові", callback_data="admin:list:new:0"),
        InlineKeyboardButton(text="📄 Всі",  callback_data="admin:list:all:0"),
    ],[
        InlineKeyboardButton(text="⚙️ Фільтри", callback_data="admin:filters"),
        InlineKeyboardButton(text="📤 Експорт CSV", callback_data="admin:export"),
    ]])

def kb_list(items: list[Lead], page: int, scope: str) -> InlineKeyboardMarkup:
    rows = []
    for lead in items:
        title = f"№{lead.id} • {lead.source} • {lead.name or lead.contact or 'без імені'} • {lead.status}"
        rows.append([InlineKeyboardButton(text=title[:64], callback_data=f"admin:lead:{lead.id}:{scope}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:list:{scope}:{page-1}"))
    nav.append(InlineKeyboardButton(text="⚙️ Фільтри", callback_data="admin:filters"))
    nav.append(InlineKeyboardButton(text="🏠 Меню", callback_data="admin:menu"))
    if len(items) == 10:
        nav.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin:list:{scope}:{page+1}"))
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_lead_actions(lead_id: int, scope: str = "all", page: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟡 В роботі",     callback_data=f"admin:status:{lead_id}:in_review:{scope}:{page}"),
            InlineKeyboardButton(text="🗓 Заплановано",  callback_data=f"admin:status:{lead_id}:scheduled:{scope}:{page}"),
            InlineKeyboardButton(text="✅ Закрито",      callback_data=f"admin:status:{lead_id}:closed:{scope}:{page}"),
        ],
        [InlineKeyboardButton(text="🗑 Видалити", callback_data=f"admin:delete:{lead_id}:{scope}:{page}")],
        [InlineKeyboardButton(text="⬅️ До списку", callback_data=f"admin:list:{scope}:{page}")],
    ])

async def fetch_page(scope: str, page: int, uid: int) -> list[Lead]:
    """Враховує активні фільтри користувача: статус/джерело/період."""
    f = get_filters(uid)
    from .db import Lead as L

    stmt = select(L).order_by(desc(L.created_at)).offset(page * 10).limit(10)

    # Статус
    if scope == "new":
        stmt = stmt.where(L.status == "new")
    elif f["status"] != "any":
        stmt = stmt.where(L.status == f["status"])

    # Джерело
    if f["source"] != "any":
        stmt = stmt.where(L.source == f["source"])

    # Період
    days = period_to_days(f["period"])
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = stmt.where(L.created_at >= cutoff)

    async with async_session() as session:
        res = await session.execute(stmt)
        return list(res.scalars().all())

async def fetch_lead(lead_id: int) -> Lead | None:
    async with async_session() as session:
        res = await session.execute(select(Lead).where(Lead.id == lead_id))
        return res.scalar_one_or_none()

def render_lead(lead: Lead) -> str:
    def v(x, dash="—"):
        return x if (x is not None and str(x).strip()) else dash

    created = "—"
    try:
        if getattr(lead, "created_at", None):
            created = lead.created_at.astimezone(TZ_INFO).strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass

    status_ua = RENDER_STATUS.get((lead.status or "").strip(), lead.status or "new")
    source_ua = RENDER_SOURCE.get((lead.source or "").strip(), lead.source or "—")

    lines = [
        f"<b>Лід №{lead.id}</b> ({status_ua})",
        f"Джерело: {source_ua}",
        f"Створено: {created}",
        f"Імʼя: {v(lead.name)}",
        f"Контакт: {v(lead.contact)}",
        f"Email: {v(lead.email)}",
        f"Категорія/тип: {v(lead.category)}",
        f"Терміновість: {v(lead.urgency)}",
        f"Формат: {v(lead.consult_format)}",
        f"Тривалість: {v(lead.duration)}",
        f"Слот: {v(lead.slot_iso)}",
        f"Коротко: {v(lead.brief)}",
    ]
    return "\n".join(lines)

# ----------------- handlers -----------------

@router.message(Command("admin"))
async def admin_entry(message: Message):
    if not is_admin(message):
        return await message.answer("Доступ заборонено.")
    await message.answer("Адмін-панель:", reply_markup=kb_admin_menu())

@router.callback_query(F.data == "admin:menu")
async def admin_menu(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    await call.message.edit_text("Адмін-панель:", reply_markup=kb_admin_menu())
    await call.answer()

@router.callback_query(F.data == "admin:filters")
async def admin_filters(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    text = "Фільтри (застосовуються до списків):\n" + filters_human(get_filters(call.from_user.id))
    await call.message.edit_text(text, reply_markup=kb_filters(call.from_user.id))
    await call.answer()

@router.callback_query(F.data.startswith("admin:filters:set:"))
async def admin_filters_set(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    _, _, _, key, value = call.data.split(":")
    set_filter(call.from_user.id, key, value)
    text = "Фільтри оновлено:\n" + filters_human(get_filters(call.from_user.id))
    await call.message.edit_text(text, reply_markup=kb_filters(call.from_user.id))
    await call.answer("Застосовано")

@router.callback_query(F.data == "admin:filters:clear")
async def admin_filters_clear(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    clear_filters(call.from_user.id)
    text = "Фільтри очищено.\n" + filters_human(get_filters(call.from_user.id))
    await call.message.edit_text(text, reply_markup=kb_filters(call.from_user.id))
    await call.answer("Скинуто")

@router.callback_query(F.data.startswith("admin:list:"))
async def admin_list(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    _, _, scope, page_str = call.data.split(":")
    page = int(page_str)
    items = await fetch_page(scope, page, call.from_user.id)
    title = "Нові ліди:" if scope == "new" else "Всі ліди:"
    flt = filters_human(get_filters(call.from_user.id))
    text = f"{title}\n{flt}\nСторінка {page+1}"
    await call.message.edit_text(text, reply_markup=kb_list(items, page, scope))
    await call.answer()

@router.callback_query(F.data.startswith("admin:lead:") & ~F.data.startswith("admin:lead:open"))
async def admin_open_lead_from_list(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    _, _, lead_id_str, scope, page_str = call.data.split(":")
    lead = await fetch_lead(int(lead_id_str))
    if not lead:
        await call.answer("Лід не знайдено", show_alert=True); return
    await call.message.edit_text(render_lead(lead), reply_markup=kb_lead_actions(lead.id, scope, int(page_str)))
    await call.answer()

@router.callback_query(F.data.startswith("admin:lead:open:"))
async def admin_open_lead_from_notify(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    lead_id = int(call.data.split(":")[-1])
    lead = await fetch_lead(lead_id)
    if not lead:
        await call.answer("Лід не знайдено", show_alert=True); return
    await call.message.edit_text(render_lead(lead), reply_markup=kb_lead_actions(lead.id, "all", 0))
    await call.answer()

@router.callback_query(F.data.startswith("admin:status:"))
async def admin_set_status(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    _, _, lead_id_str, new_status, scope, page_str = call.data.split(":")
    async with async_session() as session:
        res = await session.execute(select(Lead).where(Lead.id == int(lead_id_str)))
        lead = res.scalar_one_or_none()
        if not lead:
            return await call.answer("Лід не знайдено", show_alert=True)
        lead.status = new_status
        await session.commit()
    await call.answer("Статус оновлено")
    lead = await fetch_lead(int(lead_id_str))
    await call.message.edit_text(render_lead(lead), reply_markup=kb_lead_actions(lead.id, scope, int(page_str)))

@router.callback_query(F.data.startswith("admin:delete:"))
async def admin_delete_lead(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    _, _, lead_id_str, scope, page_str = call.data.split(":")
    async with async_session() as session:
        res = await session.execute(select(Lead).where(Lead.id == int(lead_id_str)))
        lead = res.scalar_one_or_none()
        if not lead:
            return await call.answer("Лід не знайдено", show_alert=True)
        await session.delete(lead)
        await session.commit()
    await call.answer("Видалено")
    items = await fetch_page(scope, int(page_str), call.from_user.id)
    flt = filters_human(get_filters(call.from_user.id))
    text = f"{'Нові' if scope=='new' else 'Всі'} ліди:\n{flt}\nСторінка {int(page_str)+1}"
    await call.message.edit_text(text, reply_markup=kb_list(items, int(page_str), scope))

@router.callback_query(F.data == "admin:export")
async def admin_export(call: CallbackQuery):
    if not is_admin(call):
        return await call.answer("Немає доступу", show_alert=True)
    file_path = Path("data") / f"leads_export_{datetime.now(TZ_INFO).strftime('%Y%m%d_%H%M')}.csv"
    file_path.parent.mkdir(exist_ok=True)
    async with async_session() as session:
        res = await session.execute(select(Lead).order_by(desc(Lead.created_at)))
        leads = list(res.scalars().all())
    # Excel-friendly CSV
    with file_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writerow(["id","created_at_local","status","source","name","contact","email",
                    "category","urgency","duration","slot_iso","brief"])
        for l in leads:
            created_local = l.created_at.astimezone(TZ_INFO).strftime("%Y-%m-%d %H:%M")
            contact = l.contact or ""
            if contact and not contact.startswith("'"):
                contact = "'" + contact
            w.writerow([l.id, created_local, l.status, l.source, l.name or "",
                        contact, l.email or "", l.category or "", l.urgency or "",
                        l.duration or "", l.slot_iso or "", (l.brief or "").replace("\n"," ")])
    await call.message.answer_document(FSInputFile(str(file_path), filename=file_path.name))
    await call.answer("Готово")