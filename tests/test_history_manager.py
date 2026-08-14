from __future__ import annotations

import sqlite3

from core.history_manager import HistoryManager


def test_history_off_does_not_store_prompt_body(tmp_path):
    database = tmp_path / "history.sqlite3"
    history = HistoryManager(database)
    assert history.add(enabled=False, mode="T2VA", request="secret request", output="secret output") is False
    assert not database.exists()


def test_history_on_stores_locally(tmp_path):
    database = tmp_path / "history.sqlite3"
    history = HistoryManager(database)
    assert history.add(enabled=True, mode="T2VA", request="request", output="output")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT mode, request, output FROM history").fetchone() == (
            "T2VA",
            "request",
            "output",
        )
        assert connection.execute("SELECT renderer_id FROM history").fetchone() == (
            "minimax_h3",
        )
