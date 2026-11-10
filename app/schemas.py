"""Pydantic-схеми: валідація вхідних даних і формат відповідей API."""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import TitleType, WatchStatus

Genre = Annotated[str, Field(min_length=2, max_length=32)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------- Users ----------


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
    email: str = Field(max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str | None = Field(default=None, max_length=64)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class UserOut(ORMModel):
    id: int
    username: str
    email: str
    display_name: str | None
    created_at: datetime


# ---------- Catalog ----------


def _normalize_genres(genres: list[str] | None) -> list[str] | None:
    if genres is None:
        return None
    return sorted({g.strip().lower() for g in genres})


class EpisodeIn(BaseModel):
    number: int = Field(ge=1, le=999)
    name: str | None = Field(default=None, max_length=255)
    air_date: date | None = None
    runtime_minutes: int | None = Field(default=None, ge=1, le=600)


class SeasonIn(BaseModel):
    number: int = Field(ge=0, le=100, description="0 — спецвипуски")
    name: str | None = Field(default=None, max_length=255)
    release_date: date | None = None
    episodes: list[EpisodeIn] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def unique_episode_numbers(self) -> "SeasonIn":
        numbers = [e.number for e in self.episodes]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Episode numbers within a season must be unique")
        return self


class TitleCreate(BaseModel):
    type: TitleType
    name: str = Field(min_length=1, max_length=255)
    original_name: str | None = Field(default=None, max_length=255)
    year: int | None = Field(default=None, ge=1888, le=2100)
    description: str | None = Field(default=None, max_length=5000)
    runtime_minutes: int | None = Field(default=None, ge=1, le=1000)
    genres: list[Genre] = Field(default_factory=list, max_length=10)
    seasons: list[SeasonIn] = Field(default_factory=list, max_length=100)

    @field_validator("genres")
    @classmethod
    def normalize_genres(cls, value: list[str]) -> list[str] | None:
        return _normalize_genres(value)

    @model_validator(mode="after")
    def check_seasons(self) -> "TitleCreate":
        if self.type is TitleType.MOVIE and self.seasons:
            raise ValueError("A movie cannot have seasons")
        numbers = [s.number for s in self.seasons]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Season numbers must be unique")
        return self


class TitleUpdate(BaseModel):
    """Часткове оновлення (PATCH). Тип тайтлу і сезони тут не змінюються."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    original_name: str | None = Field(default=None, max_length=255)
    year: int | None = Field(default=None, ge=1888, le=2100)
    description: str | None = Field(default=None, max_length=5000)
    runtime_minutes: int | None = Field(default=None, ge=1, le=1000)
    genres: list[Genre] | None = Field(default=None, max_length=10)

    @field_validator("genres")
    @classmethod
    def normalize_genres(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            raise ValueError("genres cannot be null, use [] to clear")
        return _normalize_genres(value)

    @field_validator("name")
    @classmethod
    def name_not_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("name cannot be null")
        return value


class TitleOut(ORMModel):
    id: int
    type: TitleType
    name: str
    original_name: str | None
    year: int | None
    description: str | None
    runtime_minutes: int | None
    genres: list[str]


class SeasonSummary(BaseModel):
    id: int
    number: int
    name: str | None
    release_date: date | None
    episodes_count: int


class TitleDetails(TitleOut):
    seasons: list[SeasonSummary]
    avg_rating: float | None
    ratings_count: int
    viewers_count: int


class TitlePage(BaseModel):
    items: list[TitleOut]
    total: int
    limit: int
    offset: int


class EpisodeOut(ORMModel):
    id: int
    number: int
    name: str | None
    air_date: date | None
    runtime_minutes: int | None


class SeasonOut(ORMModel):
    id: int
    title_id: int
    number: int
    name: str | None
    release_date: date | None
    episodes: list[EpisodeOut]


# ---------- Tracking ----------


class UserTitleUpsert(BaseModel):
    status: WatchStatus
    rating: int | None = Field(default=None, ge=1, le=10)


class UserTitleOut(ORMModel):
    title: TitleOut
    status: WatchStatus
    rating: int | None
    updated_at: datetime


class UserTitlePage(BaseModel):
    items: list[UserTitleOut]
    total: int
    limit: int
    offset: int


class NextEpisode(BaseModel):
    episode_id: int
    season_number: int
    episode_number: int
    name: str | None


class Progress(BaseModel):
    title_id: int
    status: WatchStatus | None
    watched_episodes: int
    total_episodes: int
    percent: float
    next_episode: NextEpisode | None


class WatchResult(BaseModel):
    episodes_marked: int = Field(description="Скільки серій було відмічено цим запитом")
    progress: Progress


class UserStats(BaseModel):
    user_id: int
    titles_by_status: dict[WatchStatus, int]
    movies_completed: int
    episodes_watched: int
    minutes_watched: int
    hours_watched: float
    avg_rating_given: float | None


class TrendingItem(BaseModel):
    title: TitleOut
    viewers: int


class TrendingOut(BaseModel):
    days: int
    items: list[TrendingItem]
