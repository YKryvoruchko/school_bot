from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import Class, News, Parent, parent_class_association


async def broadcast_news(news_id: int, bot: Bot) -> None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(News).options(selectinload(News.classes)).where(News.id == news_id)
        )
        news = result.scalar_one_or_none()

        if news is None:
            return

        stmt = (
            select(Parent.telegram_id)
            .distinct()
            .join(parent_class_association, parent_class_association.c.parent_id == Parent.id)
            .join(Class, Class.id == parent_class_association.c.class_id)
            .where(Class.school_id == news.school_id)
        )

        if news.classes:
            target_class_ids = [c.id for c in news.classes]
            stmt = stmt.where(Class.id.in_(target_class_ids))

        result = await session.execute(stmt)
        telegram_ids = result.scalars().all()

    text = f"<b>{news.title}</b>\n\n{news.text}"

    for telegram_id in telegram_ids:
        try:
            await bot.send_message(chat_id=telegram_id, text=text)
        except Exception:
            continue