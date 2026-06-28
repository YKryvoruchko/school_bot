import asyncio

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


class Registration(StatesGroup):
    choosing_role = State()
    waiting_for_name = State()
    choosing_class = State()
    choosing_classes = State()


CB_ROLE = "role"
CB_CLASS = "class"
CB_TOGGLE = "toggle"
CB_DONE = "classes_done"

MENU_MY_CLASSES = "📋 Мої класи"
MENU_MY_PROFILE = "👤 Мій профіль"
MENU_ADD_SECTION = "➕ Додати секцію"

_user_locks: dict[int, asyncio.Lock] = {}
_MAX_LOCKS = 10_000


def get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        if len(_user_locks) >= _MAX_LOCKS:
            to_remove = [uid for uid, lk in _user_locks.items() if not lk.locked()]
            for uid in to_remove:
                del _user_locks[uid]
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def role_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎓 Учень", callback_data=f"{CB_ROLE}:student")
    builder.button(text="👨‍👩‍👦 Батько/Мати", callback_data=f"{CB_ROLE}:parent")
    builder.adjust(1)
    return builder.as_markup()


def classes_keyboard(classes: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cls in classes:
        builder.button(text=cls.name, callback_data=f"{CB_CLASS}:{cls.id}")
    builder.adjust(2)
    return builder.as_markup()


def classes_multiselect_keyboard(classes: list, selected: set) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cls in classes:
        mark = "✅ " if cls.id in selected else "⬜️ "
        builder.button(text=f"{mark}{cls.name}", callback_data=f"{CB_TOGGLE}:{cls.id}")
    builder.button(text="✅ Готово", callback_data=CB_DONE)
    builder.adjust(2)
    return builder.as_markup()


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text=MENU_MY_CLASSES)
    builder.button(text=MENU_MY_PROFILE)
    builder.button(text=MENU_ADD_SECTION)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)