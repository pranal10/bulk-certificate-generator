"""ORM models: a Job holds one Certificate row per recipient."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, UTCDateTime, utcnow


class JobStatus(str, enum.Enum):
    PENDING = "pending"  # accepted, waiting for a worker
    PROCESSING = "processing"  # a worker is generating certificates
    COMPLETED = "completed"  # every recipient has a final result (generated or failed)


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


def _new_id() -> str:
    return str(uuid.uuid4())


def _enum_column(enum_cls):
    # Store the enum *values* ("pending") rather than the names ("PENDING")
    # so the DB stays readable and matches the API.
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    event_name: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        _enum_column(JobStatus), nullable=False, default=JobStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Certificate.position",
    )


class Certificate(Base):
    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    # Index of the recipient in the original request, so a client can map a
    # failure back to the row it submitted.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # Stored exactly as submitted (after trimming) even when invalid, so the
    # job report shows what was received.
    recipient_name: Mapped[str | None] = mapped_column(String(200))
    recipient_email: Mapped[str | None] = mapped_column(String(320))

    status: Mapped[CertificateStatus] = mapped_column(
        _enum_column(CertificateStatus),
        nullable=False,
        default=CertificateStatus.PENDING,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    job: Mapped[Job] = relationship(back_populates="certificates")
