import asyncio
import json as _json
import logging
import uuid
from pathlib import Path

from aiogram import Bot
from broadcaster import broadcast_news, update_news_in_telegram
from sqladmin import ModelView
from sqlalchemy import select, update as sa_update, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.exc import StaleDataError
from starlette.requests import Request
from wtforms import BooleanField as _BooleanField, FileField, SelectField, SelectMultipleField
from wtforms.widgets import CheckboxInput

from auth import hash_password, is_admin_or_above, is_authenticated, is_superadmin, session_class_id, session_role
from database import async_session_maker
from models import Class, News, NewsTarget, SchoolConfig, Section, SectionType, TelegramAccount, WebUser, WebUserRole

logger = logging.getLogger(__name__)

bot_instance: "Bot | None" = None


def set_bot_instance(bot: "Bot") -> None:
    global bot_instance
    bot_instance = bot


MEDIA_DIR = Path(__file__).resolve().parent / "media"
MEDIA_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

_background_tasks: set[asyncio.Task] = set()


def _fire_and_track(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_task_result)
    return task


def _log_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        logger.warning("Background task cancelled")
        return
    exc = task.exception()
    if exc:
        logger.error("Background task failed: %s", exc, exc_info=exc)


MAGIC_SIGNATURES: dict[bytes, str] = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


def _detect_mime(content: bytes) -> str:
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    for sig, mime in MAGIC_SIGNATURES.items():
        if content[:len(sig)] == sig:
            return mime
    try:
        import magic
        return magic.from_buffer(content[:2048], mime=True)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("python-magic failed: %s", exc)
    return "application/octet-stream"


def _delete_media_file(image_url: str | None) -> None:
    if not image_url or image_url.startswith(("http://", "https://")):
        return
    file_path = (MEDIA_DIR.parent / image_url).resolve()
    try:
        file_path.relative_to(MEDIA_DIR.resolve())
    except ValueError:
        logger.warning("Path traversal attempt: %s", file_path)
        return
    file_path.unlink(missing_ok=True)
    logger.info("Deleted media: %s", file_path)


async def _save_uploaded_image(data: dict, model=None) -> None:
    upload = data.pop("upload_image", None)

    if upload is None or isinstance(upload, str) or not getattr(upload, "filename", None):
        if "image_url" not in data:
            data["image_url"] = getattr(model, "image_url", None) if model else None
        return

    ext = Path(upload.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning("Rejected extension: %s", upload.filename)
        return

    file_content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(file_content) > MAX_UPLOAD_BYTES:
        logger.warning("Rejected oversized file: %d bytes", len(file_content))
        return

    real_mime = _detect_mime(file_content)
    if real_mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        logger.warning("Rejected MIME: %s for file %s", real_mime, upload.filename)
        return

    filename = f"{uuid.uuid4().hex}{ext}"
    (MEDIA_DIR / filename).write_bytes(file_content)
    data["image_url"] = f"media/{filename}"


class _SafeCheckboxInput(CheckboxInput):
    validation_attrs: list = []


class SafeBooleanField(_BooleanField):
    widget = _SafeCheckboxInput()


class SchoolConfigAdmin(ModelView, model=SchoolConfig):
    name = "⚙️ Налаштування школи"
    name_plural = "⚙️ Налаштування школи"
    icon = "fa-solid fa-school"
    column_list = [SchoolConfig.name, SchoolConfig.director_contact, SchoolConfig.admin_group_link]
    form_columns = [SchoolConfig.name, SchoolConfig.director_contact, SchoolConfig.admin_group_link]
    form_labels = {
        "name": "Назва школи",
        "director_contact": "Контакт директора (t.me/... або @username)",
        "admin_group_link": "Посилання на чат з адміністрацією",
    }
    can_create = True
    can_delete = False

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)


class WebUserAdmin(ModelView, model=WebUser):
    name = "👤 Співробітник"
    name_plural = "👤 Співробітники"
    icon = "fa-solid fa-user-shield"
    column_list = [WebUser.full_name, WebUser.username, WebUser.role, WebUser.managed_class]
    column_labels = {
        WebUser.full_name: "Ім'я",
        WebUser.username: "Логін",
        WebUser.role: "Роль",
        WebUser.managed_class: "Клас (класрук)",
    }
    column_sortable_list = [WebUser.full_name, WebUser.role]
    column_searchable_list = [WebUser.full_name, WebUser.username]
    form_columns = [
        WebUser.username, WebUser.password_hash, WebUser.full_name,
        WebUser.role, WebUser.managed_class,
        WebUser.telegram_contact, WebUser.phone, WebUser.extra_info,
    ]
    form_labels = {
        "username": "Логін",
        "password_hash": "Пароль (залиште порожнім, щоб не змінювати)",
        "full_name": "Повне ім'я",
        "role": "Роль",
        "managed_class": "Клас (тільки для класного керівника)",
        "telegram_contact": "Telegram (буде показано учням/батькам)",
        "phone": "Телефон (буде показано учням/батькам)",
        "extra_info": "Додаткова контактна інформація",
    }

    def is_accessible(self, request: Request) -> bool:
        return is_superadmin(request)

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        raw = data.get("password_hash")
        if raw:
            data["password_hash"] = hash_password(raw)
        elif not is_created:
            data.pop("password_hash", None)


class ClassAdmin(ModelView, model=Class):
    name = "🏫 Клас"
    name_plural = "🏫 Класи"
    icon = "fa-solid fa-chalkboard-user"
    column_list = [Class.name, Class.teacher_lead]
    column_labels = {Class.name: "Назва класу", Class.teacher_lead: "Класний керівник"}
    column_sortable_list = [Class.name]
    form_columns = [Class.name]

    def is_accessible(self, request: Request) -> bool:
        return is_admin_or_above(request)

    def details_query(self, request: Request):
        return super().details_query(request).options(
            selectinload(Class.sections).selectinload(Section.class_)
        )


class TelegramAccountAdmin(ModelView, model=TelegramAccount):
    name = "📱 Користувач бота"
    name_plural = "📱 Користувачі бота"
    icon = "fa-solid fa-people-roof"
    column_list = [TelegramAccount.full_name, TelegramAccount.telegram_id, TelegramAccount.is_blocked, TelegramAccount.registered_at]
    column_labels = {
        TelegramAccount.full_name: "Ім'я",
        TelegramAccount.telegram_id: "Telegram ID",
        TelegramAccount.is_blocked: "🚫 Заблоковано",
        TelegramAccount.registered_at: "Дата реєстрації",
    }
    column_searchable_list = [TelegramAccount.full_name, TelegramAccount.telegram_id]
    column_sortable_list = [TelegramAccount.full_name, TelegramAccount.registered_at, TelegramAccount.is_blocked]
    column_default_sort = [(TelegramAccount.registered_at, True)]
    form_columns = [TelegramAccount.is_blocked]
    form_overrides = {"is_blocked": SafeBooleanField}
    form_labels = {"is_blocked": "🚫 Заблокувати цього користувача"}
    can_create = False
    can_delete = False

    def is_accessible(self, request: Request) -> bool:
        return is_authenticated(request)

    async def check_can_edit(self, request: Request, model) -> bool:
        return is_admin_or_above(request)

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        if not is_admin_or_above(request):
            raise ValueError("⛔️ Тільки адміністратор може блокувати користувачів.")
        await super().on_model_change(data, model, is_created, request)

    def _scope(self, stmt, request: Request):
        if is_admin_or_above(request):
            return stmt
        class_id = session_class_id(request)
        if class_id is None:
            return stmt.where(False)
        return stmt.join(Section, Section.account_id == TelegramAccount.id).where(Section.class_id == class_id).distinct()

    def list_query(self, request: Request):
        return self._scope(super().list_query(request), request)

    def count_query(self, request: Request):
        return self._scope(super().count_query(request), request)

    def details_query(self, request: Request):
        return self._scope(
            super().details_query(request).options(selectinload(TelegramAccount.sections).selectinload(Section.class_)),
            request,
        )


class SectionAdmin(ModelView, model=Section):
    name = "📋 Секція (клас ↔ користувач)"
    name_plural = "📋 Секції (класи ↔ користувачі)"
    icon = "fa-solid fa-layer-group"
    column_list = [Section.account, Section.section_type, Section.class_]
    column_labels = {Section.account: "👤 Користувач", Section.section_type: "Роль", Section.class_: "Клас"}
    column_searchable_list = []
    column_sortable_list = [Section.section_type]
    column_default_sort = [(Section.class_id, False)]
    form_columns = [Section.account, Section.section_type, Section.class_]
    form_labels = {"account": "👤 Користувач (Telegram)", "section_type": "Роль у класі", "class_": "Клас"}

    def is_accessible(self, request: Request) -> bool:
        return is_authenticated(request)

    def can_create_row(self, request: Request) -> bool:
        return is_admin_or_above(request)

    async def check_can_edit(self, request: Request, model) -> bool:
        return is_admin_or_above(request)

    async def check_can_delete(self, request: Request, model) -> bool:
        return is_admin_or_above(request)

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        if not is_admin_or_above(request):
            raise ValueError("⛔️ Доступ заборонено. Тільки адміністратор може змінювати секції.")
        await super().on_model_change(data, model, is_created, request)

    def _scope(self, stmt, request: Request):
        if is_admin_or_above(request):
            return stmt
        class_id = session_class_id(request)
        if class_id is None:
            return stmt.where(False)
        return stmt.where(Section.class_id == class_id)

    def list_query(self, request: Request):
        return self._scope(super().list_query(request).options(selectinload(Section.class_), selectinload(Section.account)), request)

    def count_query(self, request: Request):
        return self._scope(super().count_query(request), request)

    def details_query(self, request: Request):
        return self._scope(super().details_query(request).options(selectinload(Section.class_), selectinload(Section.account)), request)


_TARGET_CHOICES_ADMIN = [
    (NewsTarget.all.value,            "🏫 Вся школа"),
    (NewsTarget.teachers.value,       "👨‍🏫 Всі вчителі"),
    (NewsTarget.students.value,       "🎓 Всі учні"),
    (NewsTarget.parents.value,        "👨‍👩‍👦 Всі батьки"),
    (NewsTarget.class_students.value, "🎓 Учні класу"),
    (NewsTarget.class_parents.value,  "👨‍👩‍👦 Батьки класу"),
    (NewsTarget.class_all.value,      "👥 Учні + батьки класу"),
]


class NewsAdmin(ModelView, model=News):
    name = "📰 Новина"
    name_plural = "📰 Новини"
    icon = "fa-solid fa-newspaper"
    column_list = [News.title, News.target, News.target_class, News.author, News.created_at]
    column_labels = {
        News.title: "Заголовок", News.target: "Аудиторія",
        News.target_class: "Клас", News.author: "Автор", News.created_at: "Дата",
    }
    column_sortable_list = [News.created_at, News.title]
    column_default_sort = [(News.created_at, True)]
    form_columns = [News.title, News.text, News.author]
    form_labels = {
        "title": "📌 Заголовок",
        "text": "📝 Текст новини",
        "author": "✍️ Автор",
        "target_field": "👥 Кому надіслати",
        "target_class_ids_field": "🏫 Клас(и) — тримайте Ctrl для вибору декількох",
        "upload_image": "🖼️ Зображення (jpg/png/webp/gif, до 5МБ)",
    }

    def is_accessible(self, request: Request) -> bool:
        return is_authenticated(request)

    def _owns(self, request: Request, model: "News | None") -> bool:
        if is_admin_or_above(request) or model is None:
            return True
        class_id = session_class_id(request)
        user_id = request.session.get("user_id")
        if not class_id:
            return False
        if model.target in ("all", "teachers", "students", "parents"):
            return False
        import json
        try:
            target_ids = json.loads(model.target_class_ids) if model.target_class_ids else []
        except:
            target_ids = []
        if not target_ids and model.target_class_id:
            target_ids = [model.target_class_id]
        return model.author_id == user_id or (len(target_ids) == 1 and target_ids[0] == class_id)

    async def check_can_edit(self, request: Request, model) -> bool:
        return self._owns(request, model)

    async def check_can_delete(self, request: Request, model) -> bool:
        return self._owns(request, model)

    async def check_can_view_details(self, request: Request, model) -> bool:
        return self._owns(request, model)

    def _scope(self, stmt, request: Request):
        if is_admin_or_above(request):
            return stmt
        class_id = session_class_id(request)
        user_id = request.session.get("user_id")
        if class_id is None:
            return stmt.where(False)
        return stmt.where(or_(News.target_class_id == class_id, News.author_id == user_id))

    def list_query(self, request: Request):
        return self._scope(super().list_query(request), request)

    def count_query(self, request: Request):
        return self._scope(super().count_query(request), request)

    def details_query(self, request: Request):
        return self._scope(super().details_query(request), request)

    async def scaffold_form(self, rules=None):
        form_class = await super().scaffold_form(rules)
        async with async_session_maker() as session:
            result = await session.execute(select(Class).order_by(Class.name))
            classes = result.scalars().all()

        form_class = type(form_class.__name__, (form_class,), {
            "target_field": SelectField(label="👥 Кому надіслати", choices=_TARGET_CHOICES_ADMIN),
            "target_class_ids_field": SelectMultipleField(label="🏫 Клас(и)", choices=[(c.id, c.name) for c in classes], coerce=int),
            "upload_image": FileField(label="🖼️ Зображення (jpg/png/webp/gif, до 5МБ)"),
        })
        return form_class

    async def on_model_change(self, data, model, is_created, request: Request) -> None:
        old_image = None if is_created else getattr(model, "image_url", None)
        await _save_uploaded_image(data, model=None if is_created else model)

        is_teacher = session_role(request) == WebUserRole.teacher_lead.value

        if is_teacher:
            class_id = session_class_id(request)
            data.pop("target_field", None)
            data.pop("target_class_ids_field", None)
            data["target"] = NewsTarget.class_all.value
            data["target_class_id"] = class_id
            data["target_class_ids"] = _json.dumps([class_id]) if class_id else None
            data["author_id"] = request.session["user_id"]
        else:
            target_val = data.pop("target_field", None) or NewsTarget.all.value
            data["target"] = target_val

            class_targets = {NewsTarget.class_students.value, NewsTarget.class_parents.value, NewsTarget.class_all.value}
            selected_ids = data.pop("target_class_ids_field", None) or []
            if isinstance(selected_ids, int):
                selected_ids = [selected_ids]
            selected_ids = [int(i) for i in selected_ids if i]

            if target_val in class_targets:
                if not selected_ids:
                    async with async_session_maker() as session:
                        res = await session.execute(select(Class.id))
                        selected_ids = [r[0] for r in res.all()]
                data["target_class_ids"] = _json.dumps(selected_ids)
                data["target_class_id"] = selected_ids[0] if selected_ids else None
            else:
                data.pop("target_class_ids_field", None)
                data["target_class_ids"] = None
                data["target_class_id"] = None

        author_val = data.get("author")
        if author_val is not None and hasattr(author_val, "id"):
            data["author_id"] = author_val.id
            data.pop("author", None)

        await super().on_model_change(data, model, is_created, request)

        new_image = data.get("image_url")
        if old_image and new_image and old_image != new_image:
            _delete_media_file(old_image)

    async def insert_model(self, request: Request, data: dict):
        model = await super().insert_model(request, data)
        _fire_and_track(broadcast_news(model.id, bot_instance))
        return model

    async def update_model(self, request: Request, pk: str, data: dict):
        try:
            model = await super().update_model(request, pk, data)
        except StaleDataError:
            logger.warning("Concurrent edit conflict for news_id=%s", pk)
            raise ValueError("Новину щойно оновив інший адміністратор. Оновіть сторінку та повторіть редагування.")
        _fire_and_track(update_news_in_telegram(model.id, bot_instance))
        return model

    async def delete_model(self, request: Request, pk: str):
        async with async_session_maker() as session:
            result = await session.execute(select(News).where(News.id == int(pk)))
            news = result.scalar_one_or_none()
            image_url = news.image_url if news else None
        result = await super().delete_model(request, pk)
        _delete_media_file(image_url)
        return result