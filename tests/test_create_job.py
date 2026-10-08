"""Creating a generation job."""

from datetime import date

from tests.conftest import RECIPIENTS, create_job, make_payload


def test_create_job_returns_202_with_job_summary(client):
    response = client.post("/jobs", json=make_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["id"]
    assert body["event_name"] == "Python Bootcamp 2026"
    assert body["issue_date"] == "2026-10-08"
    assert body["status"] in {"pending", "processing", "completed"}
    assert body["progress"]["total"] == len(RECIPIENTS)
    assert body["created_at"].endswith("Z")  # timestamps are explicit UTC


def test_job_is_retrievable_after_creation(client):
    job = create_job(client)

    response = client.get(f"/jobs/{job['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == job["id"]


def test_issue_date_defaults_to_today(client):
    payload = make_payload()
    del payload["issue_date"]

    response = client.post("/jobs", json=payload)

    assert response.status_code == 202
    assert response.json()["issue_date"] == date.today().isoformat()


def test_event_name_is_trimmed(client):
    job = create_job(client, event_name="   Data Science Workshop  ")

    assert job["event_name"] == "Data Science Workshop"


def test_one_request_creates_one_certificate_per_recipient(client):
    job = create_job(client)

    response = client.get(f"/jobs/{job['id']}/certificates")

    items = response.json()["items"]
    assert [c["position"] for c in items] == [0, 1, 2]
    assert [c["recipient_email"] for c in items] == [r["email"] for r in RECIPIENTS]


def test_extra_recipient_fields_are_ignored(client):
    recipients = [{"name": "Pranal Bhatnagar", "email": "pranal@example.com", "phone": "999", "course": "x"}]

    job = create_job(client, recipients=recipients)

    assert job["progress"]["total"] == 1
    assert job["progress"]["failed"] == 0


def test_list_jobs_newest_first_with_pagination(client):
    first = create_job(client, event_name="First")
    second = create_job(client, event_name="Second")

    response = client.get("/jobs", params={"limit": 1})
    body = response.json()
    assert body["total"] == 2
    assert [j["id"] for j in body["items"]] == [second["id"]]

    response = client.get("/jobs", params={"limit": 1, "offset": 1})
    assert [j["id"] for j in response.json()["items"]] == [first["id"]]


# --- request-level validation (whole request rejected, nothing stored) -----


def test_empty_recipient_list_is_rejected(client):
    response = client.post("/jobs", json=make_payload(recipients=[]))

    assert response.status_code == 422
    assert client.get("/jobs").json()["total"] == 0


def test_missing_event_name_is_rejected(client):
    payload = make_payload()
    del payload["event_name"]

    assert client.post("/jobs", json=payload).status_code == 422


def test_blank_event_name_is_rejected(client):
    assert client.post("/jobs", json=make_payload(event_name="   ")).status_code == 422


def test_event_name_with_unprintable_characters_is_rejected(client):
    response = client.post("/jobs", json=make_payload(event_name="Python बूटकैंप"))

    assert response.status_code == 422
    assert "cannot print" in response.text


def test_recipients_must_be_a_list_of_objects(client):
    assert client.post("/jobs", json=make_payload(recipients="pranal@example.com")).status_code == 422
    assert client.post("/jobs", json=make_payload(recipients=["pranal@example.com"])).status_code == 422


def test_too_many_recipients_is_rejected(client, settings):
    recipients = [
        {"name": f"Person {i}", "email": f"p{i}@example.com"}
        for i in range(settings.max_recipients_per_job + 1)
    ]

    response = client.post("/jobs", json=make_payload(recipients=recipients))

    assert response.status_code == 422
    assert str(settings.max_recipients_per_job) in response.json()["detail"]
    assert client.get("/jobs").json()["total"] == 0


def test_invalid_json_is_rejected(client):
    response = client.post("/jobs", content="not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 422
