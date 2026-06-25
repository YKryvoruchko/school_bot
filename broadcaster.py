from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Class, News, NewsDelivery, Parent, parent_class_association

MEDIA_ROOT = Path(__file__).resolve().parent
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ANIMATION_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def is_photo_file(file_value: str) -> bool:
    path = file_value.split("?", 1)[0]
    return Path(path).suffix.lower() in PHOTO_EXTENSIONS


def file_extension(file_value: str) -> str:
    path = file_value.split("?", 1)[0]
    return Path(path).suffix.lower()


def resolve_file(file_value: str):
    if file_value.startswith("http://") or file_value.startswith("https://"):
        return file_value
    return FSInputFile(MEDIA_ROOT / file_value)


async def send_news_message(bot: Bot, telegram_id: int, text: str, file_value: str | None):
    if not file_value:
        return await bot.send_message(chat_id=telegram_id, text=text)

    if is_photo_file(file_value):
        return await bot.send_photo(
            chat_id=telegram_id,
            photo=resolve_file(file_value),
            caption=text,
        )

    ext = file_extension(file_value)
    if ext in ANIMATION_EXTENSIONS:
        return await bot.send_animation(
            chat_id=telegram_id,
            animation=resolve_file(file_value),
            caption=text,
        )

    if ext in VIDEO_EXTENSIONS:
        return await bot.send_video(
            chat_id=telegram_id,
            video=resolve_file(file_value),
            caption=text,
        )

    return await bot.send_document(
        chat_id=telegram_id,
        document=resolve_file(file_value),
        caption=text,
    )


def build_input_media(file_value: str, caption: str):
    if is_photo_file(file_value):
        return InputMediaPhoto(media=resolve_file(file_value), caption=caption)
    ext = file_extension(file_value)
    if ext in ANIMATION_EXTENSIONS:
        return InputMediaAnimation(media=resolve_file(file_value), caption=caption)
    if ext in VIDEO_EXTENSIONS:
        return InputMediaVideo(media=resolve_file(file_value), caption=caption)
    return InputMediaDocument(media=resolve_file(file_value), caption=caption)


async def broadcast_news(news_id: int, bot: Bot) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(News).options(selectinload(News.classes)).where(News.id == news_id))
        news = result.scalar_one_or_none()

        if news is None:
            return

        stmt = (
            select(Parent)
            .distinct()
            .join(parent_class_association, parent_class_association.c.parent_id == Parent.id)
            .join(Class, Class.id == parent_class_association.c.class_id)
            .where(Class.school_id == news.school_id, Parent.is_blocked == False)
        )

        if news.classes:
            target_class_ids = [c.id for c in news.classes]
            stmt = stmt.where(Class.id.in_(target_class_ids))

        result = await session.execute(stmt)
        parents = result.scalars().all()

        text = f"<b>{news.title}</b>\n\n{news.text}"
        has_file = bool(news.image_url)

        for parent in parents:
            try:
                msg = await send_news_message(bot, parent.telegram_id, text, news.image_url)

                session.add(
                    NewsDelivery(
                        news_id=news.id,
                        parent_id=parent.id,
                        telegram_message_id=msg.message_id,
                        has_photo=has_file,
                    )
                )
            except Exception:
                continue

        await session.commit()


async def update_news_in_telegram(news_id: int, bot: Bot) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(News).where(News.id == news_id))
        news = result.scalar_one_or_none()

        if news is None:
            return

        result = await session.execute(
            select(NewsDelivery, Parent.telegram_id)
            .join(Parent, Parent.id == NewsDelivery.parent_id)
            .where(NewsDelivery.news_id == news.id)
        )
        deliveries = result.all()

    text = f"<b>{news.title}</b>\n\n{news.text}"

    for delivery, telegram_id in deliveries:
        try:
            if delivery.has_photo:
                if news.image_url:
                    media = build_input_media(news.image_url, text)
                    await bot.edit_message_media(
                        chat_id=telegram_id,
                        message_id=delivery.telegram_message_id,
                        media=media,
                    )
                else:
                    await bot.edit_message_caption(
                        chat_id=telegram_id,
                        message_id=delivery.telegram_message_id,
                        caption=text,
                    )
            else:
                await bot.edit_message_text(
                    chat_id=telegram_id,
                    message_id=delivery.telegram_message_id,
                    text=text,
                )
        except Exception:
            continue
