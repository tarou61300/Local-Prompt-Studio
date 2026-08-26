from __future__ import annotations

import re
import sqlite3
import unicodedata
import uuid as uuid_module
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROMPT_LIBRARY_DATABASE_NAME = "prompt_library.sqlite3"
PROMPT_LIBRARY_SCHEMA_VERSION = 1
PROMPT_LIBRARY_PAGE_SIZE = 50
INITIAL_TAG_CANDIDATE_LIMIT = 100
SEARCH_TAG_CANDIDATE_LIMIT = 50
GLOBAL_TAG_CANDIDATE_LIMIT = 20

_MODEL_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TASK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,31}$")


class PromptLibraryError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PromptLibraryValidationError(PromptLibraryError):
    pass


class PromptLibrarySchemaError(PromptLibraryError):
    def __init__(self, found_version: int) -> None:
        super().__init__("PROMPT_LIBRARY_SCHEMA_UNSUPPORTED")
        self.found_version = found_version
        self.supported_version = PROMPT_LIBRARY_SCHEMA_VERSION


class PromptLibraryDatabaseError(PromptLibraryError):
    pass


@dataclass(frozen=True, slots=True)
class TagRecord:
    id: int
    uuid: str
    name: str
    normalized_name: str
    category_id: int | None
    created_at: str


@dataclass(frozen=True, slots=True)
class TagCandidate:
    tag: TagRecord
    is_favorite: bool
    usage_count: int


@dataclass(frozen=True, slots=True)
class PromptRecord:
    id: int
    uuid: str
    title: str
    model_id: str
    task_id: str
    prompt_text: str
    created_at: str
    updated_at: str
    tags: tuple[TagRecord, ...]


@dataclass(frozen=True, slots=True)
class PromptSummary:
    id: int
    uuid: str
    title: str
    model_id: str
    task_id: str
    updated_at: str
    tags: tuple[TagRecord, ...]


@dataclass(frozen=True, slots=True)
class PromptSearchPage:
    total_count: int
    page: int
    page_size: int
    items: tuple[PromptSummary, ...]


_SCHEMA_STATEMENTS = (
    """CREATE TABLE prompts (
        id          INTEGER PRIMARY KEY,
        uuid        TEXT NOT NULL UNIQUE,
        title       TEXT NOT NULL CHECK (length(trim(title)) > 0),
        model_id    TEXT NOT NULL CHECK (length(trim(model_id)) > 0),
        task_id     TEXT NOT NULL CHECK (length(trim(task_id)) > 0),
        prompt_text TEXT NOT NULL CHECK (length(prompt_text) > 0),
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )""",
    """CREATE TABLE tag_categories (
        id              INTEGER PRIMARY KEY,
        uuid            TEXT NOT NULL UNIQUE,
        name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
        normalized_name TEXT NOT NULL UNIQUE,
        sort_order      INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE tags (
        id              INTEGER PRIMARY KEY,
        uuid            TEXT NOT NULL UNIQUE,
        name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
        normalized_name TEXT NOT NULL UNIQUE,
        category_id     INTEGER NULL
                            REFERENCES tag_categories(id)
                            ON DELETE SET NULL,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE prompt_tags (
        prompt_id INTEGER NOT NULL
                          REFERENCES prompts(id)
                          ON DELETE CASCADE,
        tag_id    INTEGER NOT NULL
                          REFERENCES tags(id)
                          ON DELETE CASCADE,
        PRIMARY KEY (prompt_id, tag_id)
    )""",
    """CREATE TABLE tag_preferences (
        tag_id     INTEGER PRIMARY KEY
                           REFERENCES tags(id)
                           ON DELETE CASCADE,
        updated_at TEXT NOT NULL
    )""",
    """CREATE INDEX idx_prompts_target_order
       ON prompts(model_id, task_id, updated_at DESC, id DESC)""",
    """CREATE INDEX idx_prompts_target_title
       ON prompts(model_id, task_id, title COLLATE NOCASE)""",
    """CREATE INDEX idx_prompt_tags_tag_prompt
       ON prompt_tags(tag_id, prompt_id)""",
    """CREATE INDEX idx_tags_category
       ON tags(category_id)""",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_uuid() -> str:
    return str(uuid_module.uuid4())


def _has_disallowed_tag_character(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def normalize_tag_name(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_INVALID")
    if _has_disallowed_tag_character(value):
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_INVALID")
    display_name = value.strip()
    if not display_name:
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_EMPTY")
    normalized = unicodedata.normalize("NFKC", display_name)
    normalized = " ".join(normalized.split()).casefold()
    if not normalized or _has_disallowed_tag_character(normalized):
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_INVALID")
    return display_name, normalized


def _normalize_tag_query(value: str) -> str:
    if not isinstance(value, str) or _has_disallowed_tag_character(value):
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_QUERY_INVALID")
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate_title(value: str) -> str:
    if not isinstance(value, str):
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TITLE_INVALID")
    title = value.strip()
    if not title:
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TITLE_EMPTY")
    return title


def _validate_model_id(value: str) -> str:
    if not isinstance(value, str):
        raise PromptLibraryValidationError("PROMPT_LIBRARY_MODEL_INVALID")
    model_id = value
    if _MODEL_ID.fullmatch(model_id) is None:
        raise PromptLibraryValidationError("PROMPT_LIBRARY_MODEL_INVALID")
    return model_id


def _validate_task_id(value: str) -> str:
    if not isinstance(value, str):
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TASK_INVALID")
    task_id = value
    if _TASK_ID.fullmatch(task_id) is None:
        raise PromptLibraryValidationError("PROMPT_LIBRARY_TASK_INVALID")
    return task_id


def _validate_prompt_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptLibraryValidationError("PROMPT_LIBRARY_PROMPT_EMPTY")
    return value


def _validate_positive_id(value: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PromptLibraryValidationError(code)
    return value


def _validate_limit(value: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PromptLibraryValidationError("PROMPT_LIBRARY_LIMIT_INVALID")
    return value


class PromptLibraryManager:
    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        self.database_path = self.data_dir / PROMPT_LIBRARY_DATABASE_NAME
        self._initialize()

    def _open_connection(self) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > PROMPT_LIBRARY_SCHEMA_VERSION:
                raise PromptLibrarySchemaError(version)
            if version == 0:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _SCHEMA_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(
                        f"PRAGMA user_version = {PROMPT_LIBRARY_SCHEMA_VERSION}"
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError(
                "PROMPT_LIBRARY_DATABASE_INITIALIZATION_FAILED"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _tag_from_row(row: sqlite3.Row) -> TagRecord:
        return TagRecord(
            id=int(row["id"]),
            uuid=str(row["uuid"]),
            name=str(row["name"]),
            normalized_name=str(row["normalized_name"]),
            category_id=(
                int(row["category_id"]) if row["category_id"] is not None else None
            ),
            created_at=str(row["created_at"]),
        )

    def _resolve_tag(
        self,
        connection: sqlite3.Connection,
        display_name: str,
        normalized_name: str,
    ) -> TagRecord:
        row = connection.execute(
            "SELECT * FROM tags WHERE normalized_name = ?",
            (normalized_name,),
        ).fetchone()
        if row is not None:
            return self._tag_from_row(row)
        try:
            cursor = connection.execute(
                """INSERT INTO tags(
                       uuid, name, normalized_name, category_id, created_at
                   ) VALUES (?, ?, ?, NULL, ?)""",
                (_new_uuid(), display_name, normalized_name, _utc_now()),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM tags WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()
            if row is None:
                raise
            return self._tag_from_row(row)
        row = connection.execute(
            "SELECT * FROM tags WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("inserted tag is unavailable")
        return self._tag_from_row(row)

    @staticmethod
    def _prepare_tag_names(tag_names: Iterable[str]) -> tuple[tuple[str, str], ...]:
        prepared: list[tuple[str, str]] = []
        seen: set[str] = set()
        try:
            values = tuple(tag_names)
        except TypeError as exc:
            raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_INVALID") from exc
        for value in values:
            display_name, normalized_name = normalize_tag_name(value)
            if normalized_name not in seen:
                prepared.append((display_name, normalized_name))
                seen.add(normalized_name)
        return tuple(prepared)

    @staticmethod
    def _prepare_tag_ids(tag_ids: Iterable[int]) -> tuple[int, ...]:
        try:
            values = tuple(tag_ids)
        except TypeError as exc:
            raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_ID_INVALID") from exc
        return tuple(
            dict.fromkeys(
                _validate_positive_id(value, "PROMPT_LIBRARY_TAG_ID_INVALID")
                for value in values
            )
        )

    def _resolve_tags(
        self,
        connection: sqlite3.Connection,
        prepared_names: tuple[tuple[str, str], ...],
        prepared_ids: tuple[int, ...],
    ) -> tuple[TagRecord, ...]:
        records: dict[int, TagRecord] = {}
        if prepared_ids:
            placeholders = ",".join("?" for _ in prepared_ids)
            rows = connection.execute(
                f"SELECT * FROM tags WHERE id IN ({placeholders})",
                prepared_ids,
            ).fetchall()
            if len(rows) != len(prepared_ids):
                raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_NOT_FOUND")
            for row in rows:
                record = self._tag_from_row(row)
                records[record.id] = record
        for display_name, normalized_name in prepared_names:
            record = self._resolve_tag(connection, display_name, normalized_name)
            records[record.id] = record
        return tuple(sorted(records.values(), key=lambda item: (item.normalized_name, item.id)))

    def get_or_create_tag(self, name: str) -> TagRecord:
        display_name, normalized_name = normalize_tag_name(name)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            with connection:
                return self._resolve_tag(connection, display_name, normalized_name)
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def _tags_for_prompt_ids(
        self,
        connection: sqlite3.Connection,
        prompt_ids: tuple[int, ...],
    ) -> dict[int, tuple[TagRecord, ...]]:
        if not prompt_ids:
            return {}
        placeholders = ",".join("?" for _ in prompt_ids)
        rows = connection.execute(
            f"""SELECT pt.prompt_id, t.*
                FROM prompt_tags AS pt
                JOIN tags AS t ON t.id = pt.tag_id
                WHERE pt.prompt_id IN ({placeholders})
                ORDER BY pt.prompt_id, t.normalized_name, t.id""",
            prompt_ids,
        ).fetchall()
        grouped: dict[int, list[TagRecord]] = {prompt_id: [] for prompt_id in prompt_ids}
        for row in rows:
            grouped[int(row["prompt_id"])].append(self._tag_from_row(row))
        return {key: tuple(value) for key, value in grouped.items()}

    def _get_prompt_with_connection(
        self,
        connection: sqlite3.Connection,
        prompt_id: int,
    ) -> PromptRecord:
        row = connection.execute(
            "SELECT * FROM prompts WHERE id = ?", (prompt_id,)
        ).fetchone()
        if row is None:
            raise PromptLibraryValidationError("PROMPT_LIBRARY_PROMPT_NOT_FOUND")
        tags = self._tags_for_prompt_ids(connection, (prompt_id,)).get(prompt_id, ())
        return PromptRecord(
            id=int(row["id"]),
            uuid=str(row["uuid"]),
            title=str(row["title"]),
            model_id=str(row["model_id"]),
            task_id=str(row["task_id"]),
            prompt_text=str(row["prompt_text"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            tags=tags,
        )

    def create_prompt(
        self,
        *,
        title: str,
        model_id: str,
        task_id: str,
        prompt_text: str,
        tag_names: Iterable[str] = (),
        tag_ids: Iterable[int] = (),
    ) -> PromptRecord:
        stored_title = _validate_title(title)
        stored_model_id = _validate_model_id(model_id)
        stored_task_id = _validate_task_id(task_id)
        stored_prompt_text = _validate_prompt_text(prompt_text)
        prepared_names = self._prepare_tag_names(tag_names)
        prepared_ids = self._prepare_tag_ids(tag_ids)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            with connection:
                tags = self._resolve_tags(connection, prepared_names, prepared_ids)
                timestamp = _utc_now()
                cursor = connection.execute(
                    """INSERT INTO prompts(
                           uuid, title, model_id, task_id, prompt_text,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _new_uuid(),
                        stored_title,
                        stored_model_id,
                        stored_task_id,
                        stored_prompt_text,
                        timestamp,
                        timestamp,
                    ),
                )
                prompt_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO prompt_tags(prompt_id, tag_id) VALUES (?, ?)",
                    ((prompt_id, tag.id) for tag in tags),
                )
                return self._get_prompt_with_connection(connection, prompt_id)
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def get_prompt(self, prompt_id: int) -> PromptRecord:
        validated_id = _validate_positive_id(
            prompt_id, "PROMPT_LIBRARY_PROMPT_ID_INVALID"
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            return self._get_prompt_with_connection(connection, validated_id)
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def update_metadata(
        self,
        prompt_id: int,
        *,
        title: str,
        tag_names: Iterable[str] = (),
        tag_ids: Iterable[int] = (),
    ) -> PromptRecord:
        validated_id = _validate_positive_id(
            prompt_id, "PROMPT_LIBRARY_PROMPT_ID_INVALID"
        )
        stored_title = _validate_title(title)
        prepared_names = self._prepare_tag_names(tag_names)
        prepared_ids = self._prepare_tag_ids(tag_ids)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            with connection:
                if connection.execute(
                    "SELECT 1 FROM prompts WHERE id = ?", (validated_id,)
                ).fetchone() is None:
                    raise PromptLibraryValidationError(
                        "PROMPT_LIBRARY_PROMPT_NOT_FOUND"
                    )
                tags = self._resolve_tags(connection, prepared_names, prepared_ids)
                connection.execute(
                    "UPDATE prompts SET title = ?, updated_at = ? WHERE id = ?",
                    (stored_title, _utc_now(), validated_id),
                )
                connection.execute(
                    "DELETE FROM prompt_tags WHERE prompt_id = ?", (validated_id,)
                )
                connection.executemany(
                    "INSERT INTO prompt_tags(prompt_id, tag_id) VALUES (?, ?)",
                    ((validated_id, tag.id) for tag in tags),
                )
                return self._get_prompt_with_connection(connection, validated_id)
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def delete_prompt(self, prompt_id: int) -> None:
        validated_id = _validate_positive_id(
            prompt_id, "PROMPT_LIBRARY_PROMPT_ID_INVALID"
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            with connection:
                cursor = connection.execute(
                    "DELETE FROM prompts WHERE id = ?", (validated_id,)
                )
                if cursor.rowcount != 1:
                    raise PromptLibraryValidationError(
                        "PROMPT_LIBRARY_PROMPT_NOT_FOUND"
                    )
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def set_tag_favorite(self, tag_id: int, favorite: bool) -> None:
        validated_id = _validate_positive_id(tag_id, "PROMPT_LIBRARY_TAG_ID_INVALID")
        if not isinstance(favorite, bool):
            raise PromptLibraryValidationError("PROMPT_LIBRARY_FAVORITE_INVALID")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            with connection:
                if connection.execute(
                    "SELECT 1 FROM tags WHERE id = ?", (validated_id,)
                ).fetchone() is None:
                    raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_NOT_FOUND")
                if favorite:
                    connection.execute(
                        """INSERT INTO tag_preferences(tag_id, updated_at)
                           VALUES (?, ?)
                           ON CONFLICT(tag_id) DO UPDATE
                           SET updated_at = excluded.updated_at""",
                        (validated_id, _utc_now()),
                    )
                else:
                    connection.execute(
                        "DELETE FROM tag_preferences WHERE tag_id = ?",
                        (validated_id,),
                    )
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def is_tag_favorite(self, tag_id: int) -> bool:
        validated_id = _validate_positive_id(tag_id, "PROMPT_LIBRARY_TAG_ID_INVALID")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            if connection.execute(
                "SELECT 1 FROM tags WHERE id = ?", (validated_id,)
            ).fetchone() is None:
                raise PromptLibraryValidationError("PROMPT_LIBRARY_TAG_NOT_FOUND")
            return connection.execute(
                "SELECT 1 FROM tag_preferences WHERE tag_id = ?", (validated_id,)
            ).fetchone() is not None
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def search_prompts(
        self,
        *,
        model_id: str,
        task_id: str,
        tag_ids: Iterable[int] = (),
        title: str = "",
        page: int = 1,
    ) -> PromptSearchPage:
        stored_model_id = _validate_model_id(model_id)
        stored_task_id = _validate_task_id(task_id)
        prepared_ids = self._prepare_tag_ids(tag_ids)
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise PromptLibraryValidationError("PROMPT_LIBRARY_PAGE_INVALID")
        if not isinstance(title, str):
            raise PromptLibraryValidationError("PROMPT_LIBRARY_TITLE_QUERY_INVALID")

        conditions = ["p.model_id = ?", "p.task_id = ?"]
        parameters: list[object] = [stored_model_id, stored_task_id]
        title_query = title.strip()
        if title_query:
            conditions.append("p.title LIKE ? ESCAPE '\\' COLLATE NOCASE")
            parameters.append(f"%{_escape_like(title_query)}%")
        if prepared_ids:
            placeholders = ",".join("?" for _ in prepared_ids)
            conditions.append(
                f"""p.id IN (
                    SELECT pt.prompt_id
                    FROM prompt_tags AS pt
                    WHERE pt.tag_id IN ({placeholders})
                    GROUP BY pt.prompt_id
                    HAVING COUNT(DISTINCT pt.tag_id) = ?
                )"""
            )
            parameters.extend(prepared_ids)
            parameters.append(len(prepared_ids))
        where_clause = " AND ".join(conditions)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            total_count = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM prompts AS p WHERE {where_clause}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""SELECT p.id, p.uuid, p.title, p.model_id, p.task_id,
                            p.updated_at
                     FROM prompts AS p
                     WHERE {where_clause}
                     ORDER BY p.updated_at DESC, p.id DESC
                     LIMIT ? OFFSET ?""",
                (*parameters, PROMPT_LIBRARY_PAGE_SIZE, (page - 1) * PROMPT_LIBRARY_PAGE_SIZE),
            ).fetchall()
            prompt_ids = tuple(int(row["id"]) for row in rows)
            tags_by_prompt = self._tags_for_prompt_ids(connection, prompt_ids)
            items = tuple(
                PromptSummary(
                    id=int(row["id"]),
                    uuid=str(row["uuid"]),
                    title=str(row["title"]),
                    model_id=str(row["model_id"]),
                    task_id=str(row["task_id"]),
                    updated_at=str(row["updated_at"]),
                    tags=tags_by_prompt.get(int(row["id"]), ()),
                )
                for row in rows
            )
            return PromptSearchPage(
                total_count=total_count,
                page=page,
                page_size=PROMPT_LIBRARY_PAGE_SIZE,
                items=items,
            )
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> TagCandidate:
        return TagCandidate(
            tag=PromptLibraryManager._tag_from_row(row),
            is_favorite=bool(row["is_favorite"]),
            usage_count=int(row["usage_count"]),
        )

    def list_tag_candidates(
        self,
        *,
        model_id: str,
        task_id: str,
        limit: int = INITIAL_TAG_CANDIDATE_LIMIT,
    ) -> tuple[TagCandidate, ...]:
        stored_model_id = _validate_model_id(model_id)
        stored_task_id = _validate_task_id(task_id)
        validated_limit = _validate_limit(limit, INITIAL_TAG_CANDIDATE_LIMIT)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            rows = connection.execute(
                """SELECT t.*,
                          CASE WHEN pref.tag_id IS NULL THEN 0 ELSE 1 END
                              AS is_favorite,
                          COUNT(DISTINCT p.id) AS usage_count
                   FROM tags AS t
                   JOIN prompt_tags AS pt ON pt.tag_id = t.id
                   JOIN prompts AS p ON p.id = pt.prompt_id
                   LEFT JOIN tag_preferences AS pref ON pref.tag_id = t.id
                   WHERE p.model_id = ? AND p.task_id = ?
                   GROUP BY t.id
                   ORDER BY is_favorite DESC, usage_count DESC,
                            t.normalized_name, t.id
                   LIMIT ?""",
                (stored_model_id, stored_task_id, validated_limit),
            ).fetchall()
            return tuple(self._candidate_from_row(row) for row in rows)
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def search_tag_candidates(
        self,
        *,
        model_id: str,
        task_id: str,
        query: str,
        limit: int = SEARCH_TAG_CANDIDATE_LIMIT,
    ) -> tuple[TagCandidate, ...]:
        stored_model_id = _validate_model_id(model_id)
        stored_task_id = _validate_task_id(task_id)
        validated_limit = _validate_limit(limit, SEARCH_TAG_CANDIDATE_LIMIT)
        normalized_query = _normalize_tag_query(query)
        if not normalized_query:
            return self.list_tag_candidates(
                model_id=stored_model_id,
                task_id=stored_task_id,
                limit=min(validated_limit, INITIAL_TAG_CANDIDATE_LIMIT),
            )
        escaped = _escape_like(normalized_query)
        prefix_pattern = f"{escaped}%"
        contains_pattern = f"%{escaped}%"
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            rows = connection.execute(
                """SELECT t.*,
                          CASE WHEN pref.tag_id IS NULL THEN 0 ELSE 1 END
                              AS is_favorite,
                          COUNT(DISTINCT p.id) AS usage_count,
                          CASE
                            WHEN t.normalized_name = ? THEN 0
                            WHEN t.normalized_name LIKE ? ESCAPE '\\' THEN 1
                            ELSE 2
                          END AS match_rank
                   FROM tags AS t
                   JOIN prompt_tags AS pt ON pt.tag_id = t.id
                   JOIN prompts AS p ON p.id = pt.prompt_id
                   LEFT JOIN tag_preferences AS pref ON pref.tag_id = t.id
                   WHERE p.model_id = ? AND p.task_id = ?
                     AND t.normalized_name LIKE ? ESCAPE '\\'
                   GROUP BY t.id
                   ORDER BY match_rank, is_favorite DESC, usage_count DESC,
                            t.normalized_name, t.id
                   LIMIT ?""",
                (
                    normalized_query,
                    prefix_pattern,
                    stored_model_id,
                    stored_task_id,
                    contains_pattern,
                    validated_limit,
                ),
            ).fetchall()
            return tuple(self._candidate_from_row(row) for row in rows)
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED") from exc
        finally:
            if connection is not None:
                connection.close()

    def search_existing_tags(
        self,
        query: str,
        *,
        limit: int = GLOBAL_TAG_CANDIDATE_LIMIT,
    ) -> tuple[TagCandidate, ...]:
        """Search every stored tag, including tags that are currently unused."""

        validated_limit = _validate_limit(limit, GLOBAL_TAG_CANDIDATE_LIMIT)
        normalized_query = _normalize_tag_query(query)
        escaped = _escape_like(normalized_query)
        prefix_pattern = f"{escaped}%"
        contains_pattern = f"%{escaped}%"
        connection: sqlite3.Connection | None = None
        try:
            connection = self._open_connection()
            rows = connection.execute(
                """SELECT t.*,
                          CASE WHEN pref.tag_id IS NULL THEN 0 ELSE 1 END
                              AS is_favorite,
                          COUNT(DISTINCT pt.prompt_id) AS usage_count,
                          CASE
                            WHEN t.normalized_name = ? THEN 0
                            WHEN t.normalized_name LIKE ? ESCAPE '\\' THEN 1
                            ELSE 2
                          END AS match_rank
                   FROM tags AS t
                   LEFT JOIN prompt_tags AS pt ON pt.tag_id = t.id
                   LEFT JOIN tag_preferences AS pref ON pref.tag_id = t.id
                   WHERE t.normalized_name LIKE ? ESCAPE '\\'
                   GROUP BY t.id
                   ORDER BY match_rank, is_favorite DESC, usage_count DESC,
                            t.normalized_name, t.id
                   LIMIT ?""",
                (
                    normalized_query,
                    prefix_pattern,
                    contains_pattern,
                    validated_limit,
                ),
            ).fetchall()
            return tuple(self._candidate_from_row(row) for row in rows)
        except PromptLibraryError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PromptLibraryDatabaseError(
                "PROMPT_LIBRARY_DATABASE_OPERATION_FAILED"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
