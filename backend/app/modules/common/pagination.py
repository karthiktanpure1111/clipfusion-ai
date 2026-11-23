"""Shared pagination primitives: ``Page[T] = {items, total, limit, offset}``."""

from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams(BaseModel):
    limit: int = 24
    offset: int = 0


def page_params(limit: int = Query(24, ge=1, le=200), offset: int = Query(0, ge=0)) -> PageParams:
    """FastAPI dependency: ``params: PageParams = Depends(page_params)``."""
    return PageParams(limit=limit, offset=offset)


def paginate(db: Session, stmt: Select, params: PageParams) -> tuple[list, int]:
    """Execute ``stmt`` (a SELECT of ORM entities) with limit/offset; return (rows, total)."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = list(db.scalars(stmt.limit(params.limit).offset(params.offset)))
    return rows, int(total)
