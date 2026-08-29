import json
from pathlib import Path

from core.version import (
    APP_DISPLAY_VERSION,
    APP_RELEASE_DATE,
    APP_VERSION,
    INTERNAL_APPLICATION_ID,
    PRODUCT_NAME,
    REPOSITORY_URL,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_local_prompt_studio_identity_and_version():
    assert PRODUCT_NAME == "Local Prompt Studio"
    assert INTERNAL_APPLICATION_ID == "local_prompt_studio"
    assert APP_VERSION == "3.1.0"
    assert APP_DISPLAY_VERSION == "v3.1.0"
    assert APP_RELEASE_DATE == "2026-08-28"
    assert REPOSITORY_URL == "https://github.com/tarou61300/Local-Prompt-Studio"


def test_repository_identity_is_used_by_current_user_facing_surfaces():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    manifest_template = (
        PROJECT_ROOT / "packaging" / "RELEASE_MANIFEST.template.txt"
    ).read_text(encoding="utf-8")
    build_script = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )
    bridge_frontend = (
        PROJECT_ROOT
        / "comfyui_extension"
        / "MMH3PromptBridge"
        / "js"
        / "mmh3_bridge.js"
    ).read_text(encoding="utf-8")

    assert REPOSITORY_URL in readme
    assert "Repository: {{REPOSITORY_URL}}" in manifest_template
    assert "Source state: {{SOURCE_STATE}}" in manifest_template
    assert '{{REPOSITORY_URL}}", $RepositoryUrl' in build_script
    assert '{{SOURCE_STATE}}", $SourceState' in build_script
    assert "Pair with Local Prompt Studio" in bridge_frontend

    for locale_name in ("en-US.json", "ja-JP.json", "zh-CN.json", "ru-RU.json"):
        locale = json.loads(
            (PROJECT_ROOT / "locales" / locale_name).read_text(encoding="utf-8")
        )
        about = locale["about.body"].format(
            product=PRODUCT_NAME,
            version=APP_DISPLAY_VERSION,
            date=APP_RELEASE_DATE,
            repository=REPOSITORY_URL,
        )
        assert REPOSITORY_URL in about


def test_prompt_library_user_database_is_documented_ignored_and_banned_from_release():
    portable_readme = (
        PROJECT_ROOT / "packaging" / "PORTABLE_DATA_README.txt"
    ).read_text(encoding="utf-8")
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    audit_script = (PROJECT_ROOT / "scripts" / "audit_release.py").read_text(
        encoding="utf-8"
    )

    assert '"_internal/locales/zh-CN.json"' in audit_script
    assert '"_internal/locales/ru-RU.json"' in audit_script

    assert "<repository>/.dev-data/prompt_library.sqlite3" in portable_readme
    assert "prompt_library.sqlite3*" in gitignore
    assert "prompt_library_datasets.json" in gitignore
    assert "prompt_library_datasets/" in gitignore
    for filename in (
        "prompt_library.sqlite3",
        "prompt_library.sqlite3-wal",
        "prompt_library.sqlite3-shm",
    ):
        assert filename in portable_readme
        assert f'"{filename}"' in audit_script
    assert "prompt_library_datasets.json" in portable_readme
    assert "prompt_library_datasets/" in portable_readme
    assert "prompt_library_datasets.json" in audit_script
    assert "prompt_library_datasets" in audit_script
