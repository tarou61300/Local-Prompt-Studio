from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from core.prompt_library_datasets import (
    DATASET_REGISTRY_FILENAME,
    DEFAULT_DATASET_ID,
    MANAGED_DATASET_DIRECTORY,
    PromptLibraryDatasetError,
    PromptLibraryDatasetRegistry,
    validate_prompt_library_database,
)
from core.prompt_library_manager import (
    PROMPT_LIBRARY_DATABASE_NAME,
    PROMPT_LIBRARY_SCHEMA_VERSION,
    PromptLibraryManager,
)


def _create_prompt(manager: PromptLibraryManager, title: str, body: str):
    return manager.create_prompt(
        title=title,
        model_id="minimax_h3",
        task_id="T2VA",
        prompt_text=body,
        tag_names=("shared-tag",),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_legacy_database_is_lazy_default_and_never_moved(tmp_path) -> None:
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    expected = _create_prompt(manager, "Legacy", "legacy body")
    database_path = data_dir / PROMPT_LIBRARY_DATABASE_NAME
    before = _sha256(database_path)

    registry = PromptLibraryDatasetRegistry(data_dir)

    assert registry.active_dataset_id == DEFAULT_DATASET_ID
    assert registry.active_dataset().database_path == database_path
    assert registry.datasets() == (registry.active_dataset(),)
    assert not registry.registry_path.exists()
    assert not registry.managed_root.exists()
    assert PromptLibraryManager(data_dir).get_prompt(expected.id).prompt_text == "legacy body"
    assert _sha256(database_path) == before


def test_new_datasets_are_schema_v1_persist_active_and_remain_isolated(tmp_path) -> None:
    data_dir = tmp_path / "data"
    default_manager = PromptLibraryManager(data_dir)
    default_prompt = _create_prompt(default_manager, "Default prompt", "default")
    registry = PromptLibraryDatasetRegistry(data_dir)

    dataset_a = registry.create_dataset("Dataset A")
    manager_a = PromptLibraryManager(dataset_a.data_dir)
    prompt_a = _create_prompt(manager_a, "A prompt", "A body")
    dataset_b = registry.create_dataset("Dataset B")
    manager_b = PromptLibraryManager(dataset_b.data_dir)
    prompt_b = _create_prompt(manager_b, "B prompt", "B body")

    assert dataset_a.id != dataset_b.id
    assert dataset_a.data_dir.parent == data_dir / MANAGED_DATASET_DIRECTORY
    assert dataset_a.data_dir.name == dataset_a.id
    for dataset in (dataset_a, dataset_b):
        with sqlite3.connect(dataset.database_path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert manager_a.search_prompts(
        model_id="minimax_h3", task_id="T2VA"
    ).items[0].id == prompt_a.id
    assert manager_b.search_prompts(
        model_id="minimax_h3", task_id="T2VA"
    ).items[0].id == prompt_b.id
    assert PromptLibraryManager(data_dir).get_prompt(default_prompt.id).prompt_text == "default"

    registry.set_active(dataset_a.id)
    reloaded = PromptLibraryDatasetRegistry(data_dir)
    assert reloaded.active_dataset_id == dataset_a.id
    assert [item.display_name for item in reloaded.datasets()] == [
        "Default",
        "Dataset A",
        "Dataset B",
    ]
    registry_text = registry.registry_path.read_text(encoding="utf-8")
    assert "A body" not in registry_text
    assert "B body" not in registry_text
    assert "shared-tag" not in registry_text


def test_empty_duplicate_and_path_like_names_are_handled_as_display_only(tmp_path) -> None:
    registry = PromptLibraryDatasetRegistry(tmp_path / "data")
    with pytest.raises(PromptLibraryDatasetError) as empty:
        registry.create_dataset("  ")
    assert empty.value.code == "PROMPT_LIBRARY_DATASET_NAME_EMPTY"

    created = registry.create_dataset("A / B")
    assert created.display_name == "A / B"
    assert created.data_dir.name == created.id
    with pytest.raises(PromptLibraryDatasetError) as duplicate:
        registry.create_dataset("a / b")
    assert duplicate.value.code == "PROMPT_LIBRARY_DATASET_NAME_DUPLICATE"


def test_valid_external_database_is_imported_by_backup_without_source_changes(
    tmp_path,
) -> None:
    external_dir = tmp_path / "external"
    source_manager = PromptLibraryManager(external_dir)
    source_prompt = _create_prompt(
        source_manager,
        "Imported",
        "  exact\r\n日本語🌙  ",
    )
    source_path = source_manager.database_path
    before = _sha256(source_path)
    registry = PromptLibraryDatasetRegistry(tmp_path / "portable-data")

    imported = registry.import_dataset(source_path, "Imported Dataset")

    assert imported.database_path != source_path
    assert imported.database_path.is_file()
    assert _sha256(source_path) == before
    loaded = PromptLibraryManager(imported.data_dir).get_prompt(source_prompt.id)
    assert loaded.prompt_text == source_prompt.prompt_text
    assert registry.active_dataset_id == imported.id


@pytest.mark.parametrize("kind", ["not_sqlite", "wrong_schema", "future"])
def test_invalid_external_databases_are_rejected_without_registry_changes(
    tmp_path,
    kind,
) -> None:
    source = tmp_path / f"{kind}.sqlite3"
    if kind == "not_sqlite":
        source.write_bytes(b"not sqlite")
    else:
        with sqlite3.connect(source) as connection:
            if kind == "wrong_schema":
                connection.execute("CREATE TABLE unrelated(value TEXT)")
                connection.execute("PRAGMA user_version = 1")
            else:
                connection.execute(
                    f"PRAGMA user_version = {PROMPT_LIBRARY_SCHEMA_VERSION + 1}"
                )
    registry = PromptLibraryDatasetRegistry(tmp_path / "data")

    with pytest.raises(PromptLibraryDatasetError) as error:
        registry.import_dataset(source, "Rejected")

    expected = (
        "PROMPT_LIBRARY_DATASET_UNSUPPORTED_VERSION"
        if kind == "future"
        else "PROMPT_LIBRARY_DATASET_INVALID"
    )
    assert error.value.code == expected
    if kind == "future":
        assert error.value.found_version == PROMPT_LIBRARY_SCHEMA_VERSION + 1
    assert [item.id for item in registry.datasets()] == [DEFAULT_DATASET_ID]
    assert not registry.registry_path.exists()


def test_export_is_consistent_reopenable_exact_and_does_not_change_source(
    tmp_path,
) -> None:
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    created = _create_prompt(manager, "Export", "  line 1\n\nline 2  ")
    registry = PromptLibraryDatasetRegistry(data_dir)
    source_hash = _sha256(manager.database_path)
    destination = tmp_path / "exports" / "library.sqlite3"

    exported = registry.export_dataset(destination)

    assert exported == destination.resolve()
    validate_prompt_library_database(exported)
    with sqlite3.connect(exported) as connection:
        exported_text = connection.execute(
            "SELECT prompt_text FROM prompts WHERE id = ?", (created.id,)
        ).fetchone()[0]
    assert exported_text == created.prompt_text
    assert _sha256(manager.database_path) == source_hash
    with pytest.raises(PromptLibraryDatasetError) as same_path:
        registry.export_dataset(manager.database_path)
    assert same_path.value.code == "PROMPT_LIBRARY_DATASET_SAME_PATH"


def test_corrupt_registry_is_reported_without_touching_legacy_database(tmp_path) -> None:
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    _create_prompt(manager, "Keep", "keep")
    before = _sha256(manager.database_path)
    (data_dir / DATASET_REGISTRY_FILENAME).write_text(
        json.dumps({"registry_version": 999}),
        encoding="utf-8",
    )

    with pytest.raises(PromptLibraryDatasetError) as error:
        PromptLibraryDatasetRegistry(data_dir)

    assert error.value.code == "PROMPT_LIBRARY_DATASET_REGISTRY_INVALID"
    assert _sha256(manager.database_path) == before
