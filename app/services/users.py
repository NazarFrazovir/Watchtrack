from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ConflictError, NotFoundError
from app.models import User
from app.schemas import UserCreate


async def get_user_or_404(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"User {user_id} not found")
    return user


async def create_user(session: AsyncSession, data: UserCreate) -> User:
    existing = await session.scalar(
        select(User).where(or_(User.username == data.username, User.email == data.email))
    )
    if existing is not None:
        field = "username" if existing.username == data.username else "email"
        raise ConflictError(f"User with this {field} already exists")

    user = User(**data.model_dump())
    session.add(user)
    await session.commit()
    return user
