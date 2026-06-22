import asyncio
import uuid
from pathlib import Path

from aiogram import Bot
from broadcaster import broadcast_news, update_news_in_telegram
from config import BOT_TOKEN

from sqladmin import ModelView
from starlette.requests import Request
from wtforms import BooleanField, FileField
from wtforms.widgets import CheckboxInput

from auth import hash_password
from models import Class, News, Parent, School, User, parent_class_association

bot_instance = Bot(token=BOT_TOKEN)

MEDIA_DIR = Path(__file__).resolve().parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class SafeBooleanField(BooleanField):
    """В sqladmin 0.27.2 собственный BooleanInputWidget наследуется напрямую
    от wtforms.widgets.Input, а не от CheckboxInput, и поэтому у него нет
    атрибута validation_attrs. При этом сам sqladmin требует wtforms>=3.1,
    где этот атрибут обязателен — в итоге ЛЮБОЙ чекбокс в форме падает
    с 500 ошибкой (AttributeError: 'BooleanInputWidget' object has no
    attribute 'validation_attrs'). Используем родной, рабочий виджет
    wtforms вместо сломанного из sqladmin."""

    widget = CheckboxInput()


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
    name = "Школа"
    name_plural = "Школи"
    icon = "fa-solid fa-school"
    column_list = [School.id, School.name]
    form_columns = [School.name]

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)


class UserAdmin(ModelView, model=User):
    name = "Користувач"
    name_plural = "Користувачі"
    icon = "fa-solid fa-user-shield"
    column_list = [User.id, User.username, User.role, User.school]
    form_columns = [User.username, User.password_hash, User.role, User.school]
    form_args = {"password_hash": {"label": "Пароль"}}

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        raw_password = data.get("password_hash")
        if raw_password:
            data["password_hash"] = hash_password(raw_password)
        elif not is_created:
            data.pop("password_hash", None)


class ClassAdmin(TenantScopedView, model=Class):
    name = "Клас"
    name_plural = "Класи"
    icon = "fa-solid fa-chalkboard-user"
    column_list = [Class.id, Class.name, Class.school]
    form_columns = [Class.name, Class.school]


class ParentAdmin(ModelView, model=Parent):
    name = "Батько/мати"
    name_plural = "Батьки"
    icon = "fa-solid fa-people-roof"
    column_list = [Parent.id, Parent.full_name, Parent.telegram_id, Parent.is_blocked]
    column_labels = {
        Parent.id: "ID",
        Parent.full_name: "Ім'я",
        Parent.telegram_id: "Telegram ID",
        Parent.is_blocked: "Заблоковано",
    }
    column_searchable_list = [Parent.telegram_id, Parent.full_name]
    column_sortable_list = [Parent.id, Parent.full_name, Parent.is_blocked]
    column_default_sort = [(Parent.id, True)]
    form_columns = [Parent.is_blocked]
    form_overrides = {"is_blocked": SafeBooleanField}
    can_create = False
    can_delete = False

    def is_accessible(self, request: Request) -> bool:
        return True

    def _scope(self, stmt, request: Request):
        if is_superadmin(request):
            return stmt
        return (
            stmt.join(parent_class_association, parent_class_association.c.parent_id == Parent.id)
            .join(Class, Class.id == parent_class_association.c.class_id)
            .where(Class.school_id == current_school_id(request))
            .distinct()
        )

    def list_query(self, request: Request):
        return self._scope(super().list_query(request), request)

    def count_query(self, request: Request):
        return self._scope(super().count_query(request), request)

    def details_query(self, request: Request):
        return self._scope(super().details_query(request), request)

    def form_edit_query(self, request: Request):
        return self._scope(super().form_edit_query(request), request)

    def _owns(self, request: Request, model: Parent | None) -> bool:
        if is_superadmin(request):
            return True
        school_id = current_school_id(request)
        return model is not None and any(c.school_id == school_id for c in model.classes)

    async def check_can_view_details(self, request: Request, model) -> bool:
        return self._owns(request, model)

    async def check_can_edit(self, request: Request, model) -> bool:
        return self._owns(request, model)


class NewsAdmin(TenantScopedView, model=News):
    name = "Новина"
    name_plural = "Новини"
    icon = "fa-solid fa-newspaper"
    column_list = [News.id, News.title, News.author, News.school]
    column_labels = {
        News.id: "ID",
        News.title: "Заголовок",
        News.author: "Автор",
        News.school: "Школа",
    }
    column_formatters = {
        News.image_url: lambda m, a: "Зображення є" if m.image_url else "-",
    }
    form_columns = [News.title, News.text, News.image_url, News.school, News.classes, News.author]
    form_labels = {
        "title": "Заголовок",
        "text": "Текст новини",
        "image_url": "Зображення (необов'язково)",
        "classes": "Класи (якщо не вибрати, новина прийде всім класам школи)",
    }
    form_overrides = {"image_url": FileField}

    async def _save_uploaded_image(self, data: dict) -> None:
        upload = data.get("image_url")

        # Уже строка из БД (старое значение) — не трогаем
        if upload is None or isinstance(upload, str):
            return

        # UploadFile без файла — убираем ключ, чтобы не записать объект в БД
        if not hasattr(upload, "filename") or not upload.filename:
            data.pop("image_url", None)
            return

        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            data.pop("image_url", None)
            return

        filename = f"{uuid.uuid4().hex}{ext}"
        content = await upload.read()
        (MEDIA_DIR / filename).write_bytes(content)
        data["image_url"] = f"media/{filename}"

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        await super().on_model_change(data, model, is_created, request)
        await self._save_uploaded_image(data)
        if not is_superadmin(request):
            data["author"] = request.session["user_id"]

    async def insert_model(self, request: Request, data: dict):
        model = await super().insert_model(request, data)

        async def _send():
            async with bot_instance:
                await broadcast_news(model.id, bot_instance)

        asyncio.create_task(_send())
        return model

    async def update_model(self, request: Request, pk: str, data: dict):
        model = await super().update_model(request, pk, data)

        async def _send():
            async with bot_instance:
                await update_news_in_telegram(model.id, bot_instance)

        asyncio.create_task(_send())
        return model