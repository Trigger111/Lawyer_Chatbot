# app/keyboards.py
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

# --- Таймзона з безпечним фолбеком ---
def get_kyiv_tz():
    try:
        return ZoneInfo("Europe/Kyiv")
    except Exception:
        try:
            import tzdata  # noqa
            return ZoneInfo("Europe/Kyiv")
        except Exception:
            return timezone(timedelta(hours=3))  # останній варіант: фіксований UTC+3

TZ = get_kyiv_tz()

# ---------------- Reply keyboards ----------------
def start_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🚀 Розпочати")]],
        resize_keyboard=True,
        input_field_placeholder="Натисніть «Розпочати»",
    )

def main_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚡️ Швидке питання")],
            [KeyboardButton(text="📞 Записатися на консультацію")],
            [KeyboardButton(text="📚 Статті та гіди")],
            [KeyboardButton(text="👩‍⚖️ Про юриста / Контакти")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Оберіть опцію",
    )

def back_menu_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Меню")]],
        resize_keyboard=True,
    )

def back_and_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Меню")]],
        resize_keyboard=True,
    )

def menu_only_kb() -> ReplyKeyboardMarkup:
    """Окрема клава лише з «Головне меню» — для розділів «Статті…», «Про юриста…»."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Меню")]],
        resize_keyboard=True,
        input_field_placeholder="Натисніть «Меню», щоб повернутися",
    )

def contact_request_kb() -> ReplyKeyboardMarkup:
    """Кнопка 'поділитися контактом' + назад/меню."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поділитися контактом", request_contact=True)],
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Меню")],
        ],
        resize_keyboard=True,
    )

def back_menu_skip_kb() -> ReplyKeyboardMarkup:
    """Назад/меню + явна кнопка пропуска."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Меню")],
            [KeyboardButton(text="⏭️ Пропустити")],
        ],
        resize_keyboard=True,
    )

# ---------------- Inline keyboards ----------------
# лише 4 категорії
CATEGORIES = [
    ("⚖️ Кримінальне",   "criminal"),
    ("🏢 Господарське",  "commercial"),
    ("📜 Цивільне",      "civil"),
    ("🧩 Інше",          "other"),
]

def categories_inline_kb() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(CATEGORIES), 2):
        chunk = CATEGORIES[i:i+2]
        rows.append([InlineKeyboardButton(text=t, callback_data=f"quick:cat:{s}") for t, s in chunk])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def urgency_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Сьогодні", callback_data="quick:urg:today"),
            InlineKeyboardButton(text="🟠 1–2 дні", callback_data="quick:urg:1-2"),
            InlineKeyboardButton(text="🟢 Не терміново", callback_data="quick:urg:later"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")],
    ])

def consult_offer_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱️ 30 хв", callback_data="offer:30"),
            InlineKeyboardButton(text="⏱️ 60 хв", callback_data="offer:60"),
        ],
        [InlineKeyboardButton(text="⏭️ Пропустити", callback_data="offer:skip")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")],
    ])

def format_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📞 Телефон", callback_data="book:fmt:phone"),
            InlineKeyboardButton(text="📲 Telegram-дзвінок", callback_data="book:fmt:tg"),
        ],
        [InlineKeyboardButton(text="🎥 Zoom", callback_data="book:fmt:zoom")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")],
    ])

# ------ Документ-потік ------
def document_type_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📄 Договір",  callback_data="doc:type:contract"),
            InlineKeyboardButton(text="📨 Претензія", callback_data="doc:type:claim"),
        ],
        [InlineKeyboardButton(text="🧾 Інше", callback_data="doc:type:other")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")],
    ])

def document_plan_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Експрес-рев’ю з помітками", callback_data="doc:plan:express")],
        [InlineKeyboardButton(text="📞 Розбір з дзвінком",         callback_data="doc:plan:call")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")],
    ])

# (утиліти — якщо знадобляться тайм-слоти)
def generate_time_slots(days_ahead: int = 3) -> list[str]:
    base = datetime.now(tz=TZ)
    hours = [time(10, 0), time(12, 0), time(15, 0), time(18, 0)]
    slots: list[str] = []
    for d in range(days_ahead + 1):
        day = (base + timedelta(days=d)).date()
        for h in hours:
            dt = datetime.combine(day, h, tzinfo=TZ)
            if dt > base + timedelta(hours=1):
                slots.append(dt.isoformat())
    return slots[:6]

def time_slots_inline_kb() -> InlineKeyboardMarkup:
    slots = generate_time_slots()
    rows = [
        [InlineKeyboardButton(
            text=datetime.fromisoformat(s).strftime("%a %d.%m %H:%M"),
            callback_data=f"book:slot:{s}"
        )]
        for s in slots
    ]
    rows.append([InlineKeyboardButton(text="👩‍💼 Написати менеджеру", callback_data="book:alt:manager")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="common:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
