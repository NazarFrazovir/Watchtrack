"""Підключення до PostgreSQL: асинхронний engine, пул з'єднань і фабрика сесій."""

from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

# Єдині правила іменування обмежень: міграції та моделі завжди генерують однакові імена.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Значення, згенеровані БД (created_at, updated_at), одразу повертаються через RETURNING,
    # тож після commit не потрібен додатковий SELECT.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 — конвенція SQLAlchemy


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_pre_ping=True,
    echo=_settings.db_echo,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-залежність: одна сесія (і одне з'єднання з пулу) на HTTP-запит."""
    async with SessionFactory() as session:
        yield session
