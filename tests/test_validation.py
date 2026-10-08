"""Recipient validation - unit level and through the API."""

import pytest

from app.schemas import RecipientIn
from app.validation import MAX_NAME_LENGTH, RecipientValidationError, validate_recipient
from tests.conftest import create_job

# --- unit ------------------------------------------------------------------


def test_valid_recipient_is_normalised():
    result = validate_recipient(RecipientIn(name="  Pranal Bhatnagar ", email=" Pranal@Example.COM "))

    assert result.name == "Pranal Bhatnagar"
    assert result.email == "pranal@example.com"


def test_accented_latin_names_are_accepted():
    result = validate_recipient(RecipientIn(name="José Müller-Ødegård", email="jose@example.com"))

    assert result.name == "José Müller-Ødegård"


@pytest.mark.parametrize(
    ("recipient", "expected_message"),
    [
        (RecipientIn(email="a@b.co"), "name is required"),
        (RecipientIn(name="   ", email="a@b.co"), "name is required"),
        (RecipientIn(name="x" * (MAX_NAME_LENGTH + 1), email="a@b.co"), "at most"),
        (RecipientIn(name="राहुल शर्मा", email="a@b.co"), "cannot print"),
        (RecipientIn(name="Line\nBreak", email="a@b.co"), "cannot print"),
        (RecipientIn(name="Pranal"), "email is required"),
        (RecipientIn(name="Pranal", email="   "), "email is required"),
        (RecipientIn(name="Pranal", email="not-an-email"), "not a valid email"),
        (RecipientIn(name="Pranal", email="missing@tld"), "not a valid email"),
        (RecipientIn(name="Pranal", email="two@@example.com"), "not a valid email"),
        (RecipientIn(name="Pranal", email="has space@example.com"), "not a valid email"),
    ],
)
def test_invalid_recipient_raises_with_reason(recipient, expected_message):
    with pytest.raises(RecipientValidationError) as excinfo:
        validate_recipient(recipient)

    assert expected_message in str(excinfo.value)


def test_all_problems_are_reported_together():
    with pytest.raises(RecipientValidationError) as excinfo:
        validate_recipient(RecipientIn(name="", email="bad"))

    message = str(excinfo.value)
    assert "name is required" in message
    assert "email is not a valid email address" in message


# --- through the API ---------------------------------------------------------


def test_invalid_recipient_is_recorded_as_failed_and_others_still_generated(client):
    recipients = [
        {"name": "Pranal Bhatnagar", "email": "pranal@example.com"},
        {"name": "", "email": "not-an-email"},
        {"name": "Priya Sharma", "email": "priya@example.com"},
    ]

    job = create_job(client, recipients=recipients)

    assert job["status"] == "completed"
    assert job["progress"] == {
        "total": 3,
        "pending": 0,
        "generated": 2,
        "failed": 1,
        "percent_complete": 100.0,
    }

    items = client.get(f"/jobs/{job['id']}/certificates").json()["items"]
    failed = items[1]
    assert failed["position"] == 1
    assert failed["status"] == "failed"
    assert failed["recipient_email"] == "not-an-email"  # what was submitted is kept
    assert "name is required" in failed["error"]
    assert "email is not a valid email address" in failed["error"]
    assert failed["download_url"] is None
    assert [c["status"] for c in items] == ["generated", "failed", "generated"]


def test_job_with_only_invalid_recipients_completes_immediately(client, settings):
    recipients = [{"name": "", "email": ""}, {"name": "Someone", "email": "nope"}]

    job = create_job(client, recipients=recipients)

    assert job["status"] == "completed"
    assert job["progress"]["generated"] == 0
    assert job["progress"]["failed"] == 2
    assert not list(settings.certificates_dir.rglob("*.pdf"))


def test_recipient_with_wrong_types_rejects_the_request(client):
    # A number instead of a string is a client bug, not bad data for one
    # person, so the whole request is rejected.
    recipients = [{"name": 12345, "email": "a@b.co"}]

    response = client.post("/jobs", json={"event_name": "Event", "recipients": recipients})

    assert response.status_code == 422
