"""
Secrets hygiene: .env loading and log redaction.

Secrets come only from the process environment, optionally populated from a
local `.env` file (never committed; see `.env.example`). They are never written
to logs: `install_redaction()` masks the value of every secret-like variable in
all log records.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

SECRET_NAME_RE = re.compile(r"(SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE|SALT|CLIENT_ID)", re.I)
_MIN_SECRET_LEN = 6


def load_dotenv(path: str | os.PathLike[str] = ".env") -> list[str]:
    """Load KEY=VALUE lines into os.environ without overriding existing values.

    Returns the names loaded (never the values).
    """
    p = Path(path)
    if not p.is_file():
        return []
    loaded = []
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def secret_values() -> list[str]:
    return sorted(
        (v for k, v in os.environ.items() if SECRET_NAME_RE.search(k) and len(v) >= _MIN_SECRET_LEN),
        key=len,
        reverse=True,
    )


def redact(text: str) -> str:
    for value in secret_values():
        text = text.replace(value, "***REDACTED***")
    return text


class RedactingFilter(logging.Filter):
    """Masks secret values in every log record passing through a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def install_redaction() -> None:
    """Attach the redacting filter to every root handler (call after logging is configured)."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO)
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
