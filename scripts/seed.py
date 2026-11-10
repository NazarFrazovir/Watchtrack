"""Демо-дані для захисту: 10 тайтлів, 3 користувачі, трохи активності.

Запуск:  python -m scripts.seed
Повторний запуск безпечний — якщо каталог не порожній, скрипт нічого не робить.
"""

import asyncio
import logging

from sqlalchemy import func, select

from app.database import SessionFactory, engine
from app.models import Season, Title, TitleType, WatchStatus
from app.schemas import EpisodeIn, SeasonIn, TitleCreate, UserCreate, UserTitleUpsert
from app.services import titles, tracking, users

logger = logging.getLogger("watchtrack.seed")


def _series(
    name: str,
    original: str,
    year: int,
    runtime: int,
    genres: list[str],
    episodes_per_season: list[int],
    description: str,
) -> TitleCreate:
    return TitleCreate(
        type=TitleType.SERIES,
        name=name,
        original_name=original,
        year=year,
        runtime_minutes=runtime,
        genres=genres,
        description=description,
        seasons=[
            SeasonIn(number=n, episodes=[EpisodeIn(number=e) for e in range(1, count + 1)])
            for n, count in enumerate(episodes_per_season, start=1)
        ],
    )


def _movie(
    name: str, original: str, year: int, runtime: int, genres: list[str], description: str
) -> TitleCreate:
    return TitleCreate(
        type=TitleType.MOVIE,
        name=name,
        original_name=original,
        year=year,
        runtime_minutes=runtime,
        genres=genres,
        description=description,
    )


CATALOG = [
    _series(
        "Пуститися берега",
        "Breaking Bad",
        2008,
        47,
        ["drama", "crime"],
        [7, 13, 13, 13, 16],
        "Вчитель хімії дізнається про смертельний діагноз і починає варити метамфетамін.",
    ),
    _series(
        "Чорнобиль",
        "Chernobyl",
        2019,
        60,
        ["drama", "history"],
        [5],
        "Мінісеріал про аварію на ЧАЕС 1986 року та ліквідацію її наслідків.",
    ),
    _series(
        "Темрява",
        "Dark",
        2017,
        55,
        ["sci-fi", "thriller", "drama"],
        [10, 10, 8],
        "Зникнення дитини в німецькому містечку відкриває заплутану історію чотирьох родин.",
    ),
    _series(
        "Аркейн",
        "Arcane",
        2021,
        40,
        ["animation", "action", "fantasy"],
        [9, 9],
        "Анімаційний серіал про двох сестер по різні боки конфлікту двох міст.",
    ),
    _series(
        "Одні з нас",
        "The Last of Us",
        2023,
        55,
        ["drama", "sci-fi"],
        [9, 7],
        "Контрабандист супроводжує дівчинку через постапокаліптичну Америку.",
    ),
    _movie(
        "Початок",
        "Inception",
        2010,
        148,
        ["sci-fi", "action", "thriller"],
        "Команда злодіїв викрадає ідеї, проникаючи у сни.",
    ),
    _movie(
        "Інтерстеллар",
        "Interstellar",
        2014,
        169,
        ["sci-fi", "drama", "adventure"],
        "Експедиція крізь червоточину в пошуках нового дому для людства.",
    ),
    _movie(
        "Матриця",
        "The Matrix",
        1999,
        136,
        ["sci-fi", "action"],
        "Хакер дізнається, що світ навколо — симуляція.",
    ),
    _movie(
        "Паразити",
        "Parasite",
        2019,
        132,
        ["thriller", "drama"],
        "Бідна родина поступово проникає в життя заможної.",
    ),
    _movie(
        "Дюна: Частина друга",
        "Dune: Part Two",
        2024,
        166,
        ["sci-fi", "adventure"],
        "Пол Атрід об'єднується з фрименами проти Харконненів.",
    ),
]

USERS = [
    UserCreate(username="alex", email="alex@example.com", display_name="Олексій"),
    UserCreate(username="maria", email="maria@example.com", display_name="Марія"),
    UserCreate(username="ivan", email="ivan@example.com", display_name="Іван"),
]


async def seed() -> None:
    async with SessionFactory() as session:
        if await session.scalar(select(func.count(Title.id))):
            logger.info("Catalog is not empty — seed skipped")
            return

        ids = {}
        for item in CATALOG:
            created = await titles.create_title(session, item)
            ids[item.original_name] = created.id

        alex, maria, ivan = [(await users.create_user(session, u)).id for u in USERS]

        async def season_id(title: str, number: int) -> int:
            return await session.scalar(
                select(Season.id).where(Season.title_id == ids[title], Season.number == number)
            )

        async def watch_seasons(user_id: int, title: str, numbers: list[int]) -> None:
            for number in numbers:
                await tracking.mark_season(session, user_id, await season_id(title, number))

        async def set_status(
            user_id: int, title: str, status: WatchStatus, rating: int | None = None
        ) -> None:
            await tracking.upsert_user_title(
                session, user_id, ids[title], UserTitleUpsert(status=status, rating=rating)
            )

        await watch_seasons(alex, "Breaking Bad", [1, 2])
        await watch_seasons(alex, "Chernobyl", [1])
        await set_status(alex, "Chernobyl", WatchStatus.COMPLETED, 10)
        await set_status(alex, "Inception", WatchStatus.COMPLETED, 9)
        await set_status(alex, "Dune: Part Two", WatchStatus.PLANNED)

        await watch_seasons(maria, "Dark", [1])
        await watch_seasons(maria, "Arcane", [1, 2])
        await set_status(maria, "Arcane", WatchStatus.COMPLETED, 9)
        await set_status(maria, "Interstellar", WatchStatus.COMPLETED, 10)
        await set_status(maria, "Breaking Bad", WatchStatus.PLANNED)

        await watch_seasons(ivan, "The Last of Us", [1])
        await watch_seasons(ivan, "Arcane", [1])
        await set_status(ivan, "The Matrix", WatchStatus.COMPLETED, 8)
        await set_status(ivan, "Parasite", WatchStatus.COMPLETED, 9)
        await set_status(ivan, "Dark", WatchStatus.DROPPED, 6)

        logger.info("Seeded %d titles and %d users", len(CATALOG), len(USERS))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        await seed()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
