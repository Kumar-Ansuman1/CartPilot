import sqlite3
from pathlib import Path

import pytest

from backend.app.database import database_connection


def test_database_connection_commits_and_closes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "cartpilot-test.db"

    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(database_path),
    )

    with database_connection() as connection:
        foreign_keys_enabled = connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()

        connection.execute(
            """
            CREATE TABLE example (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO example (value)
            VALUES (?)
            """,
            ("saved",),
        )

    assert foreign_keys_enabled == (1,)

    with sqlite3.connect(database_path) as verification:
        row = verification.execute(
            "SELECT value FROM example"
        ).fetchone()

    assert row == ("saved",)

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")