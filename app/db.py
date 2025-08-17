# app/db.py
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional, Literal

from sqlalchemy import (
    String, Text, Integer, BigInteger, DateTime, Boolean, ForeignKey, JSON, select, event
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# --- БД: SQLite --------------------------------------------------------------
DATABASE_URL = "sqlite+aiosqlite:///./data/bot.db"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)

# ВАЖНО: для SQLite включаем поддержку внешних ключей (PRAGMA foreign_keys=ON)
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, conn_record):
    try:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    except Exception:
        # на всякий — молча игнорируем, чтобы не мешать запуску на других СУБД
        pass

class Base(DeclarativeBase):
    pass

def utcnow():
    return datetime.now(timezone.utc)

# ---------------- Моделі -----------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    leads: Mapped[list["Lead"]] = relationship(back_populates="user")
    messages: Mapped[list["MessageLog"]] = relationship(back_populates="user")


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # если пользователя удалят — удалим и лиды
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    source: Mapped[str] = mapped_column(String(32))
    category: Mapped[Optional[str]] = mapped_column(String(32))
    brief: Mapped[Optional[str]] = mapped_column(Text)
    urgency: Mapped[Optional[str]] = mapped_column(String(16))
    consult_format: Mapped[Optional[str]] = mapped_column(String(16))
    duration: Mapped[Optional[str]] = mapped_column(String(8))
    slot_iso: Mapped[Optional[str]] = mapped_column(String(40))

    name: Mapped[Optional[str]] = mapped_column(String(128))
    contact: Mapped[Optional[str]] = mapped_column(String(128))
    email: Mapped[Optional[str]] = mapped_column(String(128))
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="leads")

    # КЛЮЧЕВОЕ: каскад на дочерние файлы + passive_deletes,
    # чтобы SQLAlchemy не пытался ставить lead_id = NULL
    documents: Mapped[list["Document"]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # КЛЮЧЕВОЕ: ON DELETE CASCADE и NOT NULL
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_id: Mapped[str] = mapped_column(String(256))
    file_unique_id: Mapped[Optional[str]] = mapped_column(String(128))
    kind: Mapped[Optional[str]] = mapped_column(String(32))
    caption: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    lead: Mapped["Lead"] = relationship(back_populates="documents")


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(3))  # "in"|"out"
    text: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="messages")

# -------------- init ---------------------------------------------------------
async def init_db() -> None:
    """Створити таблиці, якщо їх ще немає."""
    from pathlib import Path
    Path("data").mkdir(exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- CRUD-хелпери ------------------------------------------------------------
async def upsert_user(session: AsyncSession, tg_user) -> User:
    """Создаёт/обновляет пользователя атомарно через SQLite UPSERT."""
    values = {
        "tg_id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "last_name": tg_user.last_name,
        "language_code": getattr(tg_user, "language_code", None),
        "last_seen_at": utcnow(),
    }
    stmt = sqlite_insert(User).values(**values).on_conflict_do_update(
        index_elements=[User.tg_id],
        set_={
            "username": sqlite_insert(User).excluded.username,
            "first_name": sqlite_insert(User).excluded.first_name,
            "last_name": sqlite_insert(User).excluded.last_name,
            "language_code": sqlite_insert(User).excluded.language_code,
            "last_seen_at": sqlite_insert(User).excluded.last_seen_at,
        },
    )
    await session.execute(stmt)
    res = await session.execute(select(User).where(User.tg_id == tg_user.id))
    return res.scalar_one()

async def log_message(session: AsyncSession, user: User, direction: Literal["in","out"], text: str | None, payload: dict | None = None):
    session.add(MessageLog(user_id=user.id, direction=direction, text=text, payload=payload))

async def create_lead(session: AsyncSession, user: User, data: dict) -> Lead:
    """Создаёт лид и сразу выдаёт его id до commit."""
    lead = Lead(user=user, **data)
    session.add(lead)
    await session.flush()
    return lead
