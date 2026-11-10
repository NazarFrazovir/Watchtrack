from fastapi import APIRouter, status

from app.routers.common import ERR_404, ERR_409, ERR_422, SessionDep
from app.schemas import UserCreate, UserOut, UserStats
from app.services import stats as stats_service
from app.services import users as users_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    responses={**ERR_409, **ERR_422},
    summary="Реєстрація користувача",
)
async def create_user(data: UserCreate, session: SessionDep) -> UserOut:
    return await users_service.create_user(session, data)


@router.get("/{user_id}", response_model=UserOut, responses=ERR_404, summary="Профіль користувача")
async def get_user(user_id: int, session: SessionDep) -> UserOut:
    return await users_service.get_user_or_404(session, user_id)


@router.get(
    "/{user_id}/stats",
    response_model=UserStats,
    responses=ERR_404,
    summary="Статистика перегляду: години, серії, статуси",
)
async def get_user_stats(user_id: int, session: SessionDep) -> UserStats:
    return await stats_service.get_user_stats(session, user_id)
