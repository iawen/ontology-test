"""Deprecated SQLite compatibility facade.

Database schema creation and migrations are owned exclusively by
:mod:`core.db.db_provider`.  Keep this module for older manual scripts and
imports, while ensuring they use the same schema (including
``messages.clarification``) as the application.
"""

from .db_provider import (
    DatabaseConfigError,
    IntegrityError,
    OperationalError,
    get_db,
    get_db_type,
    init_db,
)

__all__ = [
    "DatabaseConfigError",
    "IntegrityError",
    "OperationalError",
    "get_db",
    "get_db_type",
    "init_db",
]


if __name__ == "__main__":
    init_db()
