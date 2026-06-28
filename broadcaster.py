import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import FSInputFile, InputMediaAnimation, InputMediaDocument, InputMediaPhoto, InputMediaVideo
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from database import async_session_maker
from models import News, NewsDelivery, NewsTarget, Section, SectionType, TelegramAccount

logger = logging.getLogger(__name__)

MEDIA_ROOT = Path(__file__).resolve().parent
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ANIMATION_EXTENSIONS = {".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
SEND_RATE = 1 / 25


def _file_ext(file_value: str) -> str:
    return Path(file_value.split("?", 1)[0]).suffix.lower()


def _resolve_file(file_value: str):
    if file_value.startswith(("http://", "https://")):
        return file_value
    return FSInputFile(MEDIA_ROOT / file_value)


async def _send_one(bot: Bot, telegram_id: int, text: str, file_value: str | None):
    if not file_value:
        return await bot.send_message(chat_id=telegram_id, text=text)
    ext = _file_ext(file_value)
    f = _resolve_file(file_value)
    if ext in PHOTO_EXTENSIONS:
        return await bot.send_photo(chat_id=telegram_id, photo=f, caption=text)
    if ext in ANIMATION_EXTENSIONS:
        return await bot.send_animation(chat_id=telegram_id, animation=f, caption=text)
    if ext in VIDEO_EXTENSIONS:
        return await bot.send_video(chat_id=telegram_id, video=f, caption=text)
    return await bot.send_document(chat_id=telegram_id, document=f, caption=text)


def _build_media(file_value: str, caption: str):
    ext = _file_ext(file_value)
    f = _resolve_file(file_value)
    if ext in PHOTO_EXTENSIONS:
        return InputMediaPhoto(media=f, caption=caption)
    if ext in ANIMATION_EXTENSIONS:
        return InputMediaAnimation(media=f, caption=caption)
    if ext in VIDEO_EXTENSIONS:
        return InputMediaVideo(media=f, caption=caption)
    return InputMediaDocument(media=f, caption=caption)


async def _with_retry(coro_fn, max_attempts: int = 3):
    for attempt in range(max_attempts):
        try:
            return await coro_fn()
        except TelegramRetryAfter as e:
            if attempt == max_attempts - 1:
                raise
            logger.warning("Rate limit, sleeping %.1fs (attempt %d)", e.retry_after, attempt + 1)
            await asyncio.sleep(e.retry_after)


async def _mark_blocked(account_id: int) -> None:
    async with async_session_maker() as session:
        await session.execute(
            update(TelegramAccount).where(TelegramAccount.id == account_id).values(is_blocked=True)
        )
        await session.commit()


def _get_class_ids(news: "News") -> list[int] | None:
    import json
    raw = getattr(news, "target_class_ids", None)
    if raw:
        try:
            ids = json.loads(raw)
            if isinstance(ids, list) and ids:
                return [int(i) for i in ids]
        except (ValueError, TypeError):
            pass
    if news.target_class_id:
        return [news.target_class_id]
    return None


async def _resolve_recipients(news: "News") -> list[tuple[int, int]]:
    async with async_session_maker() as session:
        target = news.target
        class_ids = _get_class_ids(news)

        if target == NewsTarget.all:
            stmt = (
                select(TelegramAccount.id, TelegramAccount.telegram_id)
                .where(TelegramAccount.is_blocked == False)  # noqa: E712
                .distinct()
            )
        else:
            base = (
                select(TelegramAccount.id, TelegramAccount.telegram_id)
                .join(Section, Section.account_id == TelegramAccount.id)
                .where(TelegramAccount.is_blocked == False)  # noqa: E712
                .distinct()
            )
            if target == NewsTarget.teachers:
                stmt = base.where(Section.section_type == SectionType.teacher)
            elif target == NewsTarget.students:
                stmt = base.where(Section.section_type == SectionType.student)
            elif target == NewsTarget.parents:
                stmt = base.where(Section.section_type == SectionType.parent)
            elif target == NewsTarget.class_students:
                if not class_ids:
                    return []
                stmt = base.where(Section.section_type == SectionType.student, Section.class_id.in_(class_ids))
            elif target == NewsTarget.class_parents:
                if not class_ids:
                    return []
                stmt = base.where(Section.section_type == SectionType.parent, Section.class_id.in_(class_ids))
            elif target == NewsTarget.class_all:
                if not class_ids:
                    return []
                stmt = base.where(
                    Section.section_type.in_([SectionType.student, SectionType.parent]),
                    Section.class_id.in_(class_ids),
                )
            else:
                return []

        result = await session.execute(stmt)
        return result.all()


async def broadcast_news(news_id: int, bot: Bot) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(News).where(News.id == news_id))
        news = result.scalar_one_or_none()
        if news is None:
            logger.warning("broadcast_news: news_id=%d not found", news_id)
            return
        news_id_val, title, text, image_url = news.id, news.title, news.text, news.image_url
        recipients = await _resolve_recipients(news)

    if not recipients:
        logger.info("broadcast_news: no recipients for news_id=%d", news_id)
        return

    full_text = f"<b>{title}</b>\n\n{text}"
    logger.info("broadcast_news: news_id=%d → %d recipients", news_id_val, len(recipients))

    deliveries: list[NewsDelivery] = []
    for account_id, telegram_id in recipients:
        await asyncio.sleep(SEND_RATE)
        try:
            msg = await _with_retry(lambda tid=telegram_id, iu=image_url: _send_one(bot, tid, full_text, iu))
            deliveries.append(NewsDelivery(
                news_id=news_id_val, account_id=account_id,
                telegram_message_id=msg.message_id, has_media=bool(image_url),
            ))
        except TelegramForbiddenError:
            logger.info("Account %d blocked the bot", account_id)
            await _mark_blocked(account_id)
        except Exception as exc:
            logger.error("Failed to send to account_id=%d: %s", account_id, exc)

    if deliveries:
        async with async_session_maker() as session:
            session.add_all(deliveries)
            await session.commit()

    logger.info("broadcast_news: done news_id=%d sent=%d/%d", news_id_val, len(deliveries), len(recipients))


async def update_news_in_telegram(news_id: int, bot: Bot) -> None:
    async with async_session_maker() as session:
        result = await session.execute(select(News).where(News.id == news_id))
        news = result.scalar_one_or_none()
        if news is None:
            return

        current_recipients = await _resolve_recipients(news)

        result2 = await session.execute(
            select(NewsDelivery, TelegramAccount.telegram_id)
            .join(TelegramAccount, TelegramAccount.id == NewsDelivery.account_id)
            .where(NewsDelivery.news_id == news_id)
        )
        deliveries_rows = result2.all()
        existing_account_ids = {d.account_id for d, _ in deliveries_rows}
        snap = (news.title, news.text, news.image_url)

    title, text, image_url = snap
    full_text = f"<b>{title}</b>\n\n{text}"
    updated_delivery_ids: list[int] = []
    new_deliveries: list[NewsDelivery] = []

    for delivery, telegram_id in deliveries_rows:
        await asyncio.sleep(SEND_RATE)
        try:
            if delivery.has_media and image_url:
                media = _build_media(image_url, full_text)
                await _with_retry(lambda tid=telegram_id, mid=delivery.telegram_message_id, m=media:
                    bot.edit_message_media(chat_id=tid, message_id=mid, media=m))
            elif delivery.has_media and not image_url:
                await _with_retry(lambda tid=telegram_id, mid=delivery.telegram_message_id:
                    bot.delete_message(chat_id=tid, message_id=mid))
                msg = await _with_retry(lambda tid=telegram_id: bot.send_message(chat_id=tid, text=full_text))
                delivery.telegram_message_id = msg.message_id
                delivery.has_media = False
                updated_delivery_ids.append(delivery.id)
            elif not delivery.has_media and image_url:
                await _with_retry(lambda tid=telegram_id, mid=delivery.telegram_message_id:
                    bot.delete_message(chat_id=tid, message_id=mid))
                msg = await _with_retry(lambda tid=telegram_id, iu=image_url: _send_one(bot, tid, full_text, iu))
                delivery.telegram_message_id = msg.message_id
                delivery.has_media = True
                updated_delivery_ids.append(delivery.id)
            else:
                await _with_retry(lambda tid=telegram_id, mid=delivery.telegram_message_id:
                    bot.edit_message_text(chat_id=tid, message_id=mid, text=full_text))
        except TelegramForbiddenError:
            await _mark_blocked(delivery.account_id)
        except Exception as exc:
            logger.error("Failed to edit for tg_id=%d: %s", telegram_id, exc)

    new_recipients = [(acc_id, tg_id) for acc_id, tg_id in current_recipients if acc_id not in existing_account_ids]
    for account_id, telegram_id in new_recipients:
        await asyncio.sleep(SEND_RATE)
        try:
            msg = await _with_retry(lambda tid=telegram_id, iu=image_url: _send_one(bot, tid, full_text, iu))
            new_deliveries.append(NewsDelivery(
                news_id=news_id, account_id=account_id,
                telegram_message_id=msg.message_id, has_media=bool(image_url),
            ))
        except TelegramForbiddenError:
            logger.info("Account %d blocked the bot", account_id)
            await _mark_blocked(account_id)
        except Exception as exc:
            logger.error("Failed to send to new account_id=%d: %s", account_id, exc)

    if updated_delivery_ids or new_deliveries:
        async with async_session_maker() as session:
            if updated_delivery_ids:
                delivery_map = {d.id: d for d, _ in deliveries_rows}
                for delivery_id in updated_delivery_ids:
                    d = delivery_map[delivery_id]
                    await session.execute(
                        update(NewsDelivery)
                        .where(NewsDelivery.id == delivery_id)
                        .values(telegram_message_id=d.telegram_message_id, has_media=d.has_media)
                    )
            if new_deliveries:
                session.add_all(new_deliveries)
            await session.commit()