"""HTTP routes."""

import re
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.dependencies import get_db, get_queue, get_settings
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.processing import JobQueue
from app.schemas import (
    CertificateListOut,
    CertificateOut,
    ErrorOut,
    JobCreate,
    JobListOut,
    JobOut,
    JobProgress,
)
from app.validation import RecipientValidationError, validate_recipient

router = APIRouter()

NOT_FOUND = {404: {"model": ErrorOut}}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _progress_by_job(db: Session, job_ids: list[str]) -> dict[str, JobProgress]:
    """One grouped query for the progress counters of many jobs."""
    counts: dict[str, dict[CertificateStatus, int]] = {job_id: {} for job_id in job_ids}
    if job_ids:
        rows = db.execute(
            select(Certificate.job_id, Certificate.status, func.count())
            .where(Certificate.job_id.in_(job_ids))
            .group_by(Certificate.job_id, Certificate.status)
        ).all()
        for job_id, cert_status, n in rows:
            counts[job_id][cert_status] = n

    result = {}
    for job_id, by_status in counts.items():
        total = sum(by_status.values())
        generated = by_status.get(CertificateStatus.GENERATED, 0)
        failed = by_status.get(CertificateStatus.FAILED, 0)
        pending = by_status.get(CertificateStatus.PENDING, 0)
        done = generated + failed
        result[job_id] = JobProgress(
            total=total,
            pending=pending,
            generated=generated,
            failed=failed,
            percent_complete=round(done / total * 100, 1) if total else 100.0,
        )
    return result


def _job_out(job: Job, progress: JobProgress) -> JobOut:
    return JobOut(
        id=job.id,
        event_name=job.event_name,
        issue_date=job.issue_date,
        status=job.status,
        created_at=job.created_at,
        updated_at=job.updated_at,
        progress=progress,
    )


def _single_job_out(db: Session, job: Job) -> JobOut:
    return _job_out(job, _progress_by_job(db, [job.id])[job.id])


def _certificate_out(certificate: Certificate) -> CertificateOut:
    out = CertificateOut.model_validate(certificate)
    if certificate.status == CertificateStatus.GENERATED:
        out.download_url = f"/certificates/{certificate.id}/download"
    return out


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _get_certificate_or_404(db: Session, certificate_id: str) -> Certificate:
    certificate = db.get(Certificate, certificate_id)
    if certificate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Certificate not found")
    return certificate


def _safe_filename_part(value: str | None, fallback: str = "certificate") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value or "").strip("-").lower()
    return cleaned[:40] or fallback


def _certificate_file(certificate: Certificate) -> Path:
    """Path of a generated PDF, or an HTTP error explaining why it's not available."""
    if certificate.status == CertificateStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Certificate has not been generated yet")
    if certificate.status == CertificateStatus.FAILED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Certificate generation failed: {certificate.error}",
        )
    path = Path(certificate.file_path or "")
    if not certificate.file_path or not path.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Certificate file is missing from storage",
        )
    return path


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


@router.post(
    "/jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a bulk certificate generation job",
    responses={422: {"model": ErrorOut}},
)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_queue),
) -> JobOut:
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise HTTPException(
            422,
            detail=(
                f"Too many recipients: {len(payload.recipients)} "
                f"(maximum is {settings.max_recipients_per_job} per job)"
            ),
        )

    job = Job(event_name=payload.event_name.strip(), issue_date=payload.issue_date or date.today())

    # Validate every recipient up-front. Invalid ones are stored as failed
    # right away so the client sees them in the 202 response; valid ones are
    # stored as pending and picked up by the worker.
    has_pending = False
    for position, raw in enumerate(payload.recipients):
        certificate = Certificate(
            position=position,
            recipient_name=(raw.name or "").strip() or None,
            recipient_email=(raw.email or "").strip() or None,
        )
        try:
            valid = validate_recipient(raw)
        except RecipientValidationError as exc:
            certificate.status = CertificateStatus.FAILED
            certificate.error = str(exc)
        else:
            certificate.recipient_name = valid.name
            certificate.recipient_email = valid.email
            has_pending = True
        job.certificates.append(certificate)

    if not has_pending:
        # Nothing to generate - don't bother the worker.
        job.status = JobStatus.COMPLETED

    db.add(job)
    db.commit()  # must be visible to the worker thread before we enqueue
    db.refresh(job)

    if has_pending:
        queue.enqueue(job.id)
        db.refresh(job)  # in inline mode the job is already finished at this point

    return _single_job_out(db, job)


@router.get("/jobs", response_model=JobListOut, summary="List jobs (newest first)")
def list_jobs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> JobListOut:
    total = db.scalar(select(func.count()).select_from(Job)) or 0
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)).all()
    progress = _progress_by_job(db, [job.id for job in jobs])
    items = [_job_out(job, progress[job.id]) for job in jobs]
    return JobListOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobOut, responses=NOT_FOUND, summary="Job status and progress")
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobOut:
    job = _get_job_or_404(db, job_id)
    return _single_job_out(db, job)


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateListOut,
    responses=NOT_FOUND,
    summary="Per-recipient results of a job",
)
def list_job_certificates(
    job_id: str,
    status_filter: CertificateStatus | None = Query(
        None, alias="status", description="Only return certificates with this status"
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> CertificateListOut:
    _get_job_or_404(db, job_id)

    conditions = [Certificate.job_id == job_id]
    if status_filter is not None:
        conditions.append(Certificate.status == status_filter)

    total = db.scalar(select(func.count()).select_from(Certificate).where(*conditions)) or 0
    certificates = db.scalars(
        select(Certificate).where(*conditions).order_by(Certificate.position).limit(limit).offset(offset)
    ).all()
    return CertificateListOut(
        items=[_certificate_out(c) for c in certificates],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/jobs/{job_id}/download",
    responses={**NOT_FOUND, 409: {"model": ErrorOut}},
    summary="Download every generated certificate of a job as a ZIP",
    response_class=StreamingResponse,
)
def download_job_certificates(job_id: str, db: Session = Depends(get_db)) -> StreamingResponse:
    job = _get_job_or_404(db, job_id)
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Job is still {job.status.value}; try again once it is completed",
        )

    generated = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.position)
    ).all()
    if not generated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This job has no generated certificates")

    # Spooled to disk beyond a few MB so a big job doesn't balloon memory.
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for certificate in generated:
            path = Path(certificate.file_path or "")
            if not path.is_file():
                continue
            arcname = f"{certificate.position + 1:04d}-{_safe_filename_part(certificate.recipient_name)}.pdf"
            archive.write(path, arcname=arcname)
    spool.seek(0)

    def _stream() -> Iterator[bytes]:
        with spool:
            while chunk := spool.read(64 * 1024):
                yield chunk

    filename = f"certificates-{_safe_filename_part(job.event_name)}-{job.id[:8]}.zip"
    return StreamingResponse(
        _stream(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# certificates
# ---------------------------------------------------------------------------


@router.get(
    "/certificates/{certificate_id}",
    response_model=CertificateOut,
    responses=NOT_FOUND,
    summary="Status of a single certificate",
)
def get_certificate(certificate_id: str, db: Session = Depends(get_db)) -> CertificateOut:
    return _certificate_out(_get_certificate_or_404(db, certificate_id))


@router.get(
    "/certificates/{certificate_id}/download",
    responses={**NOT_FOUND, 409: {"model": ErrorOut}},
    summary="Download a generated certificate (PDF)",
    response_class=FileResponse,
)
def download_certificate(certificate_id: str, db: Session = Depends(get_db)) -> FileResponse:
    certificate = _get_certificate_or_404(db, certificate_id)
    path = _certificate_file(certificate)
    filename = f"certificate-{_safe_filename_part(certificate.recipient_name)}-{certificate.id[:8]}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=filename)
