from pathlib import Path
import logging

from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Class, News, NewsDelivery, Parent, parent_class_association

MEDIA_ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


def resolve_photo(image_value: str):
    if image_value.startswith("http://") or image_value.startswith("https://"):
        return image_value
    return FSInputFile(MEDIA_ROOT / image_value)


async def broadcast_news(news_id: int, bot: Bot) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(News).options(selectinload(News.classes)).where(News.id == news_id))
        news = result.scalar_one_or_none()

        if news is None:
            return

        # Захист від дублювання: якщо вже розіслали — виходимо
        existing = await session.execute(
            select(NewsDelivery).where(NewsDelivery.news_id == news_id).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            logger.info("broadcast_news: news_id=%s вже розіслана, пропускаємо", news_id)
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
        has_photo = bool(news.image_url)

        for parent in parents:
            try:
                if has_photo:
                    msg = await bot.send_photo(
                        chat_id=parent.telegram_id,
                        photo=resolve_photo(news.image_url),
                        caption=text,
                    )
                else:
                    msg = await bot.send_message(chat_id=parent.telegram_id, text=text)

                session.add(
                    NewsDelivery(
                        news_id=news.id,
                        parent_id=parent.id,
                        telegram_message_id=msg.message_id,
                        has_photo=has_photo,
                    )
                )
            except Exception as e:
                logger.warning("broadcast_news: не вдалося надіслати parent_id=%s: %s", parent.id, e)
                continue

        await session.commit()
        logger.info("broadcast_news: news_id=%s розіслана %s батькам", news_id, len(parents))


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
                    media = InputMediaPhoto(media=resolve_photo(news.image_url), caption=text)
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
        except Exception as e:
            logger.warning("update_news_in_telegram: telegram_id=%s: %s", telegram_id, e)
            continue