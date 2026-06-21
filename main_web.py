from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy import select
from starlette.staticfiles import StaticFiles

from admin import MEDIA_DIR, ClassAdmin, NewsAdmin, ParentAdmin, SchoolAdmin, UserAdmin
from auth import AdminAuth, hash_password
from config import SECRET_KEY
from database import async_session_maker, engine
from models import Base, User, UserRole

BASE_DIR = Path(__file__).resolve().parent
ADMIN_STATIC_DIR = BASE_DIR / "static"


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        result = await session.execute(select(User))
        if result.scalars().first() is not None:
            return

        superadmin = User(
            username="admin",
            password_hash=hash_password("admin"),
            role=UserRole.superadmin,
            school_id=None,
        )
        session.add(superadmin)
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/assets", StaticFiles(directory=ADMIN_STATIC_DIR), name="assets")

admin = Admin(
    app,
    engine,
    authentication_backend=AdminAuth(secret_key=SECRET_KEY),
    title="School Bot",
)

admin.add_view(SchoolAdmin)
admin.add_view(UserAdmin)
admin.add_view(ClassAdmin)
admin.add_view(ParentAdmin)
admin.add_view(NewsAdmin)
