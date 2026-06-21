from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_school = State()
    waiting_for_class = State()
    waiting_for_more = State()


def schools_keyboard(schools) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for school in schools:
        builder.button(text=school.name, callback_data=f"school:{school.id}")
    builder.adjust(1)
    return builder.as_markup()


def classes_keyboard(classes) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for class_ in classes:
        builder.button(text=class_.name, callback_data=f"class:{class_.id}")
    builder.adjust(1)
    return builder.as_markup()


def more_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Yes", callback_data="more:yes")
    builder.button(text="No", callback_data="more:no")
    builder.adjust(2)
    return builder.as_markup()