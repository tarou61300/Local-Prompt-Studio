from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys

import pytest

from core.config_manager import ConfigManager, PORTABLE_WRITE_ERROR
from main import application_paths


def test_frozen_application_defaults_to_data_beside_executable(tmp_path, monkeypatch):
    portable_root = tmp_path / "日本語 スペース" / "MMH3"
    internal_root = portable_root / "_internal"
    executable = portable_root / "LocalPromptStudio.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal_root), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    resources, data = application_paths(Namespace(portable_data=None))
    assert resources == internal_root.resolve()
    assert data == (portable_root / "data").resolve()


def test_frozen_application_ignores_portable_data_override(tmp_path, monkeypatch):
    portable_root = tmp_path / "packaged"
    external_data = tmp_path / "external-data"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(portable_root / "_internal"), raising=False)
    monkeypatch.setattr(sys, "executable", str(portable_root / "LocalPromptStudio.exe"))

    _, data = application_paths(Namespace(portable_data=external_data))

    assert data == (portable_root / "data").resolve()
    assert data != external_data.resolve()


def test_source_application_defaults_to_project_dev_data(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    resources, data = application_paths(Namespace(portable_data=None))

    assert data == (resources / ".dev-data").resolve()


def test_portable_data_write_probe_stays_in_selected_folder(tmp_path):
    data = tmp_path / "配布 フォルダ" / "data"
    manager = ConfigManager(data)
    manager.ensure_writable()
    assert data.is_dir()
    assert list(data.glob("write-test-*.tmp")) == []


def test_write_failure_uses_required_japanese_guidance(monkeypatch, tmp_path):
    manager = ConfigManager(tmp_path / "data")
    monkeypatch.setattr("core.config_manager.tempfile.mkstemp", lambda **kwargs: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        manager.ensure_writable()
    assert PORTABLE_WRITE_ERROR == (
        "この場所には設定を書き込めません。Downloads、Documents、Desktop等の"
        "書き込み可能なフォルダへ解凍してください。"
    )
