from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove


def get_main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="▶ Начать анкету"), KeyboardButton(text="ℹ️ Подробнее об услуге")],
        ],
        resize_keyboard=True,
    )


def get_cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/cancel")]],
        resize_keyboard=True,
    )


def get_remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove(remove_keyboard=True)
