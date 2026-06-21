from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot_utils import Registration, classes_keyboard, more_keyboard, schools_keyboard
from database import async_session_maker
from models import Class, Parent, School, parent_class_association

router = Router()


async def send_schools_step(message: Message, state: FSMContext) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(School))
        schools = result.scalars().all()

    if not schools:
        await message.answer("No schools are available.")
        await state.clear()
        return

    await message.answer("Select school:", reply_markup=schools_keyboard(schools))
    await state.set_state(Registration.waiting_for_school)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(Parent).where(Parent.telegram_id == message.from_user.id))
        parent = result.scalar_one_or_none()

    if parent is not None:
        await message.answer(f"Welcome back, {parent.full_name}!")
        return

    await message.answer("Enter your full name:")
    await state.set_state(Registration.waiting_for_name)


@router.message(Registration.waiting_for_name)
async def process_name(message: Message, state: FSMContext) -> None:
    async with async_session_maker() as session:
        session.add(Parent(telegram_id=message.from_user.id, full_name=message.text.strip()))
        await session.commit()

    await send_schools_step(message, state)


@router.callback_query(Registration.waiting_for_school, F.data.startswith("school:"))
async def process_school(callback: CallbackQuery, state: FSMContext) -> None:
    school_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        result = await session.execute(select(Class).where(Class.school_id == school_id))
        classes = result.scalars().all()

    if not classes:
        await callback.answer("No classes.", show_alert=True)
        return

    await callback.message.edit_text("Select class:", reply_markup=classes_keyboard(classes))
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

    await callback.message.edit_text("Added. Add another?", reply_markup=more_keyboard())
    await state.set_state(Registration.waiting_for_more)


@router.callback_query(Registration.waiting_for_more, F.data == "more:yes")
async def process_more_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await send_schools_step(callback.message, state)


@router.callback_query(Registration.waiting_for_more, F.data == "more:no")
async def process_more_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Done! Use /my_classes")


@router.message(Command("my_classes"))
async def cmd_my_classes(message: Message) -> None:
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
        await message.answer("No classes.")
        return

    await message.answer("\n".join([f"{s.name} - {c.name}" for c, s in rows]))