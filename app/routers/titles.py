from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.models import TitleType
from app.routers.common import ERR_404, ERR_409, ERR_422, Limit, Offset, SessionDep
from app.schemas import (
    EpisodeIn,
    EpisodeOut,
    SeasonIn,
    SeasonOut,
    TitleCreate,
    TitleDetails,
    TitleOut,
    TitlePage,
    TitleUpdate,
    TrendingOut,
)
from app.services import stats as stats_service
from app.services import titles as titles_service
from app.services.titles import TitleSort

router = APIRouter(tags=["catalog"])

SeasonNumber = Annotated[int, Path(ge=0, le=100)]


@router.post(
    "/titles",
    response_model=TitleDetails,
    status_code=status.HTTP_201_CREATED,
    responses=ERR_422,
    summary="Додати фільм або серіал (разом із сезонами та серіями)",
)
async def create_title(data: TitleCreate, session: SessionDep) -> TitleDetails:
    return await titles_service.create_title(session, data)


@router.get("/titles", response_model=TitlePage, summary="Каталог з фільтрами, пошуком і пагінацією")
async def list_titles(
    session: SessionDep,
    limit: Limit = 20,
    offset: Offset = 0,
    type: Annotated[TitleType | None, Query(description="movie або series")] = None,  # noqa: A002
    genre: Annotated[str | None, Query(max_length=32)] = None,
    year: Annotated[int | None, Query(ge=1888, le=2100)] = None,
    q: Annotated[str | None, Query(min_length=2, max_length=100, description="Пошук за назвою")] = None,
    sort: TitleSort = "name",
) -> TitlePage:
    items, total = await titles_service.list_titles(
        session, title_type=type, genre=genre, year=year, query=q, sort=sort, limit=limit, offset=offset
    )
    return TitlePage(
        items=[TitleOut.model_validate(t) for t in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/titles/{title_id}",
    response_model=TitleDetails,
    responses=ERR_404,
    summary="Сторінка тайтлу: опис, сезони, рейтинг, кількість глядачів",
)
async def get_title(title_id: int, session: SessionDep) -> TitleDetails:
    return await titles_service.get_title_details(session, title_id)


@router.patch(
    "/titles/{title_id}",
    response_model=TitleDetails,
    responses={**ERR_404, **ERR_422},
    summary="Часткове оновлення тайтлу",
)
async def update_title(title_id: int, data: TitleUpdate, session: SessionDep) -> TitleDetails:
    return await titles_service.update_title(session, title_id, data)


@router.delete(
    "/titles/{title_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERR_404,
    summary="Видалити тайтл (каскадно з сезонами, серіями та відмітками)",
)
async def delete_title(title_id: int, session: SessionDep) -> Response:
    await titles_service.delete_title(session, title_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/titles/{title_id}/seasons",
    response_model=SeasonOut,
    status_code=status.HTTP_201_CREATED,
    responses={**ERR_404, **ERR_409, **ERR_422},
    summary="Додати новий сезон серіалу",
)
async def add_season(title_id: int, data: SeasonIn, session: SessionDep) -> SeasonOut:
    return await titles_service.add_season(session, title_id, data)


@router.get(
    "/titles/{title_id}/seasons/{number}",
    response_model=SeasonOut,
    responses=ERR_404,
    summary="Сезон зі списком серій",
)
async def get_season(title_id: int, number: SeasonNumber, session: SessionDep) -> SeasonOut:
    return await titles_service.get_season(session, title_id, number)


@router.post(
    "/titles/{title_id}/seasons/{number}/episodes",
    response_model=EpisodeOut,
    status_code=status.HTTP_201_CREATED,
    responses={**ERR_404, **ERR_409, **ERR_422},
    summary="Вийшла нова серія",
)
async def add_episode(
    title_id: int, number: SeasonNumber, data: EpisodeIn, session: SessionDep
) -> EpisodeOut:
    return await titles_service.add_episode(session, title_id, number, data)


@router.get("/trending", response_model=TrendingOut, summary="Популярне за останні N днів")
async def trending(
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    type: Annotated[TitleType | None, Query()] = None,  # noqa: A002
) -> TrendingOut:
    return await stats_service.get_trending(session, days, limit, type)
