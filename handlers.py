from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot_utils import (
    MENU_ADD_CLASS,
    MENU_MY_CLASSES,
    Registration,
    classes_keyboard,
    main_menu_keyboard,
    more_keyboard,
    schools_keyboard,
)
from database import async_session_maker
from models import Class, Parent, School, parent_class_association

router = Router()


async def send_classes_step(message: Message, state: FSMContext, school_id: int) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(Class).where(Class.school_id == school_id))
        classes = result.scalars().all()

    if not classes:
        await message.answer("У цій школі поки немає класів. Зверніться до адміністратора.")
        await state.clear()
        return

    await state.update_data(school_id=school_id)
    await message.answer("Виберіть клас:", reply_markup=classes_keyboard(classes))
    await state.set_state(Registration.waiting_for_class)


async def send_schools_step(message: Message, state: FSMContext) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(School))
        schools = result.scalars().all()

    if not schools:
        await message.answer("Школи поки не додані. Зверніться до адміністратора.")
        await state.clear()
        return

    if len(schools) == 1:
        await send_classes_step(message, state, schools[0].id)
        return

    await message.answer("Виберіть школу:", reply_markup=schools_keyboard(schools))
    await state.set_state(Registration.waiting_for_school)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(Parent).where(Parent.telegram_id == message.from_user.id))
        parent = result.scalar_one_or_none()

    if parent is not None:
        await message.answer(
            f"З поверненням, {parent.full_name}! Виберіть пункт меню нижче:",
            reply_markup=main_menu_keyboard(),
        )
        return

    await message.answer("Вітаємо! Напишіть, будь ласка, ваше ім'я та прізвище:")
    await state.set_state(Registration.waiting_for_name)


@router.message(Registration.waiting_for_name)
async def process_name(message: Message, state: FSMContext) -> None:
    full_name = (message.text or "").strip()
    if not full_name:
        await message.answer("Будь ласка, напишіть ім'я текстом.")
        return

    async with async_session_maker() as session:
        session.add(Parent(telegram_id=message.from_user.id, full_name=full_name))
        await session.commit()

    await send_schools_step(message, state)


@router.callback_query(Registration.waiting_for_school, F.data.startswith("school:"))
async def process_school(callback: CallbackQuery, state: FSMContext) -> None:
    school_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        result = await session.execute(select(Class).where(Class.school_id == school_id))
        classes = result.scalars().all()

    if not classes:
        await callback.answer("У цій школі поки немає класів.", show_alert=True)
        return

    await state.update_data(school_id=school_id)
    await callback.message.edit_text("Виберіть клас:", reply_markup=classes_keyboard(classes))
    await state.set_state(Registration.waiting_for_class)


@router.callback_query(Registration.waiting_for_class, F.data.startswith("class:"))
async def process_class(callback: CallbackQuery, state: FSMContext) -> None:
    class_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        result = await session.execute(select(Parent).where(Parent.telegram_id == callback.from_user.id))
        parent = result.scalar_one_or_none()

        if parent:
            existing = await session.execute(
                select(parent_class_association).where(
                    parent_class_association.c.parent_id == parent.id,
                    parent_class_association.c.class_id == class_id,
                )
            )
            if existing.first() is None:
                await session.execute(parent_class_association.insert().values(parent_id=parent.id, class_id=class_id))
                await session.commit()

    await callback.message.edit_text("Клас додано.\nДодати ще один клас?", reply_markup=more_keyboard())
    await state.set_state(Registration.waiting_for_more)


@router.callback_query(Registration.waiting_for_more, F.data == "more:yes")
async def process_more_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await send_schools_step(callback.message, state)


@router.callback_query(Registration.waiting_for_more, F.data == "more:no")
async def process_more_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Готово! Тепер ви будете отримувати новини свого класу.")
    await callback.message.answer("Головне меню:", reply_markup=main_menu_keyboard())


async def show_my_classes(message: Message) -> None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(Class, School)
            .join(School, Class.school_id == School.id)
            .join(parent_class_association, parent_class_association.c.class_id == Class.id)
            .join(Parent, parent_class_association.c.parent_id == Parent.id)
            .where(Parent.telegram_id == message.from_user.id)
        )
        rows = result.all()

    if not rows:
        await message.answer(
            "У вас поки немає доданих класів.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = "Ваші класи:\n" + "\n".join([f"- {s.name} - {c.name}" for c, s in rows])
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(F.text == MENU_MY_CLASSES)
async def menu_my_classes(message: Message) -> None:
    await show_my_classes(message)


@router.message(F.text == MENU_ADD_CLASS)
async def menu_add_class(message: Message, state: FSMContext) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(Parent).where(Parent.telegram_id == message.from_user.id))
        parent = result.scalar_one_or_none()

    if parent is None:
        await message.answer("Спочатку пройдіть реєстрацію: напишіть /start")
        return

    await send_schools_step(message, state)
