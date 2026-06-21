import asyncio

from aiogram import Bot
from broadcaster import broadcast_news
from config import BOT_TOKEN

from sqladmin import ModelView
from starlette.requests import Request

from auth import hash_password
from models import Class, News, Parent, School, User

bot_instance = Bot(token=BOT_TOKEN)


def is_superadmin(request: Request) -> bool:
    return request.session.get("role") == "superadmin"


def current_school_id(request: Request) -> int | None:
    return request.session.get("school_id")


class TenantScopedView(ModelView):
    def _scope(self, stmt, request: Request):
        if is_superadmin(request):
            return stmt
        return stmt.where(self.model.school_id == current_school_id(request))

    def _owns(self, request: Request, model) -> bool:
        if is_superadmin(request):
            return True
        return model is not None and model.school_id == current_school_id(request)

    def list_query(self, request: Request):
        return self._scope(super().list_query(request), request)

    def count_query(self, request: Request):
        return self._scope(super().count_query(request), request)

    def details_query(self, request: Request):
        return self._scope(super().details_query(request), request)

    def form_edit_query(self, request: Request):
        return self._scope(super().form_edit_query(request), request)

    async def check_can_view_details(self, request: Request, model) -> bool:
        return self._owns(request, model)

    async def check_can_edit(self, request: Request, model) -> bool:
        return self._owns(request, model)

    async def check_can_delete(self, request: Request, model) -> bool:
        return self._owns(request, model)

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        if not is_superadmin(request):
            data["school"] = current_school_id(request)


class SchoolAdmin(ModelView, model=School):
    column_list = [School.id, School.name]
    form_columns = [School.name]

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)


class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.username, User.role, User.school]
    form_columns = [User.username, User.password_hash, User.role, User.school]
    form_args = {"password_hash": {"label": "Password"}}

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        raw_password = data.get("password_hash")
        if raw_password:
            data["password_hash"] = hash_password(raw_password)
        elif not is_created:
            data.pop("password_hash", None)


class ClassAdmin(TenantScopedView, model=Class):
    column_list = [Class.id, Class.name, Class.school]
    form_columns = [Class.name, Class.school]


class ParentAdmin(ModelView, model=Parent):
    column_list = [Parent.id, Parent.full_name, Parent.telegram_id]

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)


class NewsAdmin(TenantScopedView, model=News):
    column_list = [News.id, News.title, News.author, News.school]
    form_columns = [News.title, News.text, News.school, News.classes, News.author]

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        await super().on_model_change(data, model, is_created, request)
        if not is_superadmin(request):
            data["author"] = request.session["user_id"]

    async def insert_model(self, request: Request, data: dict):
        model = await super().insert_model(request, data)
        asyncio.create_task(broadcast_news(model.id, bot_instance))
        return model