"""Аналітика: статистика користувача та тренди. Це найважчі запити системи."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, distinct, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Episode, EpisodeWatch, Season, Title, TitleType, UserTitle, WatchStatus
from app.schemas import TitleOut, TrendingItem, TrendingOut, UserStats
from app.services.users import get_user_or_404

ACTIVE_STATUSES = (WatchStatus.WATCHING, WatchStatus.COMPLETED)


async def get_user_stats(session: AsyncSession, user_id: int) -> UserStats:
    await get_user_or_404(session, user_id)

    by_status_rows = await session.execute(
        select(UserTitle.status, func.count()).where(UserTitle.user_id == user_id).group_by(UserTitle.status)
    )
    by_status = {status: 0 for status in WatchStatus}
    by_status.update({status: count for status, count in by_status_rows})

    episodes_watched, episode_minutes = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(func.coalesce(Episode.runtime_minutes, Title.runtime_minutes, 0)), 0),
            )
            .select_from(EpisodeWatch)
            .join(Episode, Episode.id == EpisodeWatch.episode_id)
            .join(Season, Season.id == Episode.season_id)
            .join(Title, Title.id == Season.title_id)
            .where(EpisodeWatch.user_id == user_id)
        )
    ).one()

    movies_completed, movie_minutes = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(func.coalesce(Title.runtime_minutes, 0)), 0))
            .select_from(UserTitle)
            .join(Title, Title.id == UserTitle.title_id)
            .where(
                UserTitle.user_id == user_id,
                UserTitle.status == WatchStatus.COMPLETED,
                Title.type == TitleType.MOVIE,
            )
        )
    ).one()

    avg_rating = await session.scalar(
        select(func.avg(UserTitle.rating)).where(UserTitle.user_id == user_id, UserTitle.rating.is_not(None))
    )

    minutes = int(episode_minutes) + int(movie_minutes)
    return UserStats(
        user_id=user_id,
        titles_by_status=by_status,
        movies_completed=movies_completed,
        episodes_watched=episodes_watched,
        minutes_watched=minutes,
        hours_watched=round(minutes / 60, 1),
        avg_rating_given=round(float(avg_rating), 2) if avg_rating is not None else None,
    )


async def get_trending(
    session: AsyncSession, days: int, limit: int, title_type: TitleType | None
) -> TrendingOut:
    """Топ тайтлів за кількістю унікальних глядачів за останні `days` днів.

    Активність = переглянуті серії (серіали) + зміни статусу на watching/completed (фільми й серіали).
    Запит агрегує сирі події на кожен виклик — головний кандидат на кешування (Bottleneck #1).
    """
    since = datetime.now(UTC) - timedelta(days=days)

    episode_activity = (
        select(Season.title_id.label("title_id"), EpisodeWatch.user_id.label("user_id"))
        .select_from(EpisodeWatch)
        .join(Episode, Episode.id == EpisodeWatch.episode_id)
        .join(Season, Season.id == Episode.season_id)
        .where(EpisodeWatch.watched_at >= since)
    )
    list_activity = select(UserTitle.title_id, UserTitle.user_id).where(
        UserTitle.updated_at >= since, UserTitle.status.in_(ACTIVE_STATUSES)
    )
    activity = union_all(episode_activity, list_activity).subquery()

    viewers = func.count(distinct(activity.c.user_id)).label("viewers")
    stmt = (
        select(activity.c.title_id, viewers)
        .join(Title, Title.id == activity.c.title_id)
        .group_by(activity.c.title_id)
        .order_by(desc(viewers), activity.c.title_id)
        .limit(limit)
    )
    if title_type is not None:
        stmt = stmt.where(Title.type == title_type)

    ranking = (await session.execute(stmt)).all()
    if not ranking:
        return TrendingOut(days=days, items=[])

    titles = {
        t.id: t for t in await session.scalars(select(Title).where(Title.id.in_([r[0] for r in ranking])))
    }
    return TrendingOut(
        days=days,
        items=[
            TrendingItem(title=TitleOut.model_validate(titles[title_id]), viewers=count)
            for title_id, count in ranking
        ],
    )
