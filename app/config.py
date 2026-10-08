"""Application settings.

Everything is read from environment variables (or a `.env` file in the
working directory). See `.env.example` for the full list.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLAlchemy URL. SQLite by default so the project runs with zero setup;
    # any other relational DB works by changing this (e.g. postgresql+psycopg://...).
    database_url: str = "sqlite:///./data/certgen.db"

    # Where generated PDFs are written. One sub-folder per job.
    certificates_dir: Path = Path("./data/certificates")

    # Background worker threads used to generate certificates.
    worker_threads: int = 2

    # Upper bound on recipients in a single request (protects the service
    # from accidental multi-million-row payloads).
    max_recipients_per_job: int = 1000

    # Printed on every certificate.
    organization_name: str = "Acme Learning Institute"

    # When true, jobs are processed synchronously inside the request instead
    # of being handed to the worker pool. Used by the test-suite and handy
    # for debugging; not recommended for real traffic.
    process_jobs_inline: bool = False

    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
