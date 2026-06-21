import enum

from sqlalchemy import BigInteger, Column, ForeignKey, String, Table, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    superadmin = "superadmin"
    admin = "admin"


parent_class_association = Table(
    "parent_class_association",
    Base.metadata,
    Column("parent_id", ForeignKey("parents.id"), primary_key=True),
    Column("class_id", ForeignKey("classes.id"), primary_key=True),
)


class School(Base):
    __tablename__ = "schools"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)

    classes: Mapped[list["Class"]] = relationship(back_populates="school", cascade="all, delete-orphan")
    news: Mapped[list["News"]] = relationship(back_populates="school", cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship(back_populates="school")

    def __str__(self) -> str:
        return self.name


class Class(Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"))

    school: Mapped["School"] = relationship(back_populates="classes")

    def __str__(self) -> str:
        return self.name


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(default=UserRole.admin)
    school_id: Mapped[int | None] = mapped_column(ForeignKey("schools.id"), nullable=True)

    school: Mapped["School | None"] = relationship(back_populates="users")

    def __str__(self) -> str:
        return self.username


class News(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"))

    author: Mapped["User"] = relationship()
    school: Mapped["School"] = relationship(back_populates="news")

    def __str__(self) -> str:
        return self.title


class Parent(Base):
    __tablename__ = "parents"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    full_name: Mapped[str] = mapped_column(String(255))

    classes: Mapped[list["Class"]] = relationship(secondary=parent_class_association, backref="parents")

    def __str__(self) -> str:
        return self.full_name