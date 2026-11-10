"""Каталог: фільми, серіали, сезони, серії."""

from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.errors import BusinessRuleError, ConflictError, NotFoundError
from app.models import Episode, Season, Title, TitleType, UserTitle, WatchStatus
from app.schemas import (
    EpisodeIn,
    SeasonIn,
    SeasonSummary,
    TitleCreate,
    TitleDetails,
    TitleOut,
    TitleUpdate,
)

TitleSort = Literal["name", "-year", "year", "-created"]

_SORTS = {
    "name": (Title.name.asc(), Title.id.asc()),
    "year": (Title.year.asc().nulls_last(), Title.id.asc()),
    "-year": (Title.year.desc().nulls_last(), Title.id.asc()),
    "-created": (Title.created_at.desc(), Title.id.desc()),
}


def _build_season(data: SeasonIn) -> Season:
    return Season(
        number=data.number,
        name=data.name,
        release_date=data.release_date,
        episodes=[Episode(**e.model_dump()) for e in data.episodes],
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_title_or_404(session: AsyncSession, title_id: int) -> Title:
    title = await session.get(Title, title_id)
    if title is None:
        raise NotFoundError(f"Title {title_id} not found")
    return title


async def create_title(session: AsyncSession, data: TitleCreate) -> TitleDetails:
    title = Title(
        **data.model_dump(exclude={"seasons"}),
        seasons=[_build_season(s) for s in data.seasons],
    )
    session.add(title)
    await session.commit()
    return await get_title_details(session, title.id)


async def list_titles(
    session: AsyncSession,
    *,
    title_type: TitleType | None,
    genre: str | None,
    year: int | None,
    query: str | None,
    sort: TitleSort,
    limit: int,
    offset: int,
) -> tuple[list[Title], int]:
    stmt = select(Title)
    if title_type is not None:
        stmt = stmt.where(Title.type == title_type)
    if genre:
        stmt = stmt.where(Title.genres.contains([genre.lower()]))
    if year is not None:
        stmt = stmt.where(Title.year == year)
    if query:
        pattern = f"%{_escape_like(query)}%"
        # Свідомо простий пошук: ILIKE '%...%' не використовує B-tree індекс (див. Bottleneck #3).
        stmt = stmt.where(
            or_(Title.name.ilike(pattern, escape="\\"), Title.original_name.ilike(pattern, escape="\\"))
        )

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = await session.scalars(stmt.order_by(*_SORTS[sort]).limit(limit).offset(offset))
    return list(items), total or 0


async def get_title_details(session: AsyncSession, title_id: int) -> TitleDetails:
    title = await get_title_or_404(session, title_id)

    seasons = await session.execute(
        select(Season.id, Season.number, Season.name, Season.release_date, func.count(Episode.id))
        .outerjoin(Episode, Episode.season_id == Season.id)
        .where(Season.title_id == title_id)
        .group_by(Season.id)
        .order_by(Season.number)
    )

    # Агрегати рахуються на кожен запит — це навмисне «вузьке місце» базової версії (Bottleneck #2).
    avg_rating, ratings_count, viewers_count = (
        await session.execute(
            select(
                func.avg(UserTitle.rating),
                func.count(UserTitle.rating),
                func.count().filter(UserTitle.status.in_([WatchStatus.WATCHING, WatchStatus.COMPLETED])),
            ).where(UserTitle.title_id == title_id)
        )
    ).one()

    return TitleDetails(
        **TitleOut.model_validate(title).model_dump(),
        seasons=[
            SeasonSummary(id=sid, number=num, name=name, release_date=rel, episodes_count=cnt)
            for sid, num, name, rel, cnt in seasons
        ],
        avg_rating=round(float(avg_rating), 2) if avg_rating is not None else None,
        ratings_count=ratings_count,
        viewers_count=viewers_count,
    )


async def update_title(session: AsyncSession, title_id: int, data: TitleUpdate) -> TitleDetails:
    title = await get_title_or_404(session, title_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(title, field, value)
    await session.commit()
    return await get_title_details(session, title_id)


async def delete_title(session: AsyncSession, title_id: int) -> None:
    title = await get_title_or_404(session, title_id)
    await session.delete(title)
    await session.commit()


async def _get_season(session: AsyncSession, title_id: int, number: int) -> Season | None:
    return await session.scalar(
        select(Season)
        .options(selectinload(Season.episodes))
        .where(Season.title_id == title_id, Season.number == number)
    )


async def get_season(session: AsyncSession, title_id: int, number: int) -> Season:
    await get_title_or_404(session, title_id)
    season = await _get_season(session, title_id, number)
    if season is None:
        raise NotFoundError(f"Season {number} of title {title_id} not found")
    return season


async def add_season(session: AsyncSession, title_id: int, data: SeasonIn) -> Season:
    title = await get_title_or_404(session, title_id)
    if title.type is not TitleType.SERIES:
        raise BusinessRuleError("Seasons can only be added to series")
    if await _get_season(session, title_id, data.number) is not None:
        raise ConflictError(f"Season {data.number} already exists")

    season = _build_season(data)
    season.title_id = title_id
    session.add(season)
    await session.commit()
    return await get_season(session, title_id, data.number)


async def add_episode(session: AsyncSession, title_id: int, season_number: int, data: EpisodeIn) -> Episode:
    """Вихід нової серії — саме ця подія запускає «пікову ніч прем'єри»."""
    season = await get_season(session, title_id, season_number)
    if any(e.number == data.number for e in season.episodes):
        raise ConflictError(f"Episode {data.number} already exists in season {season_number}")

    episode = Episode(season_id=season.id, **data.model_dump())
    session.add(episode)
    await session.commit()
    return episode
