"""/jobs — persistent background jobs of the current user."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, DbSession, get_owned
from app.models import ProcessingJob
from app.modules.common.pagination import Page, PageParams, page_params
from app.modules.jobs import service
from app.modules.jobs.schemas import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=Page[JobOut])
def list_jobs(user: CurrentUser, db: DbSession, params: PageParams = Depends(page_params),
              status: str | None = None, job_type: str | None = None,
              video_source_id: uuid.UUID | None = None, clip_id: uuid.UUID | None = None,
              active: bool | None = Query(None)) -> Page[JobOut]:
    """List jobs (newest first). ``active=true`` returns queued/running/retrying jobs only."""
    rows, total = service.list_jobs(db, user, params, status=status, job_type=job_type,
                                    video_source_id=video_source_id, clip_id=clip_id, active=active)
    return Page[JobOut](items=service.jobs_out(db, rows), total=total, limit=params.limit, offset=params.offset)


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> JobOut:
    """One job (404 for other users' jobs)."""
    return service.job_out(db, get_owned(db, ProcessingJob, job_id, user, name="Job"))


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> JobOut:
    """Cancel a queued/running/retrying job."""
    job = service.cancel_job(db, get_owned(db, ProcessingJob, job_id, user, name="Job"))
    return service.job_out(db, job)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: uuid.UUID, user: CurrentUser, db: DbSession) -> JobOut:
    """Retry a failed or cancelled job."""
    job = service.retry_job(db, get_owned(db, ProcessingJob, job_id, user, name="Job"))
    return service.job_out(db, job)
