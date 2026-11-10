from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas import ErrorResponse

SessionDep = Annotated[AsyncSession, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=100, description="Розмір сторінки")]
Offset = Annotated[int, Query(ge=0, le=10_000, description="Зсув від початку")]

ERR_404 = {404: {"model": ErrorResponse, "description": "Ресурс не знайдено"}}
ERR_409 = {409: {"model": ErrorResponse, "description": "Конфлікт (дублікат)"}}
ERR_422 = {422: {"model": ErrorResponse, "description": "Невалідні дані або порушення бізнес-правила"}}
