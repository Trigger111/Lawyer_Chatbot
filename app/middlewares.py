# app/middlewares.py
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable

from .db import async_session, upsert_user, log_message

class DBUserMiddleware(BaseMiddleware):
    """Зберігає/оновлює користувача та лог входячих повідомлень."""
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        from_user = event.from_user if hasattr(event, "from_user") else None
        if from_user:
            async with async_session() as session:
                user = await upsert_user(session, from_user)
                # лог вхідного тексту
                text = None
                if isinstance(event, Message):
                    text = event.text or event.caption
                await log_message(session, user, "in", text)
                await session.commit()
                # пробросимо user у data, якщо схочеш використати
                data["db_user"] = user
        return await handler(event, data)
