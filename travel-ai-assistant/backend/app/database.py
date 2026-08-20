"""SQLite database setup for Voyage AI.

Future work:
- Add migrations with Alembic before the schema starts changing often.
- Replace SQLite with PostgreSQL if the project needs concurrent production use.
"""

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_URL = f"sqlite:///{(BACKEND_DIR / 'travel_assistant.db').as_posix()}"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
    if DATABASE_URL.startswith("sqlite")
    else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def create_db_and_tables() -> None:
    """Create tables for local development when the app starts."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_local_compatibility_columns()


def _ensure_local_compatibility_columns() -> None:
    """Add local columns for databases created before later application stages.

    This is deliberately small compatibility support for the existing SQLite
    database. A hosted deployment should use versioned Alembic migrations.
    """

    inspector = inspect(engine)
    table_additions = {
        "users": {
            "password_hash": "VARCHAR(255) NOT NULL DEFAULT ''",
            "full_name": "VARCHAR(120)",
            "updated_at": "DATETIME",
        },
        "session_states": {
            "latest_hotel_search_request_json": "TEXT",
            "latest_hotel_results_json": "TEXT",
            "current_hotel_option_json": "TEXT",
            "pending_hotel_selection_json": "TEXT",
            "latest_destination_discovery_request_json": "TEXT",
            "latest_destination_discovery_results_json": "TEXT",
            "pending_destination_discovery_json": "TEXT",
            "active_trip_json": "TEXT",
            "default_origin_json": "TEXT",
        },
        "bookings": {
            "booking_type": "VARCHAR(40) NOT NULL DEFAULT 'manual'",
            "details_json": "TEXT",
        },
    }

    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, additions in table_additions.items():
            if table_name not in existing_tables:
                continue
            columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
                    )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
