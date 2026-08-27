from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .prompt_library_manager import (
    PROMPT_LIBRARY_DATABASE_NAME,
    PROMPT_LIBRARY_SCHEMA_VERSION,
    PromptLibraryError,
    PromptLibraryManager,
)


DATASET_REGISTRY_VERSION = 1
DATASET_REGISTRY_FILENAME = "prompt_library_datasets.json"
MANAGED_DATASET_DIRECTORY = "prompt_library_datasets"
DEFAULT_DATASET_ID = "default"

_REQUIRED_COLUMNS = {
    "prompts": {
        "id",
        "uuid",
        "title",
        "model_id",
        "task_id",
        "prompt_text",
        "created_at",
        "updated_at",
    },
    "tag_categories": {
        "id",
        "uuid",
        "name",
        "normalized_name",
        "sort_order",
        "created_at",
    },
    "tags": {
        "id",
        "uuid",
        "name",
        "normalized_name",
        "category_id",
        "created_at",
    },
    "prompt_tags": {"prompt_id", "tag_id"},
    "tag_preferences": {"tag_id", "updated_at"},
}


class PromptLibraryDatasetError(RuntimeError):
    def __init__(self, code: str, *, found_version: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.found_version = found_version


@dataclass(frozen=True, slots=True)
class PromptLibraryDataset:
    id: str
    display_name: str
    data_dir: Path
    is_default: bool = False

    @property
    def database_path(self) -> Path:
        return self.data_dir / PROMPT_LIBRARY_DATABASE_NAME


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def validate_prompt_library_database(path: Path | str) -> None:
    database_path = Path(path)
    if not database_path.is_file():
        raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_INVALID")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(_read_only_uri(database_path), uri=True)
        connection.execute("PRAGMA query_only = ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > PROMPT_LIBRARY_SCHEMA_VERSION:
            raise PromptLibraryDatasetError(
                "PROMPT_LIBRARY_DATASET_UNSUPPORTED_VERSION",
                found_version=version,
            )
        if version != PROMPT_LIBRARY_SCHEMA_VERSION:
            raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_INVALID")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or str(quick_check[0]).casefold() != "ok":
            raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_INVALID")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not _REQUIRED_COLUMNS.keys() <= tables:
            raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_INVALID")
        for table_name, required_columns in _REQUIRED_COLUMNS.items():
            columns = {
                str(row[1])
                for row in connection.execute(
                    f'PRAGMA table_info("{table_name}")'
                )
            }
            if not required_columns <= columns:
                raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_INVALID")
    except PromptLibraryDatasetError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_INVALID") from exc
    finally:
        if connection is not None:
            connection.close()


def _backup_database(source: Path, destination: Path) -> None:
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(_read_only_uri(source), uri=True)
        source_connection.execute("PRAGMA query_only = ON")
        destination_connection = sqlite3.connect(destination)
        source_connection.backup(destination_connection)
        destination_connection.commit()
    except (OSError, sqlite3.Error) as exc:
        raise PromptLibraryDatasetError(
            "PROMPT_LIBRARY_DATASET_OPERATION_FAILED"
        ) from exc
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()


def _normalize_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_NAME_EMPTY")
    display_name = value.strip()
    if not display_name:
        raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_NAME_EMPTY")
    if len(display_name) > 100 or any(
        unicodedata.category(character).startswith("C")
        for character in display_name
    ):
        raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_NAME_INVALID")
    return display_name


class PromptLibraryDatasetRegistry:
    """Small portable registry for independent Prompt Library SQLite files."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.registry_path = self.data_dir / DATASET_REGISTRY_FILENAME
        self.managed_root = self.data_dir / MANAGED_DATASET_DIRECTORY
        self._records: list[tuple[str, str]] = []
        self._active_id = DEFAULT_DATASET_ID
        self._load()

    def _default_dataset(self) -> PromptLibraryDataset:
        return PromptLibraryDataset(
            id=DEFAULT_DATASET_ID,
            display_name="Default",
            data_dir=self.data_dir,
            is_default=True,
        )

    def datasets(self) -> tuple[PromptLibraryDataset, ...]:
        return (
            self._default_dataset(),
            *(
                PromptLibraryDataset(
                    id=dataset_id,
                    display_name=display_name,
                    data_dir=self.managed_root / dataset_id,
                )
                for dataset_id, display_name in self._records
            ),
        )

    @property
    def active_dataset_id(self) -> str:
        return self._active_id

    def active_dataset(self) -> PromptLibraryDataset:
        return self.dataset(self._active_id)

    def dataset(self, dataset_id: str) -> PromptLibraryDataset:
        for item in self.datasets():
            if item.id == dataset_id:
                return item
        raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_NOT_FOUND")

    def _load(self) -> None:
        if not self.registry_path.exists():
            return
        try:
            raw: dict[str, Any] = json.loads(
                self.registry_path.read_text(encoding="utf-8")
            )
            if int(raw.get("registry_version", 0)) != DATASET_REGISTRY_VERSION:
                raise ValueError("unsupported registry version")
            entries = raw.get("datasets", [])
            if not isinstance(entries, list):
                raise TypeError("datasets must be a list")
            records: list[tuple[str, str]] = []
            ids: set[str] = set()
            names: set[str] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise TypeError("dataset must be an object")
                dataset_id = str(entry.get("id", ""))
                parsed_id = str(uuid.UUID(dataset_id))
                if parsed_id != dataset_id or dataset_id in ids:
                    raise ValueError("invalid dataset id")
                display_name = _normalize_display_name(entry.get("display_name"))
                name_key = display_name.casefold()
                if name_key in names or name_key == "default":
                    raise ValueError("duplicate dataset name")
                records.append((dataset_id, display_name))
                ids.add(dataset_id)
                names.add(name_key)
            active_id = str(raw.get("active_dataset_id", DEFAULT_DATASET_ID))
            if active_id != DEFAULT_DATASET_ID and active_id not in ids:
                active_id = DEFAULT_DATASET_ID
            self._records = records
            self._active_id = active_id
        except (
            PromptLibraryDatasetError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise PromptLibraryDatasetError(
                "PROMPT_LIBRARY_DATASET_REGISTRY_INVALID"
            ) from exc

    def _save(self) -> None:
        payload = {
            "registry_version": DATASET_REGISTRY_VERSION,
            "active_dataset_id": self._active_id,
            "datasets": [
                {"id": dataset_id, "display_name": display_name}
                for dataset_id, display_name in self._records
            ],
        }
        temporary_path: Path | None = None
        descriptor = -1
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix="prompt-library-datasets-",
                suffix=".tmp",
                dir=self.data_dir,
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                descriptor = -1
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary_path, self.registry_path)
        except OSError as exc:
            raise PromptLibraryDatasetError(
                "PROMPT_LIBRARY_DATASET_OPERATION_FAILED"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _validated_new_name(self, value: object) -> str:
        display_name = _normalize_display_name(value)
        used_names = {dataset.display_name.casefold() for dataset in self.datasets()}
        if display_name.casefold() in used_names:
            raise PromptLibraryDatasetError(
                "PROMPT_LIBRARY_DATASET_NAME_DUPLICATE"
            )
        return display_name

    def set_active(self, dataset_id: str) -> PromptLibraryDataset:
        record = self.dataset(dataset_id)
        previous = self._active_id
        self._active_id = record.id
        try:
            self._save()
        except PromptLibraryDatasetError:
            self._active_id = previous
            raise
        return record

    @staticmethod
    def _remove_created_directory(directory: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            (directory / f"{PROMPT_LIBRARY_DATABASE_NAME}{suffix}").unlink(
                missing_ok=True
            )
        try:
            directory.rmdir()
        except OSError:
            pass

    def create_dataset(self, display_name: object) -> PromptLibraryDataset:
        stored_name = self._validated_new_name(display_name)
        dataset_id = str(uuid.uuid4())
        directory = self.managed_root / dataset_id
        previous_records = list(self._records)
        previous_active = self._active_id
        try:
            PromptLibraryManager(directory)
            validate_prompt_library_database(directory / PROMPT_LIBRARY_DATABASE_NAME)
            self._records.append((dataset_id, stored_name))
            self._active_id = dataset_id
            self._save()
        except PromptLibraryDatasetError:
            self._records = previous_records
            self._active_id = previous_active
            self._remove_created_directory(directory)
            raise
        except (OSError, sqlite3.Error, PromptLibraryError) as exc:
            self._records = previous_records
            self._active_id = previous_active
            self._remove_created_directory(directory)
            raise PromptLibraryDatasetError(
                "PROMPT_LIBRARY_DATASET_OPERATION_FAILED"
            ) from exc
        return self.dataset(dataset_id)

    def import_dataset(
        self,
        source: Path | str,
        display_name: object,
    ) -> PromptLibraryDataset:
        stored_name = self._validated_new_name(display_name)
        source_path = Path(source)
        validate_prompt_library_database(source_path)
        dataset_id = str(uuid.uuid4())
        directory = self.managed_root / dataset_id
        database_path = directory / PROMPT_LIBRARY_DATABASE_NAME
        temporary_path = directory / f"{PROMPT_LIBRARY_DATABASE_NAME}.tmp"
        previous_records = list(self._records)
        previous_active = self._active_id
        try:
            directory.mkdir(parents=True, exist_ok=False)
            _backup_database(source_path, temporary_path)
            validate_prompt_library_database(temporary_path)
            os.replace(temporary_path, database_path)
            self._records.append((dataset_id, stored_name))
            self._active_id = dataset_id
            self._save()
        except PromptLibraryDatasetError:
            self._records = previous_records
            self._active_id = previous_active
            temporary_path.unlink(missing_ok=True)
            self._remove_created_directory(directory)
            raise
        except (OSError, sqlite3.Error, PromptLibraryError) as exc:
            self._records = previous_records
            self._active_id = previous_active
            temporary_path.unlink(missing_ok=True)
            self._remove_created_directory(directory)
            raise PromptLibraryDatasetError(
                "PROMPT_LIBRARY_DATASET_OPERATION_FAILED"
            ) from exc
        return self.dataset(dataset_id)

    def export_dataset(
        self,
        destination: Path | str,
        dataset_id: str | None = None,
    ) -> Path:
        record = self.dataset(dataset_id or self._active_id)
        source_path = record.database_path.resolve(strict=False)
        destination_path = Path(destination).resolve(strict=False)
        if source_path == destination_path:
            raise PromptLibraryDatasetError("PROMPT_LIBRARY_DATASET_SAME_PATH")
        validate_prompt_library_database(source_path)
        temporary_path: Path | None = None
        try:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f"{destination_path.name}.",
                suffix=".tmp",
                dir=destination_path.parent,
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            _backup_database(source_path, temporary_path)
            validate_prompt_library_database(temporary_path)
            os.replace(temporary_path, destination_path)
        except PromptLibraryDatasetError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatasetError(
                "PROMPT_LIBRARY_DATASET_OPERATION_FAILED"
            ) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return destination_path
