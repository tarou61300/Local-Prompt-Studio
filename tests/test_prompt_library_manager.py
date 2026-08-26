from __future__ import annotations

import inspect
import sqlite3
import uuid
from dataclasses import fields

import pytest

import core.prompt_library_manager as library_module
from core.prompt_library_manager import (
    INITIAL_TAG_CANDIDATE_LIMIT,
    PROMPT_LIBRARY_DATABASE_NAME,
    PROMPT_LIBRARY_PAGE_SIZE,
    PROMPT_LIBRARY_SCHEMA_VERSION,
    PromptLibraryDatabaseError,
    PromptLibraryManager,
    PromptLibrarySchemaError,
    PromptLibraryValidationError,
    PromptSummary,
)


MODEL_ID = "minimax_h3"
TASK_ID = "I2VA"


def _manager(tmp_path) -> PromptLibraryManager:
    return PromptLibraryManager(tmp_path / "portable-data")


def _create_prompt(
    manager: PromptLibraryManager,
    *,
    title: str = "Prompt",
    model_id: str = MODEL_ID,
    task_id: str = TASK_ID,
    prompt_text: str = "Prompt body",
    tag_names=(),
    tag_ids=(),
):
    return manager.create_prompt(
        title=title,
        model_id=model_id,
        task_id=task_id,
        prompt_text=prompt_text,
        tag_names=tag_names,
        tag_ids=tag_ids,
    )


def _connect(manager: PromptLibraryManager) -> sqlite3.Connection:
    connection = sqlite3.connect(manager.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _assert_canonical_uuid4(value: str) -> None:
    parsed = uuid.UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value
    assert value == value.lower()


def test_database_path_and_initial_schema(tmp_path) -> None:
    manager = _manager(tmp_path)

    assert manager.database_path == (
        tmp_path / "portable-data" / PROMPT_LIBRARY_DATABASE_NAME
    )
    assert manager.database_path.is_file()
    with _connect(manager) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "prompts",
            "tag_categories",
            "tags",
            "prompt_tags",
            "tag_preferences",
        } <= tables
        indexes = {
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex%'"""
            )
        }
        assert indexes == {
            "idx_prompts_target_order",
            "idx_prompts_target_title",
            "idx_prompt_tags_tag_prompt",
            "idx_tags_category",
        }


def test_schema_version_constant_is_one() -> None:
    assert PROMPT_LIBRARY_SCHEMA_VERSION == 1


def test_initial_schema_creation_is_atomic(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "broken-schema"
    monkeypatch.setattr(
        library_module,
        "_SCHEMA_STATEMENTS",
        (*library_module._SCHEMA_STATEMENTS, "CREATE TABLE invalid syntax"),
    )

    with pytest.raises(PromptLibraryDatabaseError):
        PromptLibraryManager(data_dir)

    with sqlite3.connect(data_dir / PROMPT_LIBRARY_DATABASE_NAME) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'prompts'"
        ).fetchone() is None


def test_reopen_version_one_database_preserves_data(tmp_path) -> None:
    manager = _manager(tmp_path)
    created = _create_prompt(manager, title="Keep me")

    reopened = PromptLibraryManager(manager.data_dir)

    assert reopened.get_prompt(created.id) == created


def test_newer_schema_is_rejected_without_modification(tmp_path) -> None:
    data_dir = tmp_path / "newer"
    data_dir.mkdir()
    database_path = data_dir / PROMPT_LIBRARY_DATABASE_NAME
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel(value) VALUES ('preserve')")
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(PromptLibrarySchemaError) as error:
        PromptLibraryManager(data_dir)

    assert error.value.found_version == 99
    assert error.value.supported_version == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == "preserve"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'prompts'"
        ).fetchone() is None


def test_every_manager_connection_enables_foreign_keys(tmp_path) -> None:
    manager = _manager(tmp_path)
    connection = manager._open_connection()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO prompt_tags(prompt_id, tag_id) VALUES (999, 999)"
            )
    finally:
        connection.close()


def test_create_transaction_rolls_back_tags_and_prompt(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path)
    original = manager._resolve_tag
    call_count = 0

    def fail_on_second_tag(connection, display_name, normalized_name):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise sqlite3.OperationalError("forced rollback")
        return original(connection, display_name, normalized_name)

    monkeypatch.setattr(manager, "_resolve_tag", fail_on_second_tag)

    with pytest.raises(PromptLibraryDatabaseError):
        _create_prompt(manager, tag_names=("first", "second"))

    with _connect(manager) as connection:
        assert connection.execute("SELECT COUNT(*) FROM prompts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM prompt_tags").fetchone()[0] == 0


def test_create_and_get_prompt_with_uuid_and_tags(tmp_path) -> None:
    manager = _manager(tmp_path)

    created = _create_prompt(
        manager,
        title="  Example  ",
        task_id="Ref2VA",
        tag_names=("Woman", "park"),
    )
    loaded = manager.get_prompt(created.id)

    assert loaded == created
    assert loaded.title == "Example"
    assert loaded.task_id == "Ref2VA"
    assert {tag.normalized_name for tag in loaded.tags} == {"woman", "park"}
    _assert_canonical_uuid4(loaded.uuid)
    for tag in loaded.tags:
        _assert_canonical_uuid4(tag.uuid)


def test_prompt_text_is_stored_byte_for_byte(tmp_path) -> None:
    manager = _manager(tmp_path)
    prompt_text = "  先頭空白\r\n\r\n日本語とéと🌙\n末尾空白  "

    loaded = manager.get_prompt(
        _create_prompt(manager, prompt_text=prompt_text).id
    )

    assert loaded.prompt_text == prompt_text
    assert loaded.prompt_text.encode("utf-8") == prompt_text.encode("utf-8")


def test_duplicate_titles_are_allowed(tmp_path) -> None:
    manager = _manager(tmp_path)

    first = _create_prompt(manager, title="Same")
    second = _create_prompt(manager, title=" Same ")

    assert first.id != second.id
    assert first.title == second.title == "Same"


@pytest.mark.parametrize("title", ["", " ", "\r\n"])
def test_empty_title_is_rejected(tmp_path, title) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(PromptLibraryValidationError) as error:
        _create_prompt(manager, title=title)

    assert error.value.code == "PROMPT_LIBRARY_TITLE_EMPTY"


@pytest.mark.parametrize(
    "model_id",
    [
        "",
        "MiniMax H3",
        "-custom",
        "custom-profile",
        "a",
        " minimax_h3",
    ],
)
def test_invalid_model_id_is_rejected(tmp_path, model_id) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(PromptLibraryValidationError) as error:
        _create_prompt(manager, model_id=model_id)

    assert error.value.code == "PROMPT_LIBRARY_MODEL_INVALID"


@pytest.mark.parametrize(
    "task_id", ["", "I2V A", "-I2VA", "i", "T2V-A", " Ref2VA "]
)
def test_invalid_task_id_is_rejected(tmp_path, task_id) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(PromptLibraryValidationError) as error:
        _create_prompt(manager, task_id=task_id)

    assert error.value.code == "PROMPT_LIBRARY_TASK_INVALID"


@pytest.mark.parametrize("prompt_text", ["", " ", "\r\n\t"])
def test_empty_prompt_is_rejected_without_leaking_content(tmp_path, prompt_text) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(PromptLibraryValidationError) as error:
        _create_prompt(manager, prompt_text=prompt_text)

    assert str(error.value) == "PROMPT_LIBRARY_PROMPT_EMPTY"


def test_update_metadata_changes_only_title_tags_and_updated_at(
    tmp_path, monkeypatch
) -> None:
    counter = iter(
        [
            "2026-08-26T00:00:00.000001+00:00",
            "2026-08-26T00:00:00.000002+00:00",
            "2026-08-26T00:00:00.000003+00:00",
            "2026-08-26T00:00:00.000004+00:00",
        ]
    )
    monkeypatch.setattr(library_module, "_utc_now", lambda: next(counter))
    manager = _manager(tmp_path)
    original = _create_prompt(
        manager,
        title="Old",
        prompt_text="  immutable body  ",
        tag_names=("old-tag",),
    )

    updated = manager.update_metadata(
        original.id,
        title="  New  ",
        tag_names=("new-tag",),
    )

    assert updated.title == "New"
    assert [tag.normalized_name for tag in updated.tags] == ["new-tag"]
    assert updated.updated_at != original.updated_at
    assert updated.created_at == original.created_at
    assert updated.uuid == original.uuid
    assert updated.model_id == original.model_id
    assert updated.task_id == original.task_id
    assert updated.prompt_text == original.prompt_text


def test_update_metadata_api_cannot_accept_content_or_target_fields() -> None:
    parameters = inspect.signature(PromptLibraryManager.update_metadata).parameters

    assert {"title", "tag_names", "tag_ids"} <= set(parameters)
    assert "prompt_text" not in parameters
    assert "model_id" not in parameters
    assert "task_id" not in parameters
    assert "uuid" not in parameters


def test_delete_prompt_cascades_links_but_preserves_tags(tmp_path) -> None:
    manager = _manager(tmp_path)
    created = _create_prompt(manager, tag_names=("keep-tag",))
    tag = created.tags[0]

    manager.delete_prompt(created.id)

    with pytest.raises(PromptLibraryValidationError) as error:
        manager.get_prompt(created.id)
    assert error.value.code == "PROMPT_LIBRARY_PROMPT_NOT_FOUND"
    with _connect(manager) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM prompt_tags WHERE prompt_id = ?", (created.id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT normalized_name FROM tags WHERE id = ?", (tag.id,)
        ).fetchone()[0] == "keep-tag"


def test_tag_create_and_duplicate_normalization(tmp_path) -> None:
    manager = _manager(tmp_path)

    full_width = manager.get_or_create_tag("  Ｗｏｍａｎ  ")
    ascii_case = manager.get_or_create_tag("woman")
    upper_case = manager.get_or_create_tag("WOMAN")

    assert full_width.id == ascii_case.id == upper_case.id
    assert full_width.name == "Ｗｏｍａｎ"
    assert full_width.normalized_name == "woman"


def test_tag_whitespace_collapses_only_for_comparison(tmp_path) -> None:
    manager = _manager(tmp_path)

    first = manager.get_or_create_tag("  Silver   Hair  ")
    second = manager.get_or_create_tag("silver hair")

    assert first.id == second.id
    assert first.name == "Silver   Hair"
    assert first.normalized_name == "silver hair"


def test_semantically_similar_tags_remain_distinct(tmp_path) -> None:
    manager = _manager(tmp_path)

    tags = [
        manager.get_or_create_tag(name)
        for name in ("女性", "女", "woman", "female")
    ]

    assert len({tag.id for tag in tags}) == 4


@pytest.mark.parametrize(
    "name", ["", " ", "line\nbreak", "nul\x00value", "tab\tvalue", "bidi\u202evalue"]
)
def test_invalid_tag_names_are_rejected(tmp_path, name) -> None:
    manager = _manager(tmp_path)

    with pytest.raises(PromptLibraryValidationError):
        manager.get_or_create_tag(name)


def test_unused_tag_survives_metadata_update_and_prompt_delete(tmp_path) -> None:
    manager = _manager(tmp_path)
    created = _create_prompt(manager, tag_names=("reusable",))
    tag_id = created.tags[0].id

    manager.update_metadata(created.id, title=created.title, tag_names=())
    manager.delete_prompt(created.id)

    with _connect(manager) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()[0] == 1


def test_favorite_on_and_off_uses_separate_preference_row(tmp_path) -> None:
    manager = _manager(tmp_path)
    tag = manager.get_or_create_tag("favorite")

    assert manager.is_tag_favorite(tag.id) is False
    manager.set_tag_favorite(tag.id, True)
    assert manager.is_tag_favorite(tag.id) is True
    manager.set_tag_favorite(tag.id, False)
    assert manager.is_tag_favorite(tag.id) is False

    with _connect(manager) as connection:
        tag_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tags)")
        }
        preference_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tag_preferences)")
        }
        assert "is_favorite" not in tag_columns
        assert "is_favorite" not in preference_columns
        assert connection.execute(
            "SELECT COUNT(*) FROM tag_preferences WHERE tag_id = ?", (tag.id,)
        ).fetchone()[0] == 0


def test_search_filters_model_and_task_with_zero_tags(tmp_path) -> None:
    manager = _manager(tmp_path)
    expected = _create_prompt(manager, title="Expected")
    _create_prompt(manager, title="Other model", model_id="wan_2_2")
    _create_prompt(manager, title="Other task", task_id="T2VA")

    page = manager.search_prompts(model_id=MODEL_ID, task_id=TASK_ID)

    assert page.total_count == 1
    assert [item.id for item in page.items] == [expected.id]


def test_search_one_tag_and_multiple_tags_use_and_semantics(tmp_path) -> None:
    manager = _manager(tmp_path)
    both = _create_prompt(manager, title="Both", tag_names=("woman", "park"))
    woman_only = _create_prompt(manager, title="Woman", tag_names=("woman",))
    park_only = _create_prompt(manager, title="Park", tag_names=("park",))
    woman_id = next(tag.id for tag in both.tags if tag.normalized_name == "woman")
    park_id = next(tag.id for tag in both.tags if tag.normalized_name == "park")

    one = manager.search_prompts(
        model_id=MODEL_ID, task_id=TASK_ID, tag_ids=(woman_id,)
    )
    all_selected = manager.search_prompts(
        model_id=MODEL_ID, task_id=TASK_ID, tag_ids=(woman_id, park_id)
    )

    assert {item.id for item in one.items} == {both.id, woman_only.id}
    assert [item.id for item in all_selected.items] == [both.id]
    assert park_only.id not in {item.id for item in all_selected.items}


def test_title_search_is_partial_and_escapes_wildcards(tmp_path) -> None:
    manager = _manager(tmp_path)
    literal = _create_prompt(manager, title=r"100%_Real\Prompt")
    _create_prompt(manager, title="100xxRealPrompt")

    partial = manager.search_prompts(
        model_id=MODEL_ID, task_id=TASK_ID, title="real"
    )
    escaped = manager.search_prompts(
        model_id=MODEL_ID, task_id=TASK_ID, title="%_Real\\"
    )

    assert partial.total_count == 2
    assert [item.id for item in escaped.items] == [literal.id]


def test_pagination_is_fixed_at_50_with_total_and_second_page(tmp_path) -> None:
    manager = _manager(tmp_path)
    for index in range(55):
        _create_prompt(manager, title=f"Prompt {index:02d}")

    first = manager.search_prompts(model_id=MODEL_ID, task_id=TASK_ID, page=1)
    second = manager.search_prompts(model_id=MODEL_ID, task_id=TASK_ID, page=2)

    assert PROMPT_LIBRARY_PAGE_SIZE == 50
    assert first.page_size == second.page_size == 50
    assert first.total_count == second.total_count == 55
    assert len(first.items) == 50
    assert len(second.items) == 5
    assert {item.id for item in first.items}.isdisjoint(
        {item.id for item in second.items}
    )


def test_search_order_is_stable_when_timestamps_match(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        library_module,
        "_utc_now",
        lambda: "2026-08-26T00:00:00.000000+00:00",
    )
    manager = _manager(tmp_path)
    created = [_create_prompt(manager, title=str(index)) for index in range(3)]

    page = manager.search_prompts(model_id=MODEL_ID, task_id=TASK_ID)

    assert [item.id for item in page.items] == [item.id for item in reversed(created)]


def test_search_summaries_exclude_prompt_text_and_batch_load_tags(
    tmp_path, monkeypatch
) -> None:
    manager = _manager(tmp_path)
    for index in range(3):
        _create_prompt(
            manager,
            title=f"Prompt {index}",
            prompt_text=f"private prompt {index}",
            tag_names=(f"tag-{index}",),
        )
    statements: list[str] = []
    original_open = manager._open_connection

    def traced_open():
        connection = original_open()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(manager, "_open_connection", traced_open)

    page = manager.search_prompts(model_id=MODEL_ID, task_id=TASK_ID)

    assert "prompt_text" not in {field.name for field in fields(PromptSummary)}
    assert all(item.tags for item in page.items)
    select_statements = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(select_statements) == 3
    assert not any("private prompt" in statement for statement in statements)


def test_initial_tag_candidates_are_target_scoped_and_ranked(tmp_path) -> None:
    manager = _manager(tmp_path)
    first = _create_prompt(manager, tag_names=("common", "favorite", "plain"))
    _create_prompt(manager, tag_names=("common",))
    _create_prompt(manager, model_id="wan_2_2", tag_names=("other-target",))
    favorite_id = next(
        tag.id for tag in first.tags if tag.normalized_name == "favorite"
    )
    manager.set_tag_favorite(favorite_id, True)

    candidates = manager.list_tag_candidates(model_id=MODEL_ID, task_id=TASK_ID)

    assert [candidate.tag.normalized_name for candidate in candidates] == [
        "favorite",
        "common",
        "plain",
    ]
    assert [candidate.usage_count for candidate in candidates] == [1, 2, 1]
    assert candidates[0].is_favorite is True
    assert "other-target" not in {
        candidate.tag.normalized_name for candidate in candidates
    }


def test_initial_candidate_limit_and_search_reach_tags_outside_it(tmp_path) -> None:
    manager = _manager(tmp_path)
    tag_names = tuple(f"tag{index:03d}" for index in range(101))
    _create_prompt(manager, tag_names=tag_names)

    initial = manager.list_tag_candidates(model_id=MODEL_ID, task_id=TASK_ID)
    searched = manager.search_tag_candidates(
        model_id=MODEL_ID,
        task_id=TASK_ID,
        query="ＴＡＧ１００",
    )

    assert INITIAL_TAG_CANDIDATE_LIMIT == 100
    assert len(initial) == 100
    assert "tag100" not in {item.tag.normalized_name for item in initial}
    assert [item.tag.normalized_name for item in searched] == ["tag100"]


def test_tag_candidate_search_ranks_exact_prefix_then_contains(tmp_path) -> None:
    manager = _manager(tmp_path)
    _create_prompt(manager, tag_names=("cat", "catalog", "bobcat"))

    candidates = manager.search_tag_candidates(
        model_id=MODEL_ID,
        task_id=TASK_ID,
        query="CAT",
    )

    assert [candidate.tag.normalized_name for candidate in candidates] == [
        "cat",
        "catalog",
        "bobcat",
    ]


def test_category_is_schema_only_and_nullable_on_tags(tmp_path) -> None:
    manager = _manager(tmp_path)

    assert not hasattr(manager, "create_category")
    with _connect(manager) as connection:
        category_column = next(
            row
            for row in connection.execute("PRAGMA table_info(tags)")
            if row[1] == "category_id"
        )
        assert category_column[3] == 0
