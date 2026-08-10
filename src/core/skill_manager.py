from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SKILL_NAME = "h3-prompt-writing"
REQUIRED_FILES = (
    "SKILL.md",
    "references/base-en.txt",
    "references/ref-en.txt",
)
RAW_BASE_URL = (
    "https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/main/skills/"
    "h3-prompt-writing"
)


class SkillError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SkillStatus:
    installed: bool
    valid: bool
    location: Path
    fetched_at: str | None
    sha256: dict[str, str]
    error: str | None = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SkillManager:
    """Validates and explicitly downloads the official H3 prompt-writing skill."""

    def __init__(self, location: Path | str) -> None:
        self.location = Path(location)

    def status(self) -> SkillStatus:
        try:
            if not self.location.exists():
                return SkillStatus(False, False, self.location, None, {}, "Skill未導入")
            missing = [
                relative for relative in REQUIRED_FILES if not (self.location / relative).is_file()
            ]
        except OSError as exc:
            return SkillStatus(
                True,
                False,
                self.location,
                None,
                {},
                f"Skillフォルダを読み取れません: {exc}",
            )
        if missing:
            return SkillStatus(
                True,
                False,
                self.location,
                self._metadata().get("fetched_at"),
                {},
                "必要ファイルがありません: " + ", ".join(missing),
            )
        try:
            hashes = {relative: _sha256(self.location / relative) for relative in REQUIRED_FILES}
        except OSError as exc:
            return SkillStatus(
                True,
                False,
                self.location,
                self._metadata().get("fetched_at"),
                {},
                f"Skillファイルを読み取れません: {exc}",
            )
        return SkillStatus(
            True,
            True,
            self.location,
            self._metadata().get("fetched_at"),
            hashes,
        )

    def _metadata(self) -> dict[str, object]:
        metadata_path = self.location / ".mmh3-skill.json"
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def load_skill(self) -> str:
        self.require_valid()
        return (self.location / "SKILL.md").read_text(encoding="utf-8")

    def reference_for_mode(self, mode: str) -> str:
        self.require_valid()
        filename = "ref-en.txt" if mode == "Ref2VA" else "base-en.txt"
        if mode not in {"T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"}:
            raise SkillError(f"未対応のH3モードです: {mode}")
        return (self.location / "references" / filename).read_text(encoding="utf-8")

    def require_valid(self) -> SkillStatus:
        status = self.status()
        if not status.valid:
            raise SkillError(status.error or "MiniMax H3 Prompt Skillが破損しています。")
        return status

    def install_or_update(self, timeout: float = 30.0) -> SkillStatus:
        """Download to a sibling staging folder, validate, then atomically replace."""
        self.location.parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix="h3-skill-", dir=self.location.parent))
        staged_skill = staging_root / SKILL_NAME
        try:
            for relative in REQUIRED_FILES:
                target = staged_skill / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                url = f"{RAW_BASE_URL}/{relative}"
                try:
                    with urllib.request.urlopen(url, timeout=timeout) as response:
                        content = response.read()
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    raise SkillError(f"MiniMax公式Skillの取得に失敗しました: {exc}") from exc
                if not content.strip():
                    raise SkillError(f"取得したSkillファイルが空です: {relative}")
                target.write_bytes(content)

            hashes = {relative: _sha256(staged_skill / relative) for relative in REQUIRED_FILES}
            metadata = {
                "source": RAW_BASE_URL,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashes,
            }
            (staged_skill / ".mmh3-skill.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            staged_status = SkillManager(staged_skill).status()
            if not staged_status.valid:
                raise SkillError(staged_status.error or "取得したSkillを検証できませんでした。")

            backup = self.location.with_name(self.location.name + ".backup")
            if backup.exists():
                shutil.rmtree(backup)
            if self.location.exists():
                os.replace(self.location, backup)
            try:
                os.replace(staged_skill, self.location)
            except Exception:
                if backup.exists() and not self.location.exists():
                    os.replace(backup, self.location)
                raise
            if backup.exists():
                shutil.rmtree(backup)
            return self.require_valid()
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def check_for_update(self, timeout: float = 15.0) -> bool:
        local = self.require_valid().sha256
        for relative in REQUIRED_FILES:
            request = urllib.request.Request(f"{RAW_BASE_URL}/{relative}", method="GET")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    remote_hash = hashlib.sha256(response.read()).hexdigest()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise SkillError(f"Skill更新確認に失敗しました: {exc}") from exc
            if local.get(relative) != remote_hash:
                return True
        return False
