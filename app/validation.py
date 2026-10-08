"""Per-recipient validation.

The request schema only checks the payload *shape*; the checks here decide
whether an individual recipient can actually get a certificate. A recipient
that fails is recorded as "failed" on the job with the reason, and the rest
of the job carries on.
"""

import re
from dataclasses import dataclass

from app.certificates import unprintable_characters
from app.schemas import RecipientIn

MAX_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254

# Deliberately simple: something@something.tld, no whitespace. This is a
# format sanity check, not a deliverability check (that would need DNS/SMTP
# lookups and doesn't belong in a request path).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RecipientValidationError(ValueError):
    """Raised with a human-readable, client-facing message."""


@dataclass(frozen=True)
class ValidatedRecipient:
    name: str
    email: str


def validate_recipient(raw: RecipientIn) -> ValidatedRecipient:
    errors: list[str] = []

    name = (raw.name or "").strip()
    if not name:
        errors.append("name is required")
    elif len(name) > MAX_NAME_LENGTH:
        errors.append(f"name must be at most {MAX_NAME_LENGTH} characters")
    elif bad := unprintable_characters(name):
        # Better to report it now than to hand out a certificate full of
        # missing-glyph boxes.
        errors.append(
            f"name contains characters the certificate template cannot print: {bad!r} "
            "(only Latin-script characters are supported)"
        )

    email = (raw.email or "").strip()
    if not email:
        errors.append("email is required")
    elif len(email) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(email):
        errors.append("email is not a valid email address")

    if errors:
        raise RecipientValidationError("; ".join(errors))

    return ValidatedRecipient(name=name, email=email.lower())
