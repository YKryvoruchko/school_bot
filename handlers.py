import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bot_utils import (
    CB_CLASS, CB_DONE, CB_ROLE, CB_TOGGLE,
    MENU_ADD_SECTION, MENU_MY_CLASSES, MENU_MY_PROFILE,
    Registration,
    classes_keyboard, classes_multiselect_keyboard,
    get_user_lock, main_menu_keyboard, role_keyboard,
)
from database import async_session_maker
from models import Class, SchoolConfig, Section, SectionType, TelegramAccount, WebUser, WebUserRole

logger = logging.getLogger(__name__)
router = Router()


async def _get_account(telegram_id: int) -> TelegramAccount | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(TelegramAccount).where(TelegramAccount.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()


async def _get_school_config() -> SchoolConfig | None:
    async with async_session_maker() as session:
        result = await session.execute(select(SchoolConfig))
        return result.scalar_one_or_none()


async def _get_classes() -> list[Class]:
    async with async_session_maker() as session:
        result = await session.execute(select(Class).order_by(Class.name))
        return list(result.scalars().all())


async def _get_teacher_lead(class_id: int) -> WebUser | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(WebUser).where(
                WebUser.role == WebUserRole.teacher_lead,
                WebUser.class_id == class_id,
            )
        )
        return result.scalar_one_or_none()


def _format_teacher_contact(teacher: WebUser | None, class_name: str) -> str:
    if teacher is None:
        return f"ℹ️ Класний керівник для класу <b>{class_name}</b> ще не призначений."
    lines = [f"👩‍🏫 <b>Класний керівник {class_name}</b>: {teacher.full_name}"]
    if teacher.telegram_contact:
        lines.append(f"   Telegram: {teacher.telegram_contact}")
    if teacher.phone:
        lines.append(f"   Телефон: {teacher.phone}")
    if teacher.extra_info:
        lines.append(f"   {teacher.extra_info}")
    return "\n".join(lines)


async def _send_welcome_contacts(message: Message, class_ids: list[int]) -> None:
    config = await _get_school_config()

    async with async_session_maker() as session:
        result = await session.execute(select(Class).where(Class.id.in_(class_ids)))
        classes = result.scalars().all()

    contact_lines = []
    for cls in classes:
        teacher = await _get_teacher_lead(cls.id)
        contact_lines.append(_format_teacher_contact(teacher, cls.name))

    if contact_lines:
        await message.answer(
            "📇 <b>Контакти вашого класного керівника:</b>\n\n" + "\n\n".join(contact_lines)
        )

    if config:
        admin_text = "🏫 <b>Контакти адміністрації школи:</b>\n"
        if config.director_contact:
            admin_text += f"\n👩‍💼 Директор: {config.director_contact}"
        if config.admin_group_link:
            admin_text += f"\n💬 Чат з адміністрацією: {config.admin_group_link}"
        if config.director_contact or config.admin_group_link:
            await message.answer(admin_text)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    account = await _get_account(message.from_user.id)
    if account is not None:
        await message.answer(f"З поверненням, <b>{account.full_name}</b>! 👋", reply_markup=main_menu_keyboard())
        return

    await state.clear()
    await message.answer("👋 Вітаємо в боті школи!\n\nОберіть, хто ви:", reply_markup=role_keyboard())
    await state.set_state(Registration.choosing_role)


@router.callback_query(Registration.choosing_role, F.data.startswith(f"{CB_ROLE}:"))
async def process_role(callback: CallbackQuery, state: FSMContext) -> None:
    role = callback.data.split(":")[1]
    if role == "teacher":
        await callback.answer("⛔️ Реєстрацію вчителів проводить адміністратор школи.", show_alert=True)
        await state.clear()
        return

    await state.update_data(role=role)
    await callback.message.edit_text("✏️ Напишіть ваше <b>ім'я та прізвище</b>:")
    await state.set_state(Registration.waiting_for_name)
    await callback.answer()


@router.message(Registration.waiting_for_name, F.text)
async def process_name(message: Message, state: FSMContext) -> None:
    full_name = message.text.strip()
    if not full_name or len(full_name) < 2:
        await message.answer("Будь ласка, введіть справжнє ім'я та прізвище.")
        return

    await state.update_data(full_name=full_name)
    classes = await _get_classes()
    if not classes:
        await message.answer("⚠️ Класи ще не додані адміністратором. Спробуйте пізніше або зверніться до адміністрації.")
        await state.clear()
        return

    data = await state.get_data()
    role = data.get("role")

    if role == "teacher":
        await message.answer("⛔️ Реєстрацію вчителів проводить адміністратор школи.")
        await state.clear()
    elif role == "parent":
        await state.update_data(selected_classes=[])
        await message.answer(
            "Виберіть <b>класи ваших дітей</b> (можна декілька) і натисніть «Готово»:",
            reply_markup=classes_multiselect_keyboard(classes, set()),
        )
        await state.set_state(Registration.choosing_classes)
    else:
        await message.answer("Виберіть <b>ваш клас</b>:", reply_markup=classes_keyboard(classes))
        await state.set_state(Registration.choosing_class)


@router.message(Registration.waiting_for_name)
async def process_name_wrong_type(message: Message) -> None:
    await message.answer("Будь ласка, надішліть лише текст — ваше ім'я та прізвище.")


@router.callback_query(Registration.choosing_class, F.data.startswith(f"{CB_CLASS}:"))
async def process_class_single(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    lock = get_user_lock(callback.from_user.id)
    if lock.locked():
        return

    async with lock:
        data = await state.get_data()
        if not data:
            await callback.message.answer("Сесія застаріла. Почніть заново: /start")
            await state.clear()
            return

        class_id = int(callback.data.split(":")[1])
        await _finalize_registration(
            message=callback.message,
            state=state,
            telegram_id=callback.from_user.id,
            full_name=data["full_name"],
            role=data["role"],
            class_ids=[class_id],
        )


@router.callback_query(Registration.choosing_classes, F.data.startswith(f"{CB_TOGGLE}:"))
async def process_toggle_class(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data:
        await callback.answer("Сесія застаріла. Почніть заново: /start", show_alert=True)
        await state.clear()
        return

    class_id = int(callback.data.split(":")[1])
    selected = set(data.get("selected_classes", []))
    selected.discard(class_id) if class_id in selected else selected.add(class_id)

    await state.update_data(selected_classes=list(selected))
    classes = await _get_classes()
    await callback.message.edit_reply_markup(reply_markup=classes_multiselect_keyboard(classes, selected))
    await callback.answer()


@router.callback_query(Registration.choosing_classes, F.data == CB_DONE)
async def process_classes_done(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    lock = get_user_lock(callback.from_user.id)
    if lock.locked():
        return

    async with lock:
        data = await state.get_data()
        if not data:
            return

        selected = data.get("selected_classes", [])
        if not selected:
            await callback.message.answer("⚠️ Оберіть хоча б один клас.")
            return

        await _finalize_registration(
            message=callback.message,
            state=state,
            telegram_id=callback.from_user.id,
            full_name=data["full_name"],
            role=data["role"],
            class_ids=selected,
        )


async def _finalize_registration(*, message, state, telegram_id, full_name, role, class_ids):
    section_type = SectionType(role)

    async with async_session_maker() as session:
        stmt = sqlite_insert(TelegramAccount).values(
            telegram_id=telegram_id, full_name=full_name, is_blocked=False,
        ).on_conflict_do_update(index_elements=["telegram_id"], set_={"full_name": full_name})
        await session.execute(stmt)
        await session.flush()

        result = await session.execute(select(TelegramAccount).where(TelegramAccount.telegram_id == telegram_id))
        account = result.scalar_one()

        for class_id in class_ids:
            existing = await session.execute(
                select(Section).where(
                    Section.account_id == account.id,
                    Section.section_type == section_type,
                    Section.class_id == class_id,
                )
            )
            if existing.scalar_one_or_none() is None:
                session.add(Section(account_id=account.id, section_type=section_type, class_id=class_id))

        await session.commit()

    await state.clear()

    role_label = {"teacher": "вчитель", "student": "учень", "parent": "батько/мати"}.get(role, role)
    await message.answer(
        f"🎉 <b>Реєстрацію завершено!</b>\nІм'я: {full_name}\nРоль: {role_label}\n\n"
        "Тепер ви будете отримувати новини свого класу."
    )
    await _send_welcome_contacts(message, class_ids)
    await message.answer("Головне меню:", reply_markup=main_menu_keyboard())


@router.message(F.text == MENU_MY_CLASSES)
async def menu_my_classes(message: Message) -> None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Section, Class)
            .join(Class, Class.id == Section.class_id)
            .join(TelegramAccount, TelegramAccount.id == Section.account_id)
            .where(TelegramAccount.telegram_id == message.from_user.id)
        )
        rows = result.all()

    if not rows:
        await message.answer("У вас ще немає доданих класів.", reply_markup=main_menu_keyboard())
        return

    lines = []
    for section, cls in rows:
        emoji = {"teacher": "👨‍🏫", "student": "🎓", "parent": "👨‍👩‍👦"}.get(section.section_type.value, "•")
        lines.append(f"{emoji} {cls.name} ({section.section_type.value})")

    await message.answer("📋 <b>Ваші класи:</b>\n" + "\n".join(lines), reply_markup=main_menu_keyboard())


@router.message(F.text == MENU_MY_PROFILE)
async def menu_my_profile(message: Message) -> None:
    account = await _get_account(message.from_user.id)
    if not account:
        await message.answer("Спочатку пройдіть реєстрацію: /start")
        return

    await message.answer(
        f"👤 <b>Ваш профіль:</b>\n"
        f"Ім'я: {account.full_name}\n"
        f"Telegram ID: <code>{account.telegram_id}</code>\n"
        f"Зареєстровано: {account.registered_at.strftime('%d.%m.%Y')}",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == MENU_ADD_SECTION)
async def menu_add_section(message: Message, state: FSMContext) -> None:
    account = await _get_account(message.from_user.id)
    if not account:
        await message.answer("Спочатку пройдіть реєстрацію: /start")
        return

    await state.clear()
    await message.answer("Оберіть роль для нової секції:", reply_markup=role_keyboard())
    await state.set_state(Registration.choosing_role)