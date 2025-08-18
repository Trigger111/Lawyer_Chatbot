# app/handlers.py
from __future__ import annotations
import asyncio
import os
import re
import logging
from typing import Optional, List

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery, ContentType,
    InlineKeyboardMarkup, InlineKeyboardButton,
    Contact, ReplyKeyboardRemove,
)

from app.sheets import append_lead_safe
from .keyboards import (
    start_kb, main_reply_kb, back_menu_reply_kb, back_and_menu_kb,
    categories_inline_kb, urgency_inline_kb, consult_offer_inline_kb,
    format_inline_kb,
    document_type_inline_kb, document_plan_inline_kb,
    contact_request_kb, back_menu_skip_kb,
)
# «Клава тільки меню»: беремо з keyboards, а якщо її там ще нема — робимо локальний фолбек
try:
    from .keyboards import menu_only_kb  # бажаний імпорт
except Exception:
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    def menu_only_kb():
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🏠 Меню")]],
            resize_keyboard=True,
        )

from app.db import async_session, create_lead, upsert_user, Lead, Document

# ----------------- ADMIN IDS FROM .env -----------------
RAW_ADMIN_IDS = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = {int(x) for x in re.findall(r"\d+", RAW_ADMIN_IDS)}
logging.info("ADMIN_IDS parsed: %s", ADMIN_IDS)

# ----------------- Validation helpers -----------------
RX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RX_PHONE = re.compile(r"^\+?\d[\d\-\s]{6,}$")
RX_TG    = re.compile(r"^@[A-Za-z0-9_]{5,}$")
def valid_contact(s: str) -> bool: return bool(RX_PHONE.match(s) or RX_TG.match(s))
def valid_email(s: str) -> bool:   return bool(RX_EMAIL.match(s))

# --- нормализация текста кнопок
def norm(title: str) -> str:
    if not title:
        return ""
    s = title
    for e in ("🚀", "⚡️", "📞", "📚", "👩‍⚖️", "🏠"):
        s = s.replace(e, "")
    return re.sub(r"\s+", " ", s).strip().lower()

BTN_TITLES = {
    "Швидке питання": "Швидке питання",
    "Записатися на консультацію": "Записатися на консультацію",
    "Статті та гіди": "Статті та гіди",
    "Про юриста / Контакти": "Про юриста / Контакти",
    "Меню": "Меню",
}
BTN_SET = {t.lower() for t in BTN_TITLES.values()}

# --- файлы на етапе короткого опису ---
MAX_PDFS = 2
ALLOWED_DOC_MIMES = {"application/pdf"}

router = Router()

# ----------------- Admin notify with actions -----------------
def kb_admin_lead_actions(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Відкрити картку", callback_data=f"admin:lead:open:{lead_id}")],
        [InlineKeyboardButton(text="📎 Вкладення", callback_data=f"admin:files:{lead_id}")],
        [
            InlineKeyboardButton(text="🟡 В роботі", callback_data=f"admin:lead:status:{lead_id}:in_review"),
            InlineKeyboardButton(text="✅ Закрити",  callback_data=f"admin:lead:status:{lead_id}:closed"),
        ],
    ])

async def notify_admins_with_actions(bot, lead: Lead):
    def v(x): return x if x not in (None, "", False) else "—"
    text = (
        f"🔔 <b>Новий лід</b> #{lead.id}\n"
        f"Джерело: {v(lead.source)}\n"
        f"Ім'я: {v(lead.name)}\n"
        f"Контакт: {v(lead.contact)}\n"
        f"Слот: {v(lead.slot_iso)}\n"
        f"Опис: {v((lead.brief or '')[:200])}"
    )
    kb = kb_admin_lead_actions(lead.id)
    for uid in ADMIN_IDS:
        try:
            await bot.send_message(uid, text, reply_markup=kb)
        except Exception as e:
            logging.warning("Notify admin %s failed: %r", uid, e)

def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def _render_lead_card(l: Lead) -> str:
    def v(x): return x if x not in (None, "", False) else "—"
    rows = [
        f"🗂 <b>Лід #{l.id}</b>",
        f"Статус: <b>{v(l.status)}</b>",
        f"Джерело: {v(l.source)}",
        f"Категорія: {v(l.category)}",
        f"Терміновість: {v(l.urgency)}",
        f"Формат: {v(l.consult_format)}",
        f"Тривалість: {v(l.duration)}",
        f"Слот: {v(l.slot_iso)}",
        f"Ім’я: {v(l.name)}",
        f"Контакт: {v(l.contact)}",
        f"Email: {v(l.email)}",
    ]
    if l.brief:
        rows.append("\n📝 <b>Коротко:</b>\n" + l.brief)
    return "\n".join(rows)

@router.callback_query(F.data.startswith("admin:lead:open:"))
async def admin_open_lead(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Недоступно", show_alert=True)
    lead_id = int(call.data.rsplit(":", 1)[1])
    async with async_session() as session:
        lead = await session.get(Lead, lead_id)
        if not lead:
            return await call.answer("Лід не знайдено", show_alert=True)
    await call.message.edit_text(_render_lead_card(lead), reply_markup=kb_admin_lead_actions(lead.id))
    await call.answer()

@router.callback_query(F.data.startswith("admin:lead:status:"))
async def admin_change_status(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Недоступно", show_alert=True)
    _, _, _, lead_id_str, new_status = call.data.split(":")
    lead_id = int(lead_id_str)
    async with async_session() as session:
        lead = await session.get(Lead, lead_id)
        if not lead:
            return await call.answer("Лід не знайдено", show_alert=True)
        lead.status = new_status
        await session.commit()
    await call.message.edit_text(_render_lead_card(lead), reply_markup=kb_admin_lead_actions(lead.id))
    await call.answer("Статус оновлено ✅")

@router.callback_query(F.data.startswith("admin:files:"))
async def admin_send_attachments(call: CallbackQuery):
    if not _is_admin(call.from_user.id):
        return await call.answer("Недоступно", show_alert=True)
    lead_id = int(call.data.rsplit(":", 1)[1])

    async with async_session() as session:
        from sqlalchemy import select
        res = await session.execute(select(Document).where(Document.lead_id == lead_id))
        docs: List[Document] = list(res.scalars().all())

    if not docs:
        return await call.answer("Файлів немає", show_alert=True)

    for idx, d in enumerate(docs, 1):
        try:
            await call.message.answer_document(d.file_id, caption=f"Лід #{lead_id} — вкладення {idx}/{len(docs)}")
        except Exception as e:
            logging.warning("Send stored doc failed: %r", e)
    await call.answer("Надіслано.")

# ----------------- Articles -----------------
ARTICLES = [
    {"title": "Як зменшити ризики кримінального переслідування бізнесу", "url": "https://biz.ligazakon.net/analitycs/236799_yak-zmenshiti-riziki-krimnalnogo-pereslduvannya-bznesu-chek-list", "summary": "Ліга Закон"},
    {"title": "Як поводити себе на допиті", "url": "https://www.instagram.com/p/DKZjdvotbmv/?igsh=Y2VubDd1cnoxN3E4", "summary": "Інстаграм блог"},
    {"title": "Як розпочати розслідування в поліції за вашою заявою", "url": "https://www.instagram.com/p/DM-dqGrx3bf/?igsh=YXZyemxjYThjY2g0", "summary": "Інстаграм блог"},
    {"title": "Що буде за організацію вечірок 18+", "url": "https://www.instagram.com/p/DL7cervN775/?igsh=MW82bjV2N2Z0aWlnZA==", "summary": "Інстаграм блог"},
    {"title": "Покарання за порнографію:", "url": "https://www.instagram.com/p/DLm3ihVNOK8/?igsh=MXVmNXcwaXJvbDRqcA==", "summary": "Інстаграм блог"},
]

@router.message(F.text.func(lambda t: norm(t) == "статті та гіди"))
async def blog_menu(message: Message):
    lines = ["Останні матеріали:"]
    for a in ARTICLES[:5]:
        lines.append(f"• <b>{a['title']}</b>\n{a['summary']}\n<a href='{a['url']}'>Читати</a>")
    # тільки «Головне меню»
    await message.answer("\n\n".join(lines), reply_markup=menu_only_kb())

# ----------------- Global main-button router -----------------
async def route_main_button(message: Message, state: FSMContext) -> bool:
    t = norm(message.text or "")
    if t not in BTN_SET:
        return False
    await state.clear()
    if t == "швидке питання":
        await quick_entry(message, state)
    elif t == "записатися на консультацію":
        await booking_entry(message, state)
    elif t == "статті та гіди":
        await blog_menu(message)
    elif t == "про юриста / контакти":
        await about(message, state)
    elif t == "меню":
        await back_to_menu(message, state)
    return True

# ----------------- Start/menu -----------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Вітаю! Я бот юриста. Допоможу зі швидким питанням, записом на консультацію та матеріалами.\n"
        "<i>Важливо: відповіді бота не є юридичним висновком.</i>",
        reply_markup=main_reply_kb(),
    )

@router.message(F.text.func(lambda t: norm(t) == "розпочати"))
async def start_button(message: Message, state: FSMContext):
    await cmd_start(message, state)

@router.message(F.text.func(lambda t: norm(t) == "меню"))
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Головне меню:", reply_markup=main_reply_kb())

@router.message(F.text.func(lambda t: norm(t) == "про юриста / контакти"))
async def about(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "<b>Юристка:</b> Марія Бутіна\n"
        "<b>Спеціалізації:</b> кримінальне, цивільне, господарське право\n"
        "<b>Контакти:</b> @mariyabutina, mashabutina2001@gmail.com\n"
        "<b>Години роботи:</b> 08:00–20:00 (пн–пт), вихідні за потреби",
        reply_markup=menu_only_kb(),  # тільки «Меню»
    )

# ----------------- FSM states -----------------
class Quick(StatesGroup):
    category = State()
    brief    = State()
    urgency  = State()
    offer    = State()
    name     = State()
    contact  = State()
    email    = State()

class Booking(StatesGroup):
    fmt    = State()
    dur    = State()
    name   = State()
    contact= State()
    email  = State()

class DocumentFlow(StatesGroup):
    type    = State()
    upload  = State()
    plan    = State()
    name    = State()
    contact = State()
    email   = State()

# ----------------- 1) Quick question -----------------
@router.message(F.text.func(lambda t: norm(t) == "швидке питання"))
async def quick_entry(message: Message, state: FSMContext):
    # спершу ховаємо нижню reply-клаву
    await message.answer(" ", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Quick.category)
    await message.answer("Оберіть категорію звернення:", reply_markup=categories_inline_kb())

@router.callback_query(Quick.category, F.data == "common:back")
async def quick_cat_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Головне меню:", reply_markup=None)
    await call.message.answer("Головне меню:", reply_markup=main_reply_kb())
    await call.answer()

@router.callback_query(Quick.category, F.data.startswith("quick:cat:"))
async def quick_set_category(call: CallbackQuery, state: FSMContext):
    await state.update_data(category=call.data.split(":")[-1], pdfs=[])
    await state.set_state(Quick.brief)
    await call.message.edit_text(
        "Коротко опишіть ситуацію (до <b>500</b> символів):\n"
        "<i>Хто з ким/що сталось, які строки підтискають.</i>\n\n"
        "📎 Можна прикріпити <b>PDF</b> (до 2 файлів).",
        reply_markup=None
    )
    await call.answer()

@router.message(Quick.brief, F.document)
async def quick_brief_pdf(message: Message, state: FSMContext):
    doc = message.document
    data = await state.get_data()
    pdfs: List[str] = data.get("pdfs") or []

    if doc.mime_type not in ALLOWED_DOC_MIMES:
        return await message.answer(
            "Будь ласка, надішліть саме <b>PDF</b>-файл (до 20 МБ) або введіть короткий опис.",
            reply_markup=back_and_menu_kb()
        )
    if len(pdfs) >= MAX_PDFS:
        return await message.answer(
            f"Ви вже додали {MAX_PDFS} PDF. Далі — напишіть короткий опис (до 500 символів).",
            reply_markup=back_and_menu_kb()
        )

    pdfs.append(doc.file_id)
    await state.update_data(pdfs=pdfs)
    await message.answer(
        f"PDF отримано ({len(pdfs)}/{MAX_PDFS}). Можете надіслати ще один або напишіть опис.",
        reply_markup=back_and_menu_kb()
    )

@router.message(Quick.brief, F.text == "⬅️ Назад")
async def quick_brief_back(message: Message, state: FSMContext):
    await state.set_state(Quick.category)
    await message.answer("Оберіть категорію звернення:", reply_markup=categories_inline_kb())

@router.message(Quick.brief, F.text.len() <= 500)
async def quick_brief_ok(message: Message, state: FSMContext):
    if await route_main_button(message, state): return
    await state.update_data(brief=message.text.strip())
    await state.set_state(Quick.urgency)
    await message.answer("Яка терміновість?", reply_markup=urgency_inline_kb())

@router.message(Quick.brief)
async def quick_brief_long(message: Message):
    if norm(message.text or "") in BTN_SET:
        return
    await message.answer(
        "Будь ласка, скоротіть опис до 500 символів або натисніть «⬅️ Назад».",
        reply_markup=back_and_menu_kb()
    )

@router.callback_query(Quick.urgency, F.data == "common:back")
async def quick_urg_back(call: CallbackQuery, state: FSMContext):
    await state.set_state(Quick.brief)
    await call.message.edit_text("Коротко опишіть ситуацію (до 500 символів) або прикріпіть PDF.", reply_markup=None)
    await call.answer()

@router.callback_query(Quick.urgency, F.data.startswith("quick:urg:"))
async def quick_urgency_set(call: CallbackQuery, state: FSMContext):
    await state.update_data(urgency=call.data.split(":")[-1])
    await state.set_state(Quick.offer)
    await call.message.edit_text(
        "Для коректної відповіді пропонуємо консультацію. Оберіть формат:",
        reply_markup=consult_offer_inline_kb()
    )
    await call.answer()

@router.callback_query(Quick.offer, F.data == "common:back")
async def quick_offer_back(call: CallbackQuery, state: FSMContext):
    await state.set_state(Quick.urgency)
    await call.message.edit_text("Яка терміновість?", reply_markup=urgency_inline_kb())
    await call.answer()

@router.callback_query(Quick.offer, F.data.startswith("offer:"))
async def quick_offer(call: CallbackQuery, state: FSMContext):
    choice = call.data.split(":")[-1]
    if choice in {"30", "60"}:
        await state.update_data(duration=choice)
    await state.set_state(Quick.name)
    await call.message.edit_text("Вкажіть, будь ласка, <b>ім’я</b>.")
    await call.answer()

@router.message(Quick.name, F.text == "⬅️ Назад")
async def quick_name_back(message: Message, state: FSMContext):
    await state.set_state(Quick.offer)
    await message.answer(
        "Для коректної відповіді пропонуємо консультацію. Оберіть формат:",
        reply_markup=consult_offer_inline_kb()
    )

@router.message(Quick.name, F.text)
async def quick_name(message: Message, state: FSMContext):
    if await route_main_button(message, state): return
    await state.update_data(name=message.text.strip())
    await state.set_state(Quick.contact)
    await message.answer(
        "Дайте <b>телефон у форматі +380…</b> або <b>Telegram-нік @username</b>, "
        "або натисніть кнопку нижче:",
        reply_markup=contact_request_kb()
    )

@router.message(Quick.contact, F.content_type == ContentType.CONTACT)
async def quick_contact_shared(message: Message, state: FSMContext):
    await state.update_data(contact=message.contact.phone_number)
    await state.set_state(Quick.email)
    await message.answer("Email (необов’язково) або натисніть «⏭️ Пропустити».", reply_markup=back_menu_skip_kb())

@router.message(Quick.contact, F.text == "⬅️ Назад")
async def quick_contact_back(message: Message, state: FSMContext):
    await state.set_state(Quick.name)
    await message.answer("Вкажіть, будь ласка, ім’я.", reply_markup=back_and_menu_kb())

@router.message(Quick.contact, F.text.func(valid_contact))
async def quick_contact_ok(message: Message, state: FSMContext):
    await state.update_data(contact=message.text.strip())
    await state.set_state(Quick.email)
    await message.answer("Email (необов’язково) або натисніть «⏭️ Пропустити».", reply_markup=back_menu_skip_kb())

@router.message(Quick.contact)
async def quick_contact_bad(message: Message, state: FSMContext):
    if await route_main_button(message, state): return
    await message.answer("Невірний формат. Приклад: +380123456789 або @username",
                         reply_markup=contact_request_kb())

@router.message(Quick.email, F.text == "⬅️ Назад")
async def quick_email_back(message: Message, state: FSMContext):
    await state.set_state(Quick.contact)
    await message.answer("Дайте телефон у форматі +380… або Telegram-нік @username.",
                         reply_markup=contact_request_kb())

@router.message(Quick.email, F.text == "⏭️ Пропустити")
async def quick_email_skip(message: Message, state: FSMContext):
    await _finalize_quick(message, state, email=None)

@router.message(Quick.email, F.text)
async def quick_email(message: Message, state: FSMContext):
    if await route_main_button(message, state): return
    email = message.text.strip()
    if email != "-" and email and not valid_email(email):
        return await message.answer(
            "Схоже, email некоректний. Спробуйте ще раз або натисніть «⏭️ Пропустити».",
            reply_markup=back_menu_skip_kb()
        )
    await _finalize_quick(message, state, email=None if email in {"-", "—"} else email)

async def _finalize_quick(message: Message, state: FSMContext, email: Optional[str]):
    await state.update_data(email=email)
    data = await state.get_data()

    async with async_session() as session:
        user = await upsert_user(session, message.from_user)
        lead = await create_lead(session, user, dict(
            source="quick",
            category=data.get("category"),
            brief=data.get("brief"),
            urgency=data.get("urgency"),
            duration=data.get("duration"),
            name=data.get("name"),
            contact=data.get("contact"),
            email=data.get("email"),
            consent=True,
        ))

        # збережемо PDF у таблицю documents
        pdfs: List[str] = (data.get("pdfs") or [])[:MAX_PDFS]
        for fid in pdfs:
            session.add(Document(lead=lead, file_id=fid, kind="document", caption="quick-pdf"))

        await session.commit()

    # Google Sheets — в окремому потоці
    await asyncio.to_thread(append_lead_safe, lead)

    # сповіщення + форвард PDF адмінам
    await notify_admins_with_actions(message.bot, lead)
    pdfs = (data.get("pdfs") or [])[:MAX_PDFS]
    if pdfs:
        for idx, fid in enumerate(pdfs, 1):
            for uid in ADMIN_IDS:
                try:
                    await message.bot.send_document(uid, fid, caption=f"Лід #{lead.id} — вкладення {idx}/{len(pdfs)} (PDF)")
                except Exception as e:
                    logging.warning("Send PDF to admin %s failed: %r", uid, e)

    await message.answer("Дякуємо! Передали юристу. Очікуйте на відповідь у приватних повідомленнях.",
                         reply_markup=main_reply_kb())
    await state.clear()

# ----------------- 2) Booking (без календаря) -----------------
@router.message(F.text.func(lambda t: norm(t) == "записатися на консультацію"))
async def booking_entry(message: Message, state: FSMContext):
    await message.answer(" ", reply_markup=ReplyKeyboardRemove())  # сховати reply-клаву
    await state.set_state(Booking.fmt)
    await message.answer("Оберіть формат консультації:", reply_markup=format_inline_kb())

@router.callback_query(Booking.fmt, F.data == "common:back")
async def booking_fmt_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Головне меню:", reply_markup=None)
    await call.message.answer("Головне меню:", reply_markup=main_reply_kb())
    await call.answer()

@router.callback_query(Booking.fmt, F.data.startswith("book:fmt:"))
async def booking_set_fmt(call: CallbackQuery, state: FSMContext):
    await state.update_data(consult_format=call.data.split(":")[-1])
    await state.set_state(Booking.dur)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏱ 30 хв", callback_data="book:dur:30"),
        InlineKeyboardButton(text="⏱ 60 хв", callback_data="book:dur:60"),
    ],[
        InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")
    ]])
    await call.message.edit_text("Тривалість?", reply_markup=kb)
    await call.answer()

@router.callback_query(Booking.dur, F.data == "common:back")
async def booking_dur_back(call: CallbackQuery, state: FSMContext):
    await state.set_state(Booking.fmt)
    await call.message.edit_text("Оберіть формат консультації:", reply_markup=format_inline_kb())
    await call.answer()

@router.callback_query(Booking.dur, F.data.startswith("book:dur:"))
async def booking_set_dur(call: CallbackQuery, state: FSMContext):
    await state.update_data(duration=call.data.split(":")[-1])
    await state.set_state(Booking.name)
    await call.message.edit_text("Вкажіть, будь ласка, <b>ім’я</b>.", reply_markup=None)
    await call.answer()

@router.message(Booking.name, F.text == "⬅️ Назад")
async def booking_name_back(message: Message, state: FSMContext):
    await state.set_state(Booking.dur)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⏱ 30 хв", callback_data="book:dur:30"),
        InlineKeyboardButton(text="⏱ 60 хв", callback_data="book:dur:60"),
    ],[
        InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")
    ]])
    await message.answer("Тривалість?", reply_markup=kb)

@router.message(Booking.name, F.text)
async def booking_name(message: Message, state: FSMContext):
    if await route_main_button(message, state): return
    await state.update_data(name=message.text.strip())
    await state.set_state(Booking.contact)
    await message.answer(
        "Телефон у форматі +380… або Telegram-нік @username, або поділіться контактом:",
        reply_markup=contact_request_kb()
    )

@router.message(Booking.contact, F.content_type == ContentType.CONTACT)
async def booking_contact_shared(message: Message, state: FSMContext):
    await state.update_data(contact=message.contact.phone_number)
    await state.set_state(Booking.email)
    await message.answer("Email (необов’язково) або «⏭️ Пропустити».", reply_markup=back_menu_skip_kb())

@router.message(Booking.contact, F.text == "⬅️ Назад")
async def booking_contact_back(message: Message, state: FSMContext):
    await state.set_state(Booking.name)
    await message.answer("Вкажіть, будь ласка, ім’я.", reply_markup=back_and_menu_kb())

@router.message(Booking.contact, F.text.func(valid_contact))
async def booking_contact_ok(message: Message, state: FSMContext):
    await state.update_data(contact=message.text.strip())
    await state.set_state(Booking.email)
    await message.answer("Email (необов’язково) або «⏭️ Пропустити».", reply_markup=back_menu_skip_kb())

@router.message(Booking.contact)
async def booking_contact_bad(message: Message, state: FSMContext):
    if await route_main_button(message, state): return
    await message.answer("Невірний формат. Приклад: +380123456789 або @username",
                         reply_markup=contact_request_kb())

@router.message(Booking.email, F.text == "⏭️ Пропустити")
async def booking_email_skip(message: Message, state: FSMContext):
    await _finalize_booking(message, state, email=None)

@router.message(Booking.email, F.text == "⬅️ Назад")
async def booking_email_back(message: Message, state: FSMContext):
    await state.set_state(Booking.contact)
    await message.answer(
        "Телефон у форматі +380… або Telegram-нік @username, або поділіться контактом:",
        reply_markup=contact_request_kb()
    )

@router.message(Booking.email, F.text)
async def booking_email(message: Message, state: FSMContext):
    if await route_main_button(message, state): return
    email = message.text.strip()
    if email != "-" and email and not valid_email(email):
        return await message.answer(
            "Схоже, email некоректний. Спробуйте ще раз або «⏭️ Пропустити».",
            reply_markup=back_menu_skip_kb()
        )
    await _finalize_booking(message, state, email=None if email in {"-", "—"} else email)

async def _finalize_booking(message: Message, state: FSMContext, email: Optional[str]):
    data = await state.get_data()
    async with async_session() as session:
        user = await upsert_user(session, message.from_user)
        lead = await create_lead(session, user, dict(
            source="booking",
            consult_format=data.get("consult_format"),
            duration=data.get("duration"),
            name=data.get("name"),
            contact=data.get("contact"),
            email=email,
            consent=True,
        ))
        await session.commit()

    await asyncio.to_thread(append_lead_safe, lead)
    await notify_admins_with_actions(message.bot, lead)
    await message.answer("Готово! Ми зафіксували консультацію. Незабаром підтвердимо деталі.",
                         reply_markup=main_reply_kb())
    await state.clear()

# ----------------- 3) Document check -----------------
@router.message(Command("document"))
async def document_entry(message: Message, state: FSMContext):
    await state.set_state(DocumentFlow.type)
    await message.answer("Який тип документа?", reply_markup=document_type_inline_kb())

@router.callback_query(DocumentFlow.type, F.data == "common:back")
async def document_type_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Головне меню:", reply_markup=None)
    await call.message.answer("Головне меню:", reply_markup=main_reply_kb())
    await call.answer()

@router.callback_query(DocumentFlow.type, F.data.startswith("doc:type:"))
async def document_set_type(call: CallbackQuery, state: FSMContext):
    await state.update_data(doc_type=call.data.split(":")[-1])
    await state.set_state(DocumentFlow.upload)
    await call.message.edit_text("Надішліть файл (PDF/JPG/PNG/DOCX, до 20 МБ) і 1–2 речення контексту.")
    await call.answer()

@router.message(DocumentFlow.upload, F.text == "⬅️ Назад")
async def document_upload_back(message: Message, state: FSMContext):
    await state.set_state(DocumentFlow.type)
    await message.answer("Який тип документа?", reply_markup=document_type_inline_kb())

@router.message(DocumentFlow.upload, F.content_type.in_({ContentType.DOCUMENT, ContentType.PHOTO}))
async def document_got_file(message: Message, state: FSMContext):
    data = await state.get_data()
    files = data.get("doc_file_ids") or []
    if message.document:
        files.append(message.document.file_id)
    elif message.photo:
        files.append(message.photo[-1].file_id)
    await state.update_data(doc_file_ids=files)
    await message.answer(
        "Файл отримано. Можете надіслати ще або напишіть короткий коментар (1–2 речення).",
        reply_markup=back_and_menu_kb()
    )

@router.message(DocumentFlow.upload, F.text)
async def document_comment(message: Message, state: FSMContext):
    await state.update_data(doc_comment=message.text.strip())
    await state.set_state(DocumentFlow.plan)
    await message.answer("Оберіть формат перевірки:", reply_markup=document_plan_inline_kb())

@router.callback_query(DocumentFlow.plan, F.data == "common:back")
async def document_plan_back(call: CallbackQuery, state: FSMContext):
    await state.set_state(DocumentFlow.upload)
    await call.message.edit_text("Надішліть файл та короткий коментар (1–2 речення).", reply_markup=None)
    await call.answer()

@router.callback_query(DocumentFlow.plan, F.data.startswith("doc:plan:"))
async def document_finish(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    async with async_session() as session:
        user = await upsert_user(session, call.from_user)
        lead = await create_lead(session, user, dict(
            source="document",
            category=data.get("doc_type"),
            brief=data.get("doc_comment"),
            consent=True,
        ))
        await session.commit()

    await asyncio.to_thread(append_lead_safe, lead)
    await notify_admins_with_actions(call.bot, lead)
    await call.message.edit_text("Отримали. Візьмемо в роботу після підтвердження умов/вартості. Менеджер напише вам.")
    await state.clear()
    await call.answer()

# ----------------- Fallback first touch -----------------
@router.message(F.text, ~F.text.startswith("/"))
async def first_touch_fallback(message: Message, state: FSMContext):
    st = await state.get_state()
    if st is None:
        await message.answer("Щоб почати, натисніть «Розпочати».", reply_markup=start_kb())

@router.message(Command("whoami"))
async def whoami(message: Message):
    await message.answer(f"Ваш ID: <code>{message.from_user.id}</code>")
