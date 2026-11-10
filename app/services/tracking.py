"""Трекінг перегляду: списки користувача, відмітки серій, прогрес.

Ключове бізнес-правило: статус тайтлу в списку оновлюється автоматично
на основі переглянутих серій:
  * перша переглянута серія         -> watching (тайтл додається у список);
  * переглянуто всі серії серіалу    -> completed;
  * зняли відмітку з серії completed -> watching.
"""

from sqlalchemy import delete, exists, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.errors import BusinessRuleError, NotFoundError
from app.models import Episode, EpisodeWatch, Season, UserTitle, WatchStatus
from app.schemas import NextEpisode, Progress, TitleOut, UserTitleOut, UserTitleUpsert, WatchResult
from app.services.titles import get_title_or_404
from app.services.users import get_user_or_404

# ---------- Список користувача ----------


async def upsert_user_title(
    session: AsyncSession, user_id: int, title_id: int, data: UserTitleUpsert
) -> tuple[UserTitleOut, bool]:
    """Встановлює статус і оцінку тайтлу. Повертає (запис, чи_створено)."""
    await get_user_or_404(session, user_id)
    title = await get_title_or_404(session, title_id)

    if data.rating is not None and data.status is WatchStatus.PLANNED:
        raise BusinessRuleError("You cannot rate a title you have not started watching")

    user_title = await session.get(UserTitle, (user_id, title_id))
    created = user_title is None
    if created:
        user_title = UserTitle(user_id=user_id, title_id=title_id, status=data.status, rating=data.rating)
        session.add(user_title)
    else:
        user_title.status = data.status
        user_title.rating = data.rating

    await session.commit()
    result = UserTitleOut(
        title=TitleOut.model_validate(title),
        status=user_title.status,
        rating=user_title.rating,
        updated_at=user_title.updated_at,
    )
    return result, created


async def list_user_titles(
    session: AsyncSession, user_id: int, status: WatchStatus | None, limit: int, offset: int
) -> tuple[list[UserTitle], int]:
    await get_user_or_404(session, user_id)

    stmt = select(UserTitle).where(UserTitle.user_id == user_id)
    if status is not None:
        stmt = stmt.where(UserTitle.status == status)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    items = await session.scalars(
        stmt.options(selectinload(UserTitle.title))  # 1 додатковий запит замість N (захист від N+1)
        .order_by(UserTitle.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(items), total or 0


async def remove_user_title(session: AsyncSession, user_id: int, title_id: int) -> None:
    """Прибирає тайтл зі списку разом з усіма відмітками його серій."""
    user_title = await session.get(UserTitle, (user_id, title_id))
    if user_title is None:
        raise NotFoundError(f"Title {title_id} is not in the list of user {user_id}")

    title_episodes = select(Episode.id).join(Season).where(Season.title_id == title_id)
    await session.execute(
        delete(EpisodeWatch).where(
            EpisodeWatch.user_id == user_id, EpisodeWatch.episode_id.in_(title_episodes)
        )
    )
    await session.delete(user_title)
    await session.commit()


# ---------- Відмітки серій ----------


async def mark_episode(session: AsyncSession, user_id: int, episode_id: int) -> WatchResult:
    """Ідемпотентна відмітка: повторний запит нічого не дублює (ON CONFLICT DO NOTHING)."""
    await get_user_or_404(session, user_id)
    title_id = await _title_id_of_episode(session, episode_id)
    await _lock_user_title(session, user_id, title_id)

    inserted = await session.execute(
        pg_insert(EpisodeWatch)
        .values(user_id=user_id, episode_id=episode_id)
        .on_conflict_do_nothing()
        .returning(EpisodeWatch.episode_id)
    )
    marked = len(inserted.all())

    progress = await _sync_status(session, user_id, title_id)
    await session.commit()
    return WatchResult(episodes_marked=marked, progress=progress)


async def mark_season(session: AsyncSession, user_id: int, season_id: int) -> WatchResult:
    """Відмічає всі серії сезону одним INSERT ... SELECT."""
    await get_user_or_404(session, user_id)
    season = await session.get(Season, season_id)
    if season is None:
        raise NotFoundError(f"Season {season_id} not found")
    await _lock_user_title(session, user_id, season.title_id)

    inserted = await session.execute(
        pg_insert(EpisodeWatch)
        .from_select(
            ["user_id", "episode_id"],
            select(literal(user_id), Episode.id).where(Episode.season_id == season_id),
        )
        .on_conflict_do_nothing()
        .returning(EpisodeWatch.episode_id)
    )
    marked = len(inserted.all())

    progress = await _sync_status(session, user_id, season.title_id)
    await session.commit()
    return WatchResult(episodes_marked=marked, progress=progress)


async def unmark_episode(session: AsyncSession, user_id: int, episode_id: int) -> Progress:
    await get_user_or_404(session, user_id)
    title_id = await _title_id_of_episode(session, episode_id)
    await _lock_user_title(session, user_id, title_id)

    deleted = await session.execute(
        delete(EpisodeWatch)
        .where(EpisodeWatch.user_id == user_id, EpisodeWatch.episode_id == episode_id)
        .returning(EpisodeWatch.episode_id)
    )
    if not deleted.all():
        raise NotFoundError(f"Episode {episode_id} is not marked as watched")

    progress = await _sync_status(session, user_id, title_id)
    await session.commit()
    return progress


async def get_progress(session: AsyncSession, user_id: int, title_id: int) -> Progress:
    await get_user_or_404(session, user_id)
    await get_title_or_404(session, title_id)
    user_title = await session.get(UserTitle, (user_id, title_id))
    watched, total = await _episode_counts(session, user_id, title_id)
    return await _build_progress(session, user_id, title_id, user_title, watched, total)


# ---------- Внутрішні допоміжні функції ----------


async def _lock_user_title(session: AsyncSession, user_id: int, title_id: int) -> None:
    """Серіалізує зміни прогресу одного користувача по одному тайтлу.

    Без блокування дві паралельні відмітки двох останніх серій бачать «переглянуто N-1 з N»
    і жодна не виставляє статус completed (аномалія write skew).
    Блокування транзакційне: знімається автоматично при commit/rollback.
    """
    await session.execute(select(func.pg_advisory_xact_lock(user_id, title_id)))


async def _title_id_of_episode(session: AsyncSession, episode_id: int) -> int:
    title_id = await session.scalar(
        select(Season.title_id).join(Episode, Episode.season_id == Season.id).where(Episode.id == episode_id)
    )
    if title_id is None:
        raise NotFoundError(f"Episode {episode_id} not found")
    return title_id


async def _episode_counts(session: AsyncSession, user_id: int, title_id: int) -> tuple[int, int]:
    total = await session.scalar(
        select(func.count(Episode.id)).join(Season).where(Season.title_id == title_id)
    )
    watched = await session.scalar(
        select(func.count())
        .select_from(EpisodeWatch)
        .join(Episode, Episode.id == EpisodeWatch.episode_id)
        .join(Season, Season.id == Episode.season_id)
        .where(Season.title_id == title_id, EpisodeWatch.user_id == user_id)
    )
    return watched or 0, total or 0


def _desired_status(current: WatchStatus | None, watched: int, total: int) -> WatchStatus | None:
    if total > 0 and watched == total:
        return WatchStatus.COMPLETED
    if watched > 0 and current in (None, WatchStatus.PLANNED, WatchStatus.COMPLETED, WatchStatus.DROPPED):
        return WatchStatus.WATCHING
    return current


async def _sync_status(session: AsyncSession, user_id: int, title_id: int) -> Progress:
    watched, total = await _episode_counts(session, user_id, title_id)
    user_title = await session.get(UserTitle, (user_id, title_id))
    desired = _desired_status(user_title.status if user_title else None, watched, total)

    if user_title is None and desired is not None:
        # ON CONFLICT захищає від гонки двох паралельних відміток одного користувача.
        await session.execute(
            pg_insert(UserTitle)
            .values(user_id=user_id, title_id=title_id, status=desired)
            .on_conflict_do_nothing()
        )
        user_title = await session.get(UserTitle, (user_id, title_id), populate_existing=True)

    if user_title is not None and desired is not None and user_title.status is not desired:
        user_title.status = desired

    await session.flush()
    return await _build_progress(session, user_id, title_id, user_title, watched, total)


async def _build_progress(
    session: AsyncSession,
    user_id: int,
    title_id: int,
    user_title: UserTitle | None,
    watched: int,
    total: int,
) -> Progress:
    already_watched = exists().where(EpisodeWatch.user_id == user_id, EpisodeWatch.episode_id == Episode.id)
    next_row = (
        await session.execute(
            select(Episode.id, Season.number, Episode.number, Episode.name)
            .join(Season, Season.id == Episode.season_id)
            .where(Season.title_id == title_id, ~already_watched)
            .order_by((Season.number == 0).asc(), Season.number, Episode.number)  # спецвипуски в кінці
            .limit(1)
        )
    ).first()

    return Progress(
        title_id=title_id,
        status=user_title.status if user_title else None,
        watched_episodes=watched,
        total_episodes=total,
        percent=round(watched / total * 100, 1) if total else 0.0,
        next_episode=(
            NextEpisode(
                episode_id=next_row[0],
                season_number=next_row[1],
                episode_number=next_row[2],
                name=next_row[3],
            )
            if next_row
            else None
        ),
    )
