import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "cartpilot.db"
)


def get_database_path() -> Path:
    configured_path = os.getenv("CARTPILOT_DB_PATH")

    if configured_path:
        return Path(configured_path)

    return DEFAULT_DATABASE_PATH


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    database_path = get_database_path()
    database_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()