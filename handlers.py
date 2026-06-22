from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot_utils import (
    DONE_CALLBACK,
    MENU_ADD_CLASS,
    MENU_MY_CLASSES,
    Registration,
    classes_multiselect_keyboard,
    main_menu_keyboard,
    more_keyboard,
    schools_keyboard,
)
from database import async_session_maker
from models import Class, Parent, School, parent_class_association

router = Router()


async def _get_parent(telegram_id: int) -> Parent | None:
    async with async_session_maker() as session:
        result = await session.execute(select(Parent).where(Parent.telegram_id == telegram_id))
        return result.scalar_one_or_none()


async def finish_registration(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Головне меню:", reply_markup=main_menu_keyboard())


async def send_classes_step(message: Message, state: FSMContext, school_id: int) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(Class).where(Class.school_id == school_id))
        classes = result.scalars().all()

    if not classes:
        await message.answer("У цій школі поки немає класів. Зверніться до адміністратора.")
        await state.clear()
        return

    await state.update_data(school_id=school_id, selected_classes=[])
    await message.answer(
        "Виберіть класи (можна декілька, наприклад перший і третій) "
        "і натисніть «Готово»:",
        reply_markup=classes_multiselect_keyboard(classes, set()),
    )
    await state.set_state(Registration.choosing_classes)


async def send_schools_step(message: Message, state: FSMContext) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(School))
        schools = result.scalars().all()

    if not schools:
        await message.answer("Школи поки не додані. Зверніться до адміністратора.")
        await state.clear()
        return

    await state.update_data(schools_total=len(schools))

    # Якщо в системі лише одна школа — не питаємо про неї,
    # а одразу переходимо до вибору класів (менше кроків = зручніше).
    if len(schools) == 1:
        await send_classes_step(message, state, schools[0].id)
        return

    await message.answer("Виберіть школу:", reply_markup=schools_keyboard(schools))
    await state.set_state(Registration.waiting_for_school)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    parent = await _get_parent(message.from_user.id)

    if parent is not None:
        await message.answer(
            f"З поверненням, {parent.full_name}! Виберіть пункт меню нижче 👇",
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

    await state.update_data(school_id=school_id, selected_classes=[])
    await callback.message.edit_text(
        "Виберіть класи (можна декілька, наприклад перший і третій) "
        "і натисніть «Готово»:",
        reply_markup=classes_multiselect_keyboard(classes, set()),
    )
    await state.set_state(Registration.choosing_classes)
    await callback.answer()


@router.callback_query(Registration.choosing_classes, F.data.startswith("toggle_class:"))
async def process_toggle_class(callback: CallbackQuery, state: FSMContext) -> None:
    class_id = int(callback.data.split(":")[1])

    data = await state.get_data()
    school_id = data.get("school_id")
    selected = set(data.get("selected_classes", []))

    if class_id in selected:
        selected.discard(class_id)
    else:
        selected.add(class_id)

    await state.update_data(selected_classes=list(selected))

    async with async_session_maker() as session:
        result = await session.execute(select(Class).where(Class.school_id == school_id))
        classes = result.scalars().all()

    await callback.message.edit_reply_markup(reply_markup=classes_multiselect_keyboard(classes, selected))
    await callback.answer()


@router.callback_query(Registration.choosing_classes, F.data == DONE_CALLBACK)
async def process_classes_done(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("selected_classes", [])

    if not selected:
        await callback.answer("Відмітьте хоча б один клас ✅", show_alert=True)
        return

    async with async_session_maker() as session:
        result = await session.execute(select(Parent).where(Parent.telegram_id == callback.from_user.id))
        parent = result.scalar_one_or_none()

        if parent:
            for class_id in selected:
                existing = await session.execute(
                    select(parent_class_association).where(
                        parent_class_association.c.parent_id == parent.id,
                        parent_class_association.c.class_id == class_id,
                    )
                )
                if existing.first() is None:
                    await session.execute(
                        parent_class_association.insert().values(parent_id=parent.id, class_id=class_id)
                    )
            await session.commit()

    await callback.answer()

    schools_total = data.get("schools_total", 1)
    if schools_total > 1:
        await callback.message.edit_text(
            f"Додано класів: {len(selected)} ✅\nДодати класи з іншої школи?",
            reply_markup=more_keyboard(),
        )
        await state.set_state(Registration.waiting_for_more_schools)
    else:
        await callback.message.edit_text(
            "Готово! 🎉 Тепер ви будете отримувати новини свого класу."
        )
        await finish_registration(callback.message, state)


@router.callback_query(Registration.waiting_for_more_schools, F.data == "more:yes")
async def process_more_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await send_schools_step(callback.message, state)


@router.callback_query(Registration.waiting_for_more_schools, F.data == "more:no")
async def process_more_no(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_text("Готово! 🎉 Тепер ви будете отримувати новини своїх класів.")
    await finish_registration(callback.message, state)


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

    text = "Ваші класи:\n" + "\n".join([f"• {s.name} — {c.name}" for c, s in rows])
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(F.text == MENU_MY_CLASSES)
async def menu_my_classes(message: Message) -> None:
    await show_my_classes(message)


@router.message(F.text == MENU_ADD_CLASS)
async def menu_add_class(message: Message, state: FSMContext) -> None:
    parent = await _get_parent(message.from_user.id)

    if parent is None:
        await message.answer("Спочатку пройдіть реєстрацію: напишіть /start")
        return

    await send_schools_step(message, state)
