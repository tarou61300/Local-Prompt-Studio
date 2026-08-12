from __future__ import annotations

import sqlite3

from core.history_manager import HistoryManager


def test_existing_v1_history_table_migrates_without_data_loss(tmp_path):
    database = tmp_path / "history.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                request TEXT NOT NULL,
                output TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO history(created_at, mode, request, output) VALUES ('now', 'T2VA', 'old', 'kept')"
        )
    manager = HistoryManager(database)
    manager.add(
        enabled=True,
        mode="I2VA",
        request="new",
        output="output",
        profile_id="minimax_h3",
        profile_version="1.0.0",
        variant_id="base",
        renderer_id="video_narrative",
        processing_mode="Balanced",
        profile_hash="abc",
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT request, output FROM history ORDER BY id").fetchall() == [
            ("old", "kept"),
            ("new", "output"),
        ]
        metadata = connection.execute(
            """SELECT profile_id, profile_version, variant_id, renderer_id,
                      processing_mode, profile_hash
               FROM history WHERE request = 'new'"""
        ).fetchone()
    assert metadata == (
        "minimax_h3",
        "1.0.0",
        "base",
        "video_narrative",
        "Balanced",
        "abc",
    )
