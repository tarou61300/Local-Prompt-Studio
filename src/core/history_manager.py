from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class HistoryManager:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    def add(
        self,
        *,
        enabled: bool,
        mode: str,
        request: str,
        output: str,
        profile_id: str = "minimax_h3",
        profile_version: str = "1.0.0",
        variant_id: str = "base",
        renderer_id: str = "minimax_h3",
        processing_mode: str = "Faithful",
        profile_hash: str = "",
        common_supplement: str = "",
    ) -> bool:
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
                    output TEXT NOT NULL,
                    profile_id TEXT NOT NULL DEFAULT 'minimax_h3',
                    profile_version TEXT NOT NULL DEFAULT '1.0.0',
                    variant_id TEXT NOT NULL DEFAULT 'base',
                    renderer_id TEXT NOT NULL DEFAULT 'minimax_h3',
                    processing_mode TEXT NOT NULL DEFAULT 'Faithful',
                    profile_hash TEXT NOT NULL DEFAULT '',
                    common_supplement TEXT NOT NULL DEFAULT ''
                )"""
            )
            existing_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(history)")
            }
            migrations = {
                "profile_id": "TEXT NOT NULL DEFAULT 'minimax_h3'",
                "profile_version": "TEXT NOT NULL DEFAULT '1.0.0'",
                "variant_id": "TEXT NOT NULL DEFAULT 'base'",
                "renderer_id": "TEXT NOT NULL DEFAULT 'minimax_h3'",
                "processing_mode": "TEXT NOT NULL DEFAULT 'Faithful'",
                "profile_hash": "TEXT NOT NULL DEFAULT ''",
                "common_supplement": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in migrations.items():
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE history ADD COLUMN {column} {declaration}"
                    )
            connection.execute(
                """INSERT INTO history(
                    created_at, mode, request, output, profile_id, profile_version,
                    variant_id, renderer_id, processing_mode, profile_hash,
                    common_supplement
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    mode,
                    request,
                    output,
                    profile_id,
                    profile_version,
                    variant_id,
                    renderer_id,
                    processing_mode,
                    profile_hash,
                    common_supplement,
                ),
            )
        return True

    def clear(self) -> None:
        if not self.database_path.exists():
            return
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP TABLE IF EXISTS history")
