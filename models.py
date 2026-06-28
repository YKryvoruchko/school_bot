import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, String, Table, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WebUserRole(str, enum.Enum):
    superadmin = "superadmin"
    admin = "admin"
    teacher_lead = "teacher_lead"


class SectionType(str, enum.Enum):
    teacher = "teacher"
    student = "student"
    parent = "parent"


class NewsTarget(str, enum.Enum):
    all = "all"
    teachers = "teachers"
    students = "students"
    parents = "parents"
    class_students = "class_students"
    class_parents = "class_parents"
    class_all = "class_all"


class SchoolConfig(Base):
    __tablename__ = "school_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(255))
    director_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    admin_group_link: Mapped[str | None] = mapped_column(String(512), nullable=True)

    def __str__(self) -> str:
        return self.name


class WebUser(Base):
    __tablename__ = "web_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[WebUserRole] = mapped_column(default=WebUserRole.admin)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    telegram_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    extra_info: Mapped[str | None] = mapped_column(Text, nullable=True)

    managed_class: Mapped["Class | None"] = relationship(back_populates="teacher_lead")
    news: Mapped[list["News"]] = relationship(back_populates="author")

    def __str__(self) -> str:
        return self.full_name or self.username


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)

    teacher_lead: Mapped["WebUser | None"] = relationship(back_populates="managed_class")
    sections: Mapped[list["Section"]] = relationship(back_populates="class_")

    def __str__(self) -> str:
        return self.name


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(255))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sections: Mapped[list["Section"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    deliveries: Mapped[list["NewsDelivery"]] = relationship(back_populates="account", cascade="all, delete-orphan")

    def __str__(self) -> str:
        return self.full_name


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("telegram_accounts.id", ondelete="CASCADE"))
    section_type: Mapped[SectionType]
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)

    account: Mapped["TelegramAccount"] = relationship(back_populates="sections")
    class_: Mapped["Class | None"] = relationship(back_populates="sections")

    def __str__(self) -> str:
        class_name = self.class_.name if self.class_ else "—"
        return f"{self.section_type} / {class_name}"


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    target: Mapped[NewsTarget] = mapped_column(default=NewsTarget.all)
    target_class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id"), nullable=True)
    target_class_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("web_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, default=1)

    author: Mapped["WebUser"] = relationship(back_populates="news")
    target_class: Mapped["Class | None"] = relationship()
    deliveries: Mapped[list["NewsDelivery"]] = relationship(back_populates="news", cascade="all, delete-orphan")

    __mapper_args__ = {
        "version_id_col": version,
        "version_id_generator": lambda v: (v or 0) + 1,
    }

    upload_image = None

    def __str__(self) -> str:
        return self.title


class NewsDelivery(Base):
    __tablename__ = "news_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("telegram_accounts.id", ondelete="CASCADE"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)

    news: Mapped["News"] = relationship(back_populates="deliveries")
    account: Mapped["TelegramAccount"] = relationship(back_populates="deliveries")