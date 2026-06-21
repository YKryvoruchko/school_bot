from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Class, News, NewsDelivery, Parent, parent_class_association


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

        for parent in parents:
            try:
                if news.image_url:
                    msg = await bot.send_photo(chat_id=parent.telegram_id, photo=news.image_url, caption=text)
                else:
                    msg = await bot.send_message(chat_id=parent.telegram_id, text=text)

                session.add(
                    NewsDelivery(news_id=news.id, parent_id=parent.id, telegram_message_id=msg.message_id)
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
            if news.image_url:
                await bot.edit_message_caption(chat_id=telegram_id, message_id=delivery.telegram_message_id, caption=text)
            else:
                await bot.edit_message_text(chat_id=telegram_id, message_id=delivery.telegram_message_id, text=text)
        except Exception:
            continue