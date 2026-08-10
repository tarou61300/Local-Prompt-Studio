from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class HistoryManager:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def add(self, *, enabled: bool, mode: str, request: str, output: str) -> bool:
        if not enabled:
            return False
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    request TEXT NOT NULL,
                    output TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT INTO history(created_at, mode, request, output) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), mode, request, output),
            )
        return True

    def clear(self) -> None:
        if not self.database_path.exists():
            return
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP TABLE IF EXISTS history")

