import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import BOT_TOKEN
from database import engine
from handlers import router
from models import Base

logger = logging.getLogger(__name__)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _build_storage():
    if os.getenv("USE_MEMORY_STORAGE", "0") == "1":
        logger.warning("Using MemoryStorage! FSM state will be lost on restart.")
        from aiogram.fsm.storage.memory import MemoryStorage
        return MemoryStorage()

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(redis_url)
        logger.info("Using RedisStorage: %s", redis_url)
        return storage
    except Exception as e:
        raise RuntimeError(
            f"Failed to connect to Redis at {redis_url}. "
            "Set REDIS_URL in .env or USE_MEMORY_STORAGE=1 for development. "
            f"Error: {e}"
        ) from e


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    storage = _build_storage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())