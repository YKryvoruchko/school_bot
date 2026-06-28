from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy import select
from starlette.staticfiles import StaticFiles
from aiogram import Bot

from admin import (
    MEDIA_DIR, ClassAdmin, NewsAdmin, SchoolConfigAdmin,
    SectionAdmin, TelegramAccountAdmin, WebUserAdmin, set_bot_instance,
)
from auth import AdminAuth, hash_password
from config import BOT_TOKEN, SECRET_KEY
from database import async_session_maker, engine
from models import Base, WebUser, WebUserRole

BASE_DIR = Path(__file__).resolve().parent
ADMIN_STATIC_DIR = BASE_DIR / "static"


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        result = await session.execute(select(WebUser))
        if result.scalars().first() is not None:
            return

        session.add(WebUser(
            username="admin",
            password_hash=hash_password("admin"),
            full_name="Директор",
            role=WebUserRole.superadmin,
        ))
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    set_bot_instance(bot)
    try:
        yield
    finally:
        await bot.session.close()


app = FastAPI(lifespan=lifespan)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/assets", StaticFiles(directory=ADMIN_STATIC_DIR), name="assets")

admin = Admin(app, engine, authentication_backend=AdminAuth(secret_key=SECRET_KEY), title="School Bot")

admin.add_view(SchoolConfigAdmin)
admin.add_view(WebUserAdmin)
admin.add_view(ClassAdmin)
admin.add_view(TelegramAccountAdmin)
admin.add_view(SectionAdmin)
admin.add_view(NewsAdmin)