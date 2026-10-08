# Bulk Certificate Generator

A backend API that takes a list of recipients in one request, generates a PDF certificate for each of them from a fixed template, tracks progress per recipient, and lets the client download the results individually or as a ZIP.

Built with **Python 3.10+ · FastAPI · SQLAlchemy 2 (SQLite by default) · reportlab · pytest**.

**Live demo:** <bulk-certificate-generator-9vhr.onrender.com> — paste a few recipients, hit *Generate certificates*, and watch the job run. (Free hosting: the first request after a quiet period takes up to a minute while the service wakes up.) The same page is served at `/` when you run the project locally; the REST API is documented at `/docs`.

```
POST /jobs ──▶ 202 { job_id, status: "pending" }
                      │
                      ▼  worker thread pool renders one PDF per recipient
GET /jobs/{job_id} ──▶ { status: "processing", progress: { generated: 412, failed: 3, pending: 585 } }
                      │
                      ▼
GET /jobs/{job_id}/certificates?status=failed   ──▶ who failed and why
GET /certificates/{certificate_id}/download     ──▶ one PDF
GET /jobs/{job_id}/download                     ──▶ all PDFs as a ZIP
```

## Contents

- [Setup](#setup)
- [Running the application](#running-the-application)
- [Demo UI](#demo-ui)
- [Deploying](#deploying)
- [Running the tests](#running-the-tests)
- [API: submitting a job](#api-submitting-a-job)
- [API: tracking progress](#api-tracking-progress)
- [API: retrieving certificates](#api-retrieving-certificates)
- [Configuration](#configuration)
- [Design decisions](#design-decisions)
- [Project layout](#project-layout)
- [Limitations and next steps](#limitations-and-next-steps)

## Setup

Requires Python 3.10 or newer. No database server or message broker is needed.

```bash
git clone <this repo>
cd bulk-certificate-generator

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt       # runtime only
pip install -r requirements-dev.txt   # runtime + test dependencies
```

Configuration is optional. Every setting has a default (SQLite file under `./data/`, PDFs under `./data/certificates/`). To change something, copy `.env.example` to `.env` and edit it, or export the variables. See [Configuration](#configuration).

## Running the application

```bash
uvicorn app.main:app --reload
```

The API is now on <http://127.0.0.1:8000>. Interactive docs (Swagger UI) are at <http://127.0.0.1:8000/docs>, and `GET /health` returns `{"status": "ok"}`.

On startup the app creates the SQLite schema if it is missing and re-queues any job that was still pending/processing when the server last stopped.

With Docker:

```bash
docker build -t certgen .
docker run -p 8000:8000 -v "$PWD/data:/srv/data" certgen
```

## Demo UI

Open <http://127.0.0.1:8000/> after starting the server. It is a single static page (`app/static/index.html`, no build step, no framework) that talks to the same API documented below:

- paste recipients as `name, email` lines (a sample with one deliberately broken row is pre-filled; *Use 300 sample recipients* fills in a bulk list),
- submit, then watch the job go `pending → processing → completed` with the progress bar and per-recipient results updating every half second,
- download any generated PDF, or the whole job as a ZIP,
- expand *Raw API response* to see the exact JSON the status endpoint returns, and re-open earlier jobs from the list under the form.

The page exists so the behaviour is easy to show; everything it does is a plain `fetch` against `/jobs` and `/certificates`.

## Deploying

The repo includes a [`render.yaml`](render.yaml) blueprint, so it deploys to [Render](https://render.com) in one click:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/pranal10/bulk-certificate-generator)

Render installs `requirements.txt`, starts `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, and checks `/health`. Two things to know about the free plan: the service sleeps after ~15 minutes without traffic (the next request takes up to a minute), and the disk is ephemeral, so the SQLite file and generated PDFs are reset on each deploy or restart. For anything beyond a demo, set `DATABASE_URL` to a managed Postgres and attach a persistent disk (or object storage) for `CERTIFICATES_DIR`.

Any platform that runs a container works too: the `Dockerfile` listens on `$PORT` when it is set (Railway, Fly.io, Cloud Run) and on 8000 otherwise.

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite (73 tests, ~3 s) runs against a throw-away SQLite file and temp folder per test, with jobs processed synchronously so results are deterministic. One test exercises the real thread-pool queue. Coverage by file:

| File | What it covers |
|---|---|
| `tests/test_create_job.py` | creating a job, defaults, request-level validation (empty list, bad shapes, too many recipients, malformed JSON), listing jobs |
| `tests/test_validation.py` | per-recipient validation rules and how invalid recipients are reported without blocking valid ones |
| `tests/test_generation.py` | the PDF template: contents, page size, long names, atomic file writes, unprintable text |
| `tests/test_job_status.py` | pending → processing → completed, per-certificate progress, idempotent re-processing, background queue, restart recovery |
| `tests/test_failure_handling.py` | a render failure for one recipient leaves the others untouched, error reporting, crash mid-write, ZIP skips failures |
| `tests/test_retrieval.py` | listing/filtering/paginating certificates, PDF download, ZIP download, 404/409 cases |
| `tests/test_demo_page.py` | the demo page is served at `/` and stays out of the OpenAPI schema |

## API: submitting a job

`POST /jobs` — one request for any number of recipients (up to `MAX_RECIPIENTS_PER_JOB`, default 1000).

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -d @examples/sample_request.json
```

Request body (`examples/sample_request.json`):

```json
{
  "event_name": "Python Bootcamp 2026",
  "issue_date": "2026-10-08",
  "recipients": [
    { "name": "Pranal Bhatnagar",   "email": "pranal@example.com" },
    { "name": "Priya Sharma", "email": "priya@example.com" },
    { "name": "Rohan Mehta",  "email": "rohan@example.com" },
    { "name": "",             "email": "not-an-email" }
  ]
}
```

- `event_name` — required, printed on the certificate.
- `issue_date` — optional `YYYY-MM-DD`, defaults to today.
- `recipients` — required, at least one. Each needs a `name` and an `email`; unknown fields are ignored.

Response — `202 Accepted`, returned immediately, before any PDF is rendered:

```json
{
  "id": "03e812b3-468a-44a9-ba72-6fd3dd6f6edc",
  "event_name": "Python Bootcamp 2026",
  "issue_date": "2026-10-08",
  "status": "pending",
  "created_at": "2026-10-08T18:34:26.425976Z",
  "updated_at": "2026-10-08T18:34:26.425983Z",
  "progress": { "total": 4, "pending": 3, "generated": 0, "failed": 1, "percent_complete": 25.0 }
}
```

Notice `failed: 1` already: the fourth recipient had no name and a bad email, so it was marked failed at submission time while the other three were queued. Nothing is rejected wholesale because of one bad row.

The whole request *is* rejected (`422`, nothing stored) only when it is malformed as a request rather than as data: missing `event_name`, `recipients` not a list of objects, an empty list, wrong types (`"name": 123`), or more recipients than the configured limit.

## API: tracking progress

`GET /jobs/{job_id}`

```json
{
  "id": "03e812b3-468a-44a9-ba72-6fd3dd6f6edc",
  "status": "completed",
  "progress": { "total": 4, "pending": 0, "generated": 3, "failed": 1, "percent_complete": 100.0 },
  "...": "..."
}
```

`status` is one of `pending` (accepted, not started), `processing` (a worker is on it) or `completed` (every recipient has a final result). A job with failures still ends as `completed`; the `progress` counters and the per-recipient list say what happened. Poll this endpoint until `status == "completed"` — the counters update after every single certificate, so a 1000-recipient job visibly ticks up.

`GET /jobs/{job_id}/certificates` lists every recipient with its individual outcome. Filter with `?status=generated|failed|pending`, page with `?limit=&offset=` (`position` is the index in the original request):

```json
{
  "items": [
    {
      "id": "1365d35f-4989-4135-9134-eb54cb72f8f5",
      "job_id": "03e812b3-468a-44a9-ba72-6fd3dd6f6edc",
      "position": 0,
      "recipient_name": "Pranal Bhatnagar",
      "recipient_email": "pranal@example.com",
      "status": "generated",
      "error": null,
      "generated_at": "2026-10-08T18:34:26.448990Z",
      "download_url": "/certificates/1365d35f-4989-4135-9134-eb54cb72f8f5/download"
    },
    {
      "id": "8f6c9c93-e01f-45a4-b96a-a716cc81d1c5",
      "job_id": "03e812b3-468a-44a9-ba72-6fd3dd6f6edc",
      "position": 3,
      "recipient_name": null,
      "recipient_email": "not-an-email",
      "status": "failed",
      "error": "name is required; email is not a valid email address",
      "generated_at": null,
      "download_url": null
    }
  ],
  "total": 4, "limit": 100, "offset": 0
}
```

`GET /jobs?limit=&offset=` lists jobs, newest first. `GET /certificates/{certificate_id}` returns one row from the list above.

## API: retrieving certificates

One certificate as a PDF:

```bash
curl -OJ http://127.0.0.1:8000/certificates/1365d35f-4989-4135-9134-eb54cb72f8f5/download
# -> certificate-pranal-bhatnagar-1365d35f.pdf
```

Every generated certificate of a job as a ZIP (available once the job is `completed`; failed recipients are simply absent from the archive):

```bash
curl -OJ http://127.0.0.1:8000/jobs/03e812b3-468a-44a9-ba72-6fd3dd6f6edc/download
# -> certificates-python-bootcamp-2026-03e812b3.zip
#    0001-pranal-bhatnagar.pdf, 0002-priya-sharma.pdf, 0003-rohan-mehta.pdf
```

Error responses are always `{"detail": "..."}`:

| Status | When |
|---|---|
| `404` | unknown job / certificate, or the PDF was deleted from storage |
| `409` | downloading a certificate that is still pending or has failed (the detail carries the failure reason); ZIP of a job that is not completed yet |
| `422` | malformed request, see above |

## Configuration

All settings are environment variables (a `.env` file in the working directory is read too).

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certgen.db` | Any SQLAlchemy URL. For Postgres: `postgresql+psycopg://user:pass@host/db` plus `pip install psycopg`. |
| `CERTIFICATES_DIR` | `./data/certificates` | Where PDFs are written, one sub-folder per job. |
| `WORKER_THREADS` | `2` | Threads rendering certificates in the background. |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Requests above this are rejected with 422. |
| `ORGANIZATION_NAME` | `Acme Learning Institute` | Printed on every certificate. |
| `PROCESS_JOBS_INLINE` | `false` | `true` renders inside the request (synchronous). Used by the tests; useful when debugging. |
| `LOG_LEVEL` | `INFO` | Python log level for the app's own loggers. |

## Design decisions

### Processing model: accept first, render in the background

A 1000-recipient job takes a few seconds to render (about 4 s in my runs, ~1.5 MB of PDFs), and real jobs could be larger. Holding the HTTP connection open for that long means timeouts at the client, at proxies, and a request that can't report partial progress. So `POST /jobs` stores the job and returns `202` right away, and the rendering happens in a small **thread pool inside the API process** (`ThreadPoolJobQueue` in `app/processing.py`).

I considered three options:

| | Synchronous | In-process thread pool (chosen) | Celery / RQ + Redis |
|---|---|---|---|
| Moving parts | none | none | broker + separate worker processes |
| API stays responsive | no, blocks for the whole job | yes | yes |
| Progress visible while running | no | yes | yes |
| Survives a restart | n/a | yes, via startup recovery | yes, via the broker |
| Scales across processes/machines | no | no (one process) | yes |

For this assignment the thread pool is the right trade-off: it gives the real asynchronous behaviour the problem asks for with `pip install` as the only setup. The queue is behind a two-method interface (`enqueue`, `shutdown`), so swapping in Celery means replacing one class and leaving the API and the processor untouched. A synchronous mode (`PROCESS_JOBS_INLINE=true`) uses the same code path, which is what the tests run.

The obvious weakness of in-process workers — jobs die with the process — is handled by making `process_job` **idempotent**: it only ever touches certificates that are still `pending`. On startup the app re-queues every job that is not `completed`, so a job interrupted half-way just continues; already generated certificates are not redone.

### Validation in two layers

1. **Request shape** (Pydantic, `app/schemas.py`): is this a well-formed bulk request? Missing `event_name`, `recipients` that isn't a list of objects, wrong types, empty list, over the size limit → `422` and nothing is stored. These are client bugs, not data problems.
2. **Recipient content** (`app/validation.py`): does *this* person have a usable name and email? This runs per recipient at submission time. Failures are recorded on that recipient (`status: failed`, `error: "..."`) and the rest of the job goes ahead.

Rules are deliberately simple and spelled out in error messages: name required, ≤ 100 characters, printable by the template; email required and of the form `something@something.tld` (a format check, not a deliverability check). Input is trimmed and emails are lower-cased; the original submitted value is kept on the row so a client can recognise its own data in the report.

Validating at submission rather than in the worker means the `202` response already tells the client how many rows were bad, and the worker only ever sees rows that can be rendered.

### One failure never blocks the others

Each certificate is rendered inside its own `try/except` and committed in its own transaction (`JobProcessor.generate_certificate`). A crash while rendering recipient 7 marks row 7 as `failed` with `ExceptionType: message` and moves on to row 8. The job still finishes as `completed`, and `GET /jobs/{id}/certificates?status=failed` lists exactly who needs attention. The tests simulate this with a renderer that blows up for one specific name, and with a disk error in the middle of writing a file.

PDFs are written to a `.part` file and renamed into place only after `save()` succeeds, so there is never a half-written file that looks like a finished certificate.

### Status model

Two levels, both stored in the DB:

- **Job**: `pending → processing → completed`. "Completed" means every recipient has a final outcome, not that everything succeeded — the counters say that.
- **Certificate**: `pending`, `generated` or `failed`, with `error` on failures and `file_path`/`generated_at` on successes.

Progress counters are computed with one `GROUP BY` query rather than stored on the job, so they can never drift from the rows.

### The template

A single, code-drawn design in `app/certificates.py` (reportlab, landscape A4): organisation name, title, recipient name, event name, issue date, a signature line and the certificate id. Long names and event titles shrink to fit instead of overflowing the border. Drawing the template in code keeps the project free of binary assets and makes the "recipient-specific information" explicit in one dataclass (`CertificateData`).

reportlab's built-in fonts only cover the Latin (cp1252) character set. Rather than silently printing missing-glyph boxes, names or event titles with other scripts are rejected up-front with a clear message (`"name contains characters the certificate template cannot print"`). Supporting them would mean bundling a Unicode TTF and registering it — a contained change in that one module.

### Storage

PDFs live on disk under `CERTIFICATES_DIR/<job_id>/<certificate_id>.pdf`, with the path stored on the certificate row. File names come from server-generated UUIDs, never from user input, so there are no collisions and no path tricks. Downloads stream the file with a friendly `Content-Disposition` name; the ZIP is built into a spooled temp file so a large job doesn't balloon memory.

### Database and app structure

SQLAlchemy 2 with SQLite as the default so the project runs with zero setup; `DATABASE_URL` switches to Postgres/MySQL without code changes. For SQLite the engine enables WAL mode and a busy timeout so the API can read job status while a worker thread writes. The schema is created with `create_all` on startup — small project, one version; Alembic would be the next step if the schema starts evolving.

`create_app(settings)` builds the engine, session factory, processor and queue and hangs them on `app.state`; routes get them through FastAPI dependencies. Nothing is a module-level singleton (the `app` in `app/main.py` is just the default instance for uvicorn), so every test builds its own app against a temp directory.

## Project layout

```
app/
  main.py          app factory, lifespan (schema, startup recovery), /health
  api.py           all HTTP routes
  schemas.py       Pydantic request/response models
  validation.py    per-recipient validation rules
  models.py        SQLAlchemy models: Job, Certificate
  processing.py    JobProcessor (renders + records outcomes), queues, startup recovery
  certificates.py  the PDF template (reportlab)
  static/index.html  the demo UI served at /
  database.py      engine/session helpers, UTC datetime type
  config.py        settings from environment
  dependencies.py  FastAPI dependencies (db session, settings, queue)
tests/             pytest suite (see "Running the tests")
examples/          sample_request.json
render.yaml        one-click Render deployment (see "Deploying")
```

## Limitations and next steps

- **Single process.** Running uvicorn with `--workers N` would give each process its own queue and its own startup recovery. The step up is a broker-backed queue (Celery/RQ); the `JobQueue` interface is where it plugs in.
- **No authentication.** Anyone who can reach the API can submit jobs and download certificates. An API key per client and ownership checks on jobs would come first.
- **Local disk storage.** Fine for one server; for several, point `file_path` at object storage (S3-compatible) and serve pre-signed URLs.
- **No retention policy.** Old jobs and PDFs are kept forever; a scheduled cleanup or a `DELETE /jobs/{id}` is straightforward to add.
- **Latin-script names only** with the built-in fonts, as described above.
- **Nice-to-haves** that were left out on purpose: a completion webhook/email, per-job retry of failed rows, a verification endpoint for the certificate id printed on the PDF.
