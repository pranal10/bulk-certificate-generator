"""Pydantic request / response models."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.certificates import unprintable_characters
from app.models import CertificateStatus, JobStatus

# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


class RecipientIn(BaseModel):
    """One recipient as submitted by the client.

    Both fields are optional *at the schema level* on purpose: a missing or
    blank name/email is a per-recipient problem and is reported on that
    recipient (status "failed" + reason) instead of rejecting the whole bulk
    request. Only the *shape* of the payload is enforced here.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    email: str | None = None


class JobCreate(BaseModel):
    event_name: str = Field(min_length=1, max_length=200, examples=["Python Bootcamp 2026"])
    issue_date: date | None = Field(
        default=None, description="Date printed on the certificates. Defaults to today."
    )
    recipients: list[RecipientIn] = Field(min_length=1)

    @field_validator("event_name")
    @classmethod
    def _event_name_printable(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("event_name must not be blank")
        bad = unprintable_characters(value)
        if bad:
            raise ValueError(f"event_name contains characters the certificate template cannot print: {bad!r}")
        return value


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


class JobProgress(BaseModel):
    total: int
    pending: int
    generated: int
    failed: int
    percent_complete: float = Field(description="(generated + failed) / total * 100")


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_name: str
    issue_date: date
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    progress: JobProgress


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: CertificateStatus
    error: str | None = None
    generated_at: datetime | None = None
    download_url: str | None = Field(
        default=None, description="Present once the certificate has been generated."
    )


class CertificateListOut(BaseModel):
    items: list[CertificateOut]
    total: int
    limit: int
    offset: int


class ErrorOut(BaseModel):
    detail: str
