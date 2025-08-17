# main.py
import asyncio
import logging
import os
from dotenv import load_dotenv

# .env -> os.environ
load_dotenv()

from app.db import init_db
from app.middlewares import DBUserMiddleware

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


def _read_token() -> str:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    # иногда в .env копируют с лишними кавычками — уберём
    if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
        token = token[1:-1]
    if not token or ":" not in token:
        raise RuntimeError("BOT_TOKEN пустой или неверного формата. Укажи в .env: BOT_TOKEN=....")
    return token


API_TOKEN = _read_token()


async def main():
    # Жёстко инициализируем логгер, чтобы всегда были строки запуска
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    # создаём таблицы, если ещё нет
    await init_db()

    bot = Bot(API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # мидлварь логирования
    dp.message.middleware(DBUserMiddleware())
    dp.callback_query.middleware(DBUserMiddleware())

    # роутеры
    from app.admin import router as admin_router
    from app.handlers import router as handlers_router
    dp.include_router(admin_router)
    dp.include_router(handlers_router)

    # на всякий случай убираем вебхук + старые апдейты
    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    logging.info("Authorized as @%s (id=%s)", me.username, me.id)
    logging.info("Starting polling...")
    print(f"✅ Bot @{me.username} ({me.id}) is running. Press Ctrl+C to stop.")

    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        pass
    finally:
        await bot.session.close()
        logging.info("Polling stopped.")
        print("🛑 Polling stopped.")


if __name__ == "__main__":
    asyncio.run(main())
