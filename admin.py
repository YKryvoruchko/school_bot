import asyncio
import io
import logging
import re
import uuid
from pathlib import Path

from PIL import Image
from pillow_heif import register_heif_opener
from sqlalchemy.exc import IntegrityError

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from broadcaster import broadcast_news, update_news_in_telegram
from config import BOT_TOKEN

from sqladmin import ModelView
from starlette.requests import Request
from wtforms import BooleanField, FileField
from wtforms.widgets import CheckboxInput

from auth import hash_password
from models import Class, News, Parent, School, User, parent_class_association

register_heif_opener()

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(__file__).resolve().parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SafeBooleanField(BooleanField):
    widget = CheckboxInput()


def is_superadmin(request: Request) -> bool:
    return request.session.get("role") == "superadmin"


def current_school_id(request: Request) -> int | None:
    return request.session.get("school_id")


def _make_bot() -> Bot:
    return Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


_COLUMN_LABELS: dict[str, str] = {
    "name":        "Назва",
    "username":    "Логін",
    "telegram_id": "Telegram ID",
    "author_id":   "Автор",
    "school_id":   "Школа",
    "class_id":    "Клас",
    "news_id":     "Новина",
    "parent_id":   "Батько/мати",
}


def _friendly_integrity_error(exc: IntegrityError) -> str:
    """
    Переводит сырое сообщение SQLite IntegrityError в читаемый украинский текст.
    sqladmin передаёт это значение как context["error"] и рендерит в форме.
    """
    raw = str(exc.orig) if hasattr(exc, "orig") else str(exc)

    m = re.search(r"UNIQUE constraint failed: \w+\.(\w+)", raw)
    if m:
        label = _COLUMN_LABELS.get(m.group(1), f'«{m.group(1)}»')
        return f"Помилка: поле {label} має бути унікальним — такий запис вже існує."

    m = re.search(r"NOT NULL constraint failed: \w+\.(\w+)", raw)
    if m:
        label = _COLUMN_LABELS.get(m.group(1), f'«{m.group(1)}»')
        return f"Помилка: поле {label} є обов'язковим."

    if "FOREIGN KEY constraint failed" in raw:
        return "Помилка: пов'язаний запис не знайдено. Перевірте правильність вибраних даних."

    logger.error("Unhandled IntegrityError: %s", raw)
    return f"Помилка бази даних: {raw}"



class ErrorHandlingMixin:
    """
    Перехватывает IntegrityError в insert/update и заменяет сырое сообщение
    SQLAlchemy читаемым текстом — sqladmin покажет его в форме как alert-danger.
    """

    async def insert_model(self, request: Request, data: dict):
        try:
            return await super().insert_model(request, data)
        except IntegrityError as exc:
            raise ValueError(_friendly_integrity_error(exc)) from exc

    async def update_model(self, request: Request, pk: str, data: dict):
        try:
            return await super().update_model(request, pk, data)
        except IntegrityError as exc:
            raise ValueError(_friendly_integrity_error(exc)) from exc


class TenantScopedView(ErrorHandlingMixin, ModelView):
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
            data["school_id"] = current_school_id(request)


class SchoolAdmin(ErrorHandlingMixin, ModelView, model=School):
    name = "Школа"
    name_plural = "Школи"
    icon = "fa-solid fa-school"
    column_list = [School.id, School.name]
    form_columns = [School.name]

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)



class UserAdmin(ErrorHandlingMixin, ModelView, model=User):
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


# ---------------------------------------------------------------------------
# ClassAdmin
# ---------------------------------------------------------------------------

class ClassAdmin(TenantScopedView, model=Class):
    name = "Клас"
    name_plural = "Класи"
    icon = "fa-solid fa-chalkboard-user"
    column_list = [Class.id, Class.name, Class.school]
    form_columns = [Class.name, Class.school]


# ---------------------------------------------------------------------------
# ParentAdmin
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# NewsAdmin
# ---------------------------------------------------------------------------

class NewsAdmin(TenantScopedView, model=News):
    name = "Новина"
    name_plural = "Новини"
    icon = "fa-solid fa-newspaper"
    column_list = [News.id, News.title, News.author, News.school]

    column_labels = {
        News.id: "ID",
        News.title: "Заголовок",
        News.text: "Текст новини",
        News.author: "Автор",
        News.school: "Школа",
        News.classes: "Класи (якщо не вибрати, новина прийде всім класам школи)",
        News.image_url: "Зображення (необов'язково)",
        News.is_global: "Для ВСІХ шкіл (однією кнопкою)",
    }

    column_formatters = {
        News.image_url: lambda m, a: "Зображення є" if m.image_url else "-",
    }

    form_columns = [News.title, News.text, News.image_url, News.is_global, News.school, News.classes, News.author]

    form_overrides = {
        "image_url": FileField,
        "is_global": SafeBooleanField,
    }

    form_args = {
        "is_global": {
            "render_kw": {
                "class": "form-check-input",
                "style": "width: 1.5rem; height: 1.5rem; margin-top: 0.5rem; cursor: pointer;",
            }
        }
    }

    async def _save_uploaded_image(self, data: dict, model) -> None:
        upload = data.get("image_url")

        if upload is None or isinstance(upload, str):
            return

        if not hasattr(upload, "filename") or not upload.filename:
            if model is not None and model.image_url:
                data["image_url"] = model.image_url
            else:
                data.pop("image_url", None)
            return

        content = await upload.read()
        try:
            img = Image.open(io.BytesIO(content))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            filename = f"{uuid.uuid4().hex}.jpg"
            img.save(MEDIA_DIR / filename, format="JPEG", quality=85)
            data["image_url"] = f"media/{filename}"
        except Exception as exc:
            logger.warning("Не вдалося зберегти зображення: %s", exc)
            if model is not None and model.image_url:
                data["image_url"] = model.image_url
            else:
                data.pop("image_url", None)
            raise ValueError(
                "Не вдалося обробити зображення. "
                "Перевірте формат файлу (підтримуються JPEG, PNG, HEIC)."
            ) from exc

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        await super().on_model_change(data, model, is_created, request)
        await self._save_uploaded_image(data, model)

        if not is_superadmin(request):
            data["author_id"] = request.session["user_id"]
            data["is_global"] = False
        else:
            if not data.get("author_id") and not data.get("author"):
                data["author_id"] = request.session["user_id"]

    async def insert_model(self, request: Request, data: dict):
        try:
            model = await super().insert_model(request, data)
        except ValueError:
            raise
        except IntegrityError as exc:
            raise ValueError(_friendly_integrity_error(exc)) from exc

        async def _send():
            bot = _make_bot()
            async with bot:
                await broadcast_news(model.id, bot)

        asyncio.create_task(_send())
        return model

    async def update_model(self, request: Request, pk: str, data: dict):
        try:
            model = await super().update_model(request, pk, data)
        except ValueError:
            raise
        except IntegrityError as exc:
            raise ValueError(_friendly_integrity_error(exc)) from exc

        async def _send():
            bot = _make_bot()
            async with bot:
                await update_news_in_telegram(model.id, bot)

        asyncio.create_task(_send())
        return model