from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

from .enums import NoticeLevel

T = TypeVar("T")


class ApiModel(BaseModel):
    """Base for every request/response schema. DB rows carry extra columns we
    don't expose, so unknown keys are dropped rather than rejected."""

    model_config = ConfigDict(extra="ignore")


class InputModel(BaseModel):
    """Base for request bodies: unknown fields are a client error."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int


class Notice(ApiModel):
    level: NoticeLevel
    message: str
