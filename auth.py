import hashlib

from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request

from database import async_session_maker
from models import User


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        async with async_session_maker() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()

        if user is None or not verify_password(password, user.password_hash):
            return False

        request.session.update(
            {
                "user_id": user.id,
                "username": user.username,
                "role": user.role.value,
                "school_id": user.school_id,
            }
        )
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return "user_id" in request.session