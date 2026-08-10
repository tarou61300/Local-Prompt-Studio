from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.skill_manager import SkillError, SkillManager


FIXTURE = Path(__file__).parent / "fixtures" / "skills" / "h3-prompt-writing"


def test_skill_presence_hashes_and_mode_reference(tmp_path):
    target = tmp_path / "h3-prompt-writing"
    shutil.copytree(FIXTURE, target)
    manager = SkillManager(target)
    status = manager.status()
    assert status.installed and status.valid
    assert len(status.sha256["SKILL.md"]) == 64
    assert "base guide" in manager.reference_for_mode("T2VA")
    assert "reference guide" in manager.reference_for_mode("Ref2VA")


def test_damaged_skill_is_rejected(tmp_path):
    target = tmp_path / "h3-prompt-writing"
    target.mkdir()
    (target / "SKILL.md").write_text("incomplete", encoding="utf-8")
    manager = SkillManager(target)
    assert manager.status().installed
    assert not manager.status().valid
    with pytest.raises(SkillError):
        manager.load_skill()


def test_unreadable_skill_does_not_crash_status(monkeypatch, tmp_path):
    target = tmp_path / "h3-prompt-writing"
    target.mkdir()
    original_is_file = Path.is_file

    def denied(path):
        if target in path.parents:
            raise PermissionError("mock access denied")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", denied)
    status = SkillManager(target).status()
    assert status.installed is True
    assert status.valid is False
    assert "読み取れません" in (status.error or "")
