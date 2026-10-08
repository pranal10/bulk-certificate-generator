"""Job processing: turns pending Certificate rows into PDF files.

Design in one paragraph: a job is accepted and stored first (HTTP 202), then
its id is handed to a queue. The default queue is a small in-process thread
pool; `InlineJobQueue` runs the same code synchronously (tests / debugging).
Each certificate is generated and committed on its own, inside its own
try/except, so one bad recipient only fails that one row and the client can
watch progress tick up while the job is still running.
"""

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.certificates import CertificateData, render_certificate_pdf
from app.config import Settings
from app.database import utcnow
from app.models import Certificate, CertificateStatus, Job, JobStatus

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 500


class JobProcessor:
    def __init__(self, session_factory: sessionmaker, settings: Settings):
        self._session_factory = session_factory
        self._settings = settings

    # -- public -------------------------------------------------------------

    def process_job(self, job_id: str) -> None:
        """Generate every still-pending certificate of a job, then mark it completed.

        Safe to call more than once for the same job: already generated or
        failed certificates are skipped, which is what makes restart recovery
        (see `recover_unfinished_jobs`) trivial.
        """
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                logger.warning("process_job: job %s not found", job_id)
                return
            if job.status == JobStatus.COMPLETED:
                return

            job.status = JobStatus.PROCESSING
            session.commit()

            pending_ids = session.scalars(
                select(Certificate.id)
                .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
                .order_by(Certificate.position)
            ).all()

        logger.info("job %s: generating %d certificate(s)", job_id, len(pending_ids))
        for certificate_id in pending_ids:
            self.generate_certificate(certificate_id)

        with self._session_factory() as session:
            job = session.get(Job, job_id)
            job.status = JobStatus.COMPLETED
            session.commit()
        logger.info("job %s: completed", job_id)

    def generate_certificate(self, certificate_id: str) -> None:
        """Render a single certificate and record the outcome (generated / failed)."""
        with self._session_factory() as session:
            certificate = session.get(Certificate, certificate_id)
            if certificate is None or certificate.status != CertificateStatus.PENDING:
                return
            job = certificate.job

            try:
                output_path = self.output_path_for(certificate)
                data = CertificateData(
                    certificate_id=certificate.id,
                    recipient_name=certificate.recipient_name or "",
                    event_name=job.event_name,
                    issue_date=job.issue_date,
                    organization_name=self._settings.organization_name,
                )
                render_certificate_pdf(data, output_path)
            except Exception as exc:  # any failure must be isolated to this row
                logger.exception("certificate %s (job %s) failed", certificate.id, job.id)
                certificate.status = CertificateStatus.FAILED
                certificate.error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            else:
                certificate.status = CertificateStatus.GENERATED
                certificate.file_path = str(output_path)
                certificate.generated_at = utcnow()

            session.commit()

    def output_path_for(self, certificate: Certificate) -> Path:
        # One folder per job, file named by certificate id: no collisions and
        # nothing user-controlled ends up in the path.
        return Path(self._settings.certificates_dir) / certificate.job_id / f"{certificate.id}.pdf"


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------


class JobQueue(Protocol):
    def enqueue(self, job_id: str) -> None: ...

    def shutdown(self) -> None: ...


class ThreadPoolJobQueue:
    """Default queue: a bounded pool of worker threads inside the API process.

    No broker or extra service to run, and the API stays responsive because
    generation never happens on the request path. The trade-off is that jobs
    live in this process only - see README for when to move to Celery/RQ.
    """

    def __init__(self, processor: JobProcessor, max_workers: int):
        self._processor = processor
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="certgen-worker")
        self._futures: list[Future] = []

    def enqueue(self, job_id: str) -> None:
        future = self._executor.submit(self._run, job_id)
        self._futures.append(future)
        self._futures = [f for f in self._futures if not f.done()]

    def _run(self, job_id: str) -> None:
        try:
            self._processor.process_job(job_id)
        except Exception:
            # A bug here would otherwise vanish inside the thread pool.
            logger.exception("unhandled error while processing job %s", job_id)

    def wait_idle(self) -> None:
        """Block until every submitted job has finished (used by tests)."""
        for future in list(self._futures):
            future.result()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


class InlineJobQueue:
    """Runs the job immediately on the calling thread (synchronous mode)."""

    def __init__(self, processor: JobProcessor):
        self._processor = processor

    def enqueue(self, job_id: str) -> None:
        self._processor.process_job(job_id)

    def wait_idle(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def recover_unfinished_jobs(session_factory: sessionmaker, queue: JobQueue) -> int:
    """Re-queue jobs that were pending/processing when the process last stopped.

    Called on startup. Because `process_job` only touches pending
    certificates, re-running a half-finished job just picks up where it left off.
    """
    with session_factory() as session:
        job_ids = session.scalars(
            select(Job.id)
            .where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
            .order_by(Job.created_at)
        ).all()

    for job_id in job_ids:
        logger.info("re-queuing unfinished job %s", job_id)
        queue.enqueue(job_id)
    return len(job_ids)
