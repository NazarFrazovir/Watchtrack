from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.models import WatchStatus
from app.routers.common import ERR_404, ERR_422, Limit, Offset, SessionDep
from app.schemas import Progress, UserTitleOut, UserTitlePage, UserTitleUpsert, WatchResult
from app.services import tracking as tracking_service

router = APIRouter(prefix="/users/{user_id}", tags=["tracking"])


@router.get("/titles", response_model=UserTitlePage, responses=ERR_404, summary="Списки користувача")
async def list_user_titles(
    user_id: int,
    session: SessionDep,
    status_filter: Annotated[WatchStatus | None, Query(alias="status")] = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> UserTitlePage:
    items, total = await tracking_service.list_user_titles(session, user_id, status_filter, limit, offset)
    return UserTitlePage(
        items=[UserTitleOut.model_validate(i) for i in items], total=total, limit=limit, offset=offset
    )


@router.put(
    "/titles/{title_id}",
    response_model=UserTitleOut,
    responses={201: {"model": UserTitleOut, "description": "Додано у список"}, **ERR_404, **ERR_422},
    summary="Встановити статус і оцінку (додати у список або оновити)",
)
async def upsert_user_title(
    user_id: int, title_id: int, data: UserTitleUpsert, session: SessionDep, response: Response
) -> UserTitleOut:
    result, created = await tracking_service.upsert_user_title(session, user_id, title_id, data)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.delete(
    "/titles/{title_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERR_404,
    summary="Прибрати тайтл зі списку разом із відмітками серій",
)
async def remove_user_title(user_id: int, title_id: int, session: SessionDep) -> Response:
    await tracking_service.remove_user_title(session, user_id, title_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/titles/{title_id}/progress",
    response_model=Progress,
    responses=ERR_404,
    summary="Прогрес перегляду і наступна серія",
)
async def get_progress(user_id: int, title_id: int, session: SessionDep) -> Progress:
    return await tracking_service.get_progress(session, user_id, title_id)


@router.post(
    "/episodes/{episode_id}/watch",
    response_model=WatchResult,
    responses={201: {"model": WatchResult, "description": "Серію відмічено"}, **ERR_404},
    summary="Відмітити серію переглянутою (ідемпотентно)",
)
async def mark_episode(user_id: int, episode_id: int, session: SessionDep, response: Response) -> WatchResult:
    result = await tracking_service.mark_episode(session, user_id, episode_id)
    response.status_code = status.HTTP_201_CREATED if result.episodes_marked else status.HTTP_200_OK
    return result


@router.delete(
    "/episodes/{episode_id}/watch",
    response_model=Progress,
    responses=ERR_404,
    summary="Зняти відмітку перегляду",
)
async def unmark_episode(user_id: int, episode_id: int, session: SessionDep) -> Progress:
    return await tracking_service.unmark_episode(session, user_id, episode_id)


@router.post(
    "/seasons/{season_id}/watch",
    response_model=WatchResult,
    responses={201: {"model": WatchResult, "description": "Відмічено нові серії"}, **ERR_404},
    summary="Відмітити весь сезон переглянутим",
)
async def mark_season(user_id: int, season_id: int, session: SessionDep, response: Response) -> WatchResult:
    result = await tracking_service.mark_season(session, user_id, season_id)
    response.status_code = status.HTTP_201_CREATED if result.episodes_marked else status.HTTP_200_OK
    return result
