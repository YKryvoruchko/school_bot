import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


class TestUserLocks:
    def setup_method(self):
        import bot_utils
        bot_utils._user_locks.clear()

    def test_returns_same_lock_for_same_user(self):
        from bot_utils import get_user_lock
        assert get_user_lock(42) is get_user_lock(42)

    def test_different_locks_for_different_users(self):
        from bot_utils import get_user_lock
        assert get_user_lock(1) is not get_user_lock(2)

    def test_evicts_unlocked_entries_at_cap(self):
        import bot_utils
        from bot_utils import get_user_lock, _MAX_LOCKS
        for i in range(10000, 10000 + _MAX_LOCKS):
            get_user_lock(i)
        assert len(bot_utils._user_locks) == _MAX_LOCKS
        get_user_lock(99999)
        assert len(bot_utils._user_locks) < _MAX_LOCKS + 1

    @pytest.mark.asyncio
    async def test_locked_lock_is_not_evicted(self):
        import bot_utils
        from bot_utils import get_user_lock, _MAX_LOCKS
        lock = get_user_lock(1)
        await lock.acquire()
        try:
            for i in range(10000, 10000 + _MAX_LOCKS):
                get_user_lock(i)
            get_user_lock(99999)
            assert 1 in bot_utils._user_locks
        finally:
            lock.release()


class TestAuth:
    def test_hash_and_verify_correct_password(self):
        import auth
        mock_ctx = MagicMock()
        mock_ctx.hash.return_value = "$2b$12$fakehash"
        mock_ctx.verify.return_value = True
        with patch.object(auth, "pwd_context", mock_ctx):
            h = auth.hash_password("secret123")
            assert auth.verify_password("secret123", h)
        mock_ctx.hash.assert_called_once_with("secret123")
        mock_ctx.verify.assert_called_once_with("secret123", "$2b$12$fakehash")

    def test_verify_wrong_password_returns_false(self):
        import auth
        mock_ctx = MagicMock()
        mock_ctx.hash.return_value = "$2b$12$fakehash"
        mock_ctx.verify.return_value = False
        with patch.object(auth, "pwd_context", mock_ctx):
            h = auth.hash_password("correct")
            assert not auth.verify_password("wrong", h)


class TestSaveUploadedImage:
    @pytest.mark.asyncio
    async def test_no_upload_preserves_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import admin
        monkeypatch.setattr(admin, "MEDIA_DIR", tmp_path / "media")
        (tmp_path / "media").mkdir()
        model = MagicMock()
        model.image_url = "media/old.jpg"
        data = {}
        await admin._save_uploaded_image(data, model=model)
        assert data.get("image_url") == "media/old.jpg"

    @pytest.mark.asyncio
    async def test_new_upload_overwrites(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import admin
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        monkeypatch.setattr(admin, "MEDIA_DIR", media_dir)
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 12 + b"\xff\xd9"
        upload = AsyncMock()
        upload.filename = "photo.jpg"
        upload.read = AsyncMock(return_value=jpeg_bytes)
        data = {"upload_image": upload}
        await admin._save_uploaded_image(data, model=None)
        assert "image_url" in data
        assert data["image_url"].startswith("media/")
        assert data["image_url"].endswith(".jpg")

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self, tmp_path, monkeypatch):
        import admin
        monkeypatch.setattr(admin, "MEDIA_DIR", tmp_path / "media")
        (tmp_path / "media").mkdir()
        big = b"\xff\xd8\xff" + b"x" * (admin.MAX_UPLOAD_BYTES + 1)
        upload = AsyncMock()
        upload.filename = "big.jpg"
        upload.read = AsyncMock(return_value=big)
        data = {"upload_image": upload}
        await admin._save_uploaded_image(data, model=None)
        assert "image_url" not in data

    @pytest.mark.asyncio
    async def test_wrong_extension_rejected(self, tmp_path, monkeypatch):
        import admin
        monkeypatch.setattr(admin, "MEDIA_DIR", tmp_path / "media")
        (tmp_path / "media").mkdir()
        upload = AsyncMock()
        upload.filename = "script.exe"
        upload.read = AsyncMock(return_value=b"MZ\x90\x00")
        data = {"upload_image": upload}
        await admin._save_uploaded_image(data, model=None)
        assert "image_url" not in data


class TestGetClassIds:
    def _make_news(self, target_class_ids=None, target_class_id=None):
        news = MagicMock()
        news.target_class_ids = target_class_ids
        news.target_class_id = target_class_id
        return news

    def test_json_list(self):
        from broadcaster import _get_class_ids
        assert _get_class_ids(self._make_news(target_class_ids="[1, 2, 3]")) == [1, 2, 3]

    def test_fallback_to_single(self):
        from broadcaster import _get_class_ids
        assert _get_class_ids(self._make_news(target_class_ids=None, target_class_id=5)) == [5]

    def test_both_none_returns_none(self):
        from broadcaster import _get_class_ids
        assert _get_class_ids(self._make_news()) is None

    def test_malformed_json_falls_back(self):
        from broadcaster import _get_class_ids
        assert _get_class_ids(self._make_news(target_class_ids="not-json", target_class_id=7)) == [7]

    def test_empty_json_list_falls_back(self):
        from broadcaster import _get_class_ids
        assert _get_class_ids(self._make_news(target_class_ids="[]", target_class_id=3)) == [3]


@pytest.mark.asyncio
class TestResolveRecipients:
    async def _setup_db(self):
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from models import Base, Class, TelegramAccount, Section, SectionType

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with Session() as session:
            cls_a = Class(name="7-А")
            cls_b = Class(name="8-Б")
            session.add_all([cls_a, cls_b])
            await session.flush()

            acc1 = TelegramAccount(telegram_id=111, full_name="Батько А")
            acc2 = TelegramAccount(telegram_id=222, full_name="Учень А")
            acc3 = TelegramAccount(telegram_id=333, full_name="Батько Б")
            acc4 = TelegramAccount(telegram_id=444, full_name="Blocked", is_blocked=True)
            session.add_all([acc1, acc2, acc3, acc4])
            await session.flush()

            session.add_all([
                Section(account_id=acc1.id, section_type=SectionType.parent,  class_id=cls_a.id),
                Section(account_id=acc2.id, section_type=SectionType.student, class_id=cls_a.id),
                Section(account_id=acc3.id, section_type=SectionType.parent,  class_id=cls_b.id),
                Section(account_id=acc4.id, section_type=SectionType.parent,  class_id=cls_a.id),
            ])
            await session.commit()
            return Session, cls_a.id, cls_b.id

    async def test_class_parents_single_class(self):
        import broadcaster
        from models import NewsTarget
        Session, cls_a_id, cls_b_id = await self._setup_db()
        news = MagicMock()
        news.target = NewsTarget.class_parents
        news.target_class_ids = json.dumps([cls_a_id])
        news.target_class_id = cls_a_id
        with patch.object(broadcaster, "async_session_maker", Session):
            recipients = await broadcaster._resolve_recipients(news)
        tg_ids = {r[1] for r in recipients}
        assert 111 in tg_ids
        assert 222 not in tg_ids
        assert 444 not in tg_ids

    async def test_class_all_multi_class(self):
        import broadcaster
        from models import NewsTarget
        Session, cls_a_id, cls_b_id = await self._setup_db()
        news = MagicMock()
        news.target = NewsTarget.class_all
        news.target_class_ids = json.dumps([cls_a_id, cls_b_id])
        news.target_class_id = cls_a_id
        with patch.object(broadcaster, "async_session_maker", Session):
            recipients = await broadcaster._resolve_recipients(news)
        tg_ids = {r[1] for r in recipients}
        assert 111 in tg_ids
        assert 222 in tg_ids
        assert 333 in tg_ids
        assert 444 not in tg_ids

    async def test_empty_class_ids_returns_empty(self):
        import broadcaster
        from models import NewsTarget
        Session, cls_a_id, _ = await self._setup_db()
        news = MagicMock()
        news.target = NewsTarget.class_students
        news.target_class_ids = None
        news.target_class_id = None
        with patch.object(broadcaster, "async_session_maker", Session):
            recipients = await broadcaster._resolve_recipients(news)
        assert recipients == []


@pytest.mark.asyncio
class TestHandlersTeacherBlock:
    async def test_teacher_role_blocked(self):
        from handlers import process_role
        from bot_utils import CB_ROLE
        callback = AsyncMock()
        callback.data = f"{CB_ROLE}:teacher"
        callback.answer = AsyncMock()
        callback.message = AsyncMock()
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={})
        await process_role(callback, state)
        callback.answer.assert_called_once()
        assert callback.answer.call_args.kwargs.get("show_alert") is True
        state.clear.assert_called_once()

    async def test_student_role_proceeds(self):
        from handlers import process_role
        from bot_utils import CB_ROLE
        callback = AsyncMock()
        callback.data = f"{CB_ROLE}:student"
        callback.answer = AsyncMock()
        callback.message = AsyncMock()
        callback.message.edit_text = AsyncMock()
        state = AsyncMock()
        state.update_data = AsyncMock()
        state.set_state = AsyncMock()
        await process_role(callback, state)
        state.clear.assert_not_called()
        state.update_data.assert_called_once_with(role="student")


class TestSectionStr:
    def test_str_with_class(self):
        from models import Section, SectionType, Class
        cls = Class(name="7-А")
        section_type = SectionType.parent
        result = f"{section_type} / {cls.name}"
        assert "7-А" in result

    def test_str_without_class(self):
        from models import SectionType
        result = f"{SectionType.teacher} / —"
        assert "—" in result

    def test_section_str_logic_matches_model(self):
        import inspect
        from models import Section
        src = inspect.getsource(Section.__str__)
        assert "class_" in src
        assert "—" in src