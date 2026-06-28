import warnings

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from passlib.context import CryptContext

from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request

from database import async_session_maker
from models import WebUser, WebUserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))

        async with async_session_maker() as session:
            result = await session.execute(select(WebUser).where(WebUser.username == username))
            user = result.scalar_one_or_none()

        if user is None or not verify_password(password, user.password_hash):
            return False

        request.session.update({
            "user_id": user.id,
            "username": user.username,
            "role": user.role.value,
            "class_id": user.class_id,
            "full_name": user.full_name,
        })
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return "user_id" in request.session


def is_superadmin(request: Request) -> bool:
    return request.session.get("role") == WebUserRole.superadmin.value


def is_admin_or_above(request: Request) -> bool:
    return request.session.get("role") in (WebUserRole.superadmin.value, WebUserRole.admin.value)


def is_authenticated(request: Request) -> bool:
    return "user_id" in request.session


def session_class_id(request: Request) -> int | None:
    return request.session.get("class_id")


def session_role(request: Request) -> str | None:
    return request.session.get("role")