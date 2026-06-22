from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

MENU_MY_CLASSES = "Мої класи"
MENU_ADD_CLASS = "Додати клас"

DONE_CALLBACK = "classes_done"


class Registration(StatesGroup):
    waiting_for_name = State()
    waiting_for_school = State()
    choosing_classes = State()
    waiting_for_more_schools = State()


def schools_keyboard(schools) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for school in schools:
        builder.button(text=school.name, callback_data=f"school:{school.id}")
    builder.adjust(1)
    return builder.as_markup()


def classes_multiselect_keyboard(classes, selected_ids) -> InlineKeyboardMarkup:
    """Клавіатура з галочками: можна відмітити одразу декілька класів
    (наприклад перший і третій), а потім натиснути «Готово»."""
    builder = InlineKeyboardBuilder()
    for class_ in classes:
        mark = "✅ " if class_.id in selected_ids else "⬜️ "
        builder.button(text=f"{mark}{class_.name}", callback_data=f"toggle_class:{class_.id}")
    builder.button(text="✅ Готово", callback_data=DONE_CALLBACK)
    builder.adjust(1)
    return builder.as_markup()


def more_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Так", callback_data="more:yes")
    builder.button(text="Ні, все", callback_data="more:no")
    builder.adjust(2)
    return builder.as_markup()


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=MENU_MY_CLASSES)
    builder.button(text=MENU_ADD_CLASS)
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
