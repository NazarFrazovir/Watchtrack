"""ORM-моделі предметної області (6 сутностей).

User ──< UserTitle >── Title ──< Season ──< Episode
User ──< EpisodeWatch >────────────────────── Episode
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TitleType(enum.StrEnum):
    MOVIE = "movie"
    SERIES = "series"


class WatchStatus(enum.StrEnum):
    PLANNED = "planned"
    WATCHING = "watching"
    COMPLETED = "completed"
    DROPPED = "dropped"


def _pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    """Зберігаємо в БД значення ('movie'), а не імена ('MOVIE') елементів enum."""
    return Enum(enum_cls, name=name, values_callable=lambda members: [m.value for m in members])


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Title(Base):
    """Фільм або серіал."""

    __tablename__ = "titles"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[TitleType] = mapped_column(_pg_enum(TitleType, "title_type"))
    name: Mapped[str] = mapped_column(String(255))
    original_name: Mapped[str | None] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    description: Mapped[str | None] = mapped_column(Text)
    # Для фільму — тривалість фільму, для серіалу — типова тривалість серії.
    runtime_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    genres: Mapped[list[str]] = mapped_column(ARRAY(String(32)), server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # lazy="raise": будь-яке неявне довантаження падає з помилкою — захист від N+1.
    seasons: Mapped[list["Season"]] = relationship(
        back_populates="title",
        cascade="all, delete-orphan",
        order_by="Season.number",
        lazy="raise",
        passive_deletes=True,  # видалення каскадом робить сама БД (ON DELETE CASCADE)
    )

    __table_args__ = (
        Index("ix_titles_name", "name"),
        Index("ix_titles_type_year", "type", "year"),
        Index("ix_titles_genres", "genres", postgresql_using="gin"),  # фільтр genres @> ARRAY[...]
    )


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    title_id: Mapped[int] = mapped_column(ForeignKey("titles.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str | None] = mapped_column(String(255))
    release_date: Mapped[date | None] = mapped_column(Date)

    title: Mapped[Title] = relationship(back_populates="seasons", lazy="raise")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="season",
        cascade="all, delete-orphan",
        order_by="Episode.number",
        lazy="raise",
        passive_deletes=True,
    )

    __table_args__ = (UniqueConstraint("title_id", "number", name="uq_seasons_title_id_number"),)


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str | None] = mapped_column(String(255))
    air_date: Mapped[date | None] = mapped_column(Date)
    runtime_minutes: Mapped[int | None] = mapped_column(SmallInteger)

    season: Mapped[Season] = relationship(back_populates="episodes", lazy="raise")

    __table_args__ = (UniqueConstraint("season_id", "number", name="uq_episodes_season_id_number"),)


class UserTitle(Base):
    """Тайтл у списку користувача: статус перегляду та оцінка."""

    __tablename__ = "user_titles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    title_id: Mapped[int] = mapped_column(
        ForeignKey("titles.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    status: Mapped[WatchStatus] = mapped_column(_pg_enum(WatchStatus, "watch_status"))
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    title: Mapped[Title] = relationship(lazy="raise")

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 10", name="rating_range"),
        Index("ix_user_titles_updated_at", "updated_at"),
    )


class EpisodeWatch(Base):
    """Факт перегляду серії користувачем. Найбільша таблиця системи."""

    __tablename__ = "episode_watches"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    watched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_episode_watches_watched_at", "watched_at"),)
