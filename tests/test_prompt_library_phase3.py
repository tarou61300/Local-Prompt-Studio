from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.prompt_library_dialog import PromptLibraryEntryDialog
from app.prompt_library_page import PromptLibraryPage
from core.localization import Localization
from core.profile_loader import ProfileLoader
from core.prompt_library_manager import (
    GLOBAL_TAG_CANDIDATE_LIMIT,
    PromptLibraryDatabaseError,
    PromptLibraryManager,
    PromptLibraryValidationError,
)
from core.renderers import RendererRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _profiles(tmp_path):
    catalog = ProfileLoader(
        PROJECT_ROOT / "profiles",
        tmp_path / "profile-data",
        RendererRegistry(),
    ).discover()
    return tuple(
        sorted(
            (*catalog.profiles.values(), *catalog.custom_profiles.values()),
            key=lambda profile: (
                profile.manifest.category,
                profile.manifest.name.casefold(),
                profile.manifest.id,
            ),
        )
    )


def _page(tmp_path, data_dir: Path, locale_id: str = "ja-JP"):
    localization = Localization(PROJECT_ROOT / "locales", locale_id)
    return PromptLibraryPage(
        localization.tr,
        data_dir=data_dir,
        profiles=_profiles(tmp_path),
    )


def _dialog(
    tmp_path,
    manager: PromptLibraryManager,
    *,
    record=None,
    locale_id: str = "ja-JP",
    model_id: str = "minimax_h3",
    task_id: str = "I2VA",
):
    localization = Localization(PROJECT_ROOT / "locales", locale_id)
    return PromptLibraryEntryDialog(
        localization.tr,
        manager=manager,
        profiles=_profiles(tmp_path),
        initial_model_id=model_id,
        initial_task_id=task_id,
        record=record,
    )


def _create(
    manager: PromptLibraryManager,
    *,
    title: str = "Saved",
    body: str = "immutable prompt",
    model_id: str = "minimax_h3",
    task_id: str = "I2VA",
    tags=(),
):
    return manager.create_prompt(
        title=title,
        model_id=model_id,
        task_id=task_id,
        prompt_text=body,
        tag_names=tags,
    )


def _connect(manager: PromptLibraryManager):
    connection = sqlite3.connect(manager.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _select_page_target(page, model_id="minimax_h3", task_id="I2VA"):
    page.model_combo.setCurrentIndex(page.model_combo.findData(model_id))
    page.task_combo.setCurrentIndex(page.task_combo.findData(task_id))


def test_global_existing_tag_search_includes_unused_and_ranks_matches(tmp_path):
    manager = PromptLibraryManager(tmp_path / "data")
    exact = manager.get_or_create_tag("Walk")
    prefix = manager.get_or_create_tag("walking")
    contains = manager.get_or_create_tag("boardwalk")
    unused = manager.get_or_create_tag("walkway")
    _create(manager, tags=("Walk", "walking", "boardwalk"))

    results = manager.search_existing_tags("ＷＡＬＫ")

    assert [item.tag.id for item in results[:4]] == [
        exact.id,
        prefix.id,
        unused.id,
        contains.id,
    ]
    assert next(item for item in results if item.tag.id == unused.id).usage_count == 0
    assert len(manager.search_existing_tags("", limit=GLOBAL_TAG_CANDIDATE_LIMIT)) <= 20


def test_normalized_existing_tag_is_reused_by_create_and_metadata_update(tmp_path):
    manager = PromptLibraryManager(tmp_path / "data")
    existing = manager.get_or_create_tag("Woman")
    created = _create(manager, tags=("ＷＯＭＡＮ",))
    updated = manager.update_metadata(
        created.id,
        title=created.title,
        tag_names=("woman", "New Tag"),
    )

    assert [tag.id for tag in created.tags] == [existing.id]
    assert existing.id in {tag.id for tag in updated.tags}
    with _connect(manager) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tags WHERE normalized_name = 'woman'"
        ).fetchone()[0] == 1


def test_metadata_new_tags_roll_back_together_and_prompt_stays_immutable(
    tmp_path, monkeypatch
):
    manager = PromptLibraryManager(tmp_path / "data")
    original = _create(manager, body="  exact\r\nbody  ", tags=("old",))
    resolve = manager._resolve_tag
    calls = 0

    def fail_second(connection, display_name, normalized_name):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("forced rollback")
        return resolve(connection, display_name, normalized_name)

    monkeypatch.setattr(manager, "_resolve_tag", fail_second)
    with pytest.raises(PromptLibraryDatabaseError):
        manager.update_metadata(
            original.id,
            title="Changed",
            tag_names=("pending-one", "pending-two"),
        )

    loaded = manager.get_prompt(original.id)
    assert loaded == original
    with _connect(manager) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tags WHERE normalized_name LIKE 'pending-%'"
        ).fetchone()[0] == 0


def test_favorite_changes_do_not_touch_prompt_updated_at(tmp_path):
    manager = PromptLibraryManager(tmp_path / "data")
    prompt = _create(manager, tags=("favorite",))
    manager.set_tag_favorite(prompt.tags[0].id, True)
    assert manager.get_prompt(prompt.id).updated_at == prompt.updated_at
    manager.set_tag_favorite(prompt.tags[0].id, False)
    assert manager.get_prompt(prompt.id).updated_at == prompt.updated_at


def test_new_dialog_inherits_target_updates_tasks_and_validates_required_fields(
    tmp_path
):
    _app()
    manager = PromptLibraryManager(tmp_path / "data")
    dialog = _dialog(tmp_path, manager, model_id="minimax_h3", task_id="Ref2VA")

    assert dialog.current_model_id() == "minimax_h3"
    assert dialog.current_task_id() == "Ref2VA"
    dialog.save_entry()
    assert dialog.result_record is None
    assert dialog.error_label.text()
    dialog.title_edit.setText("Title")
    dialog.save_entry()
    assert dialog.result_record is None
    assert dialog.error_label.text()

    assert dialog.model_combo is not None
    assert dialog.task_combo is not None
    dialog.model_combo.setCurrentIndex(dialog.model_combo.findData("wan_2_2"))
    profile = next(
        profile for profile in _profiles(tmp_path)
        if profile.manifest.id == "wan_2_2"
    )
    assert [
        dialog.task_combo.itemData(index)
        for index in range(dialog.task_combo.count())
    ] == list(profile.manifest.supported_tasks)

    page = _page(tmp_path, manager.data_dir)
    page.activate()
    _select_page_target(page, "minimax_h3", "Ref2VA")
    inherited = page.create_new_prompt_dialog()
    assert inherited is not None
    assert inherited.current_model_id() == "minimax_h3"
    assert inherited.current_task_id() == "Ref2VA"
    inherited.close()
    page.close()
    dialog.reject()


def test_new_dialog_exact_prompt_zero_existing_pending_and_normalized_tags(
    tmp_path
):
    app = _app()
    manager = PromptLibraryManager(tmp_path / "data")
    existing = manager.get_or_create_tag("Woman")
    prompt_text = "  first line\n\n日本語と🌙\nlast line  "
    dialog = _dialog(tmp_path, manager)
    dialog.title_edit.setText("  Exact prompt  ")
    dialog.prompt_edit.setPlainText(prompt_text)

    dialog.tag_editor.search_edit.setText("ＷＯＭＡＮ")
    dialog.tag_editor.add_pending_tag()
    assert dialog.tag_editor.selected_tag_ids() == (existing.id,)
    assert dialog.tag_editor.pending_tag_names() == ()
    assert "Woman" in dialog.tag_editor.guidance_label.text()
    dialog.tag_editor.search_edit.setText("公園")
    dialog.tag_editor.add_pending_tag()
    assert dialog.tag_editor.pending_tag_names() == ("公園",)
    assert dialog.tag_editor.favorite_button(existing.id) is not None

    dialog.save_entry()
    app.processEvents()
    saved = dialog.result_record
    assert saved is not None
    assert saved.title == "Exact prompt"
    assert saved.prompt_text == prompt_text
    assert {tag.normalized_name for tag in saved.tags} == {"woman", "公園"}

    zero_tag_dialog = _dialog(tmp_path, manager)
    zero_tag_dialog.title_edit.setText("No tags")
    zero_tag_dialog.prompt_edit.setPlainText("body")
    zero_tag_dialog.save_entry()
    assert zero_tag_dialog.result_record is not None
    assert zero_tag_dialog.result_record.tags == ()


def test_new_and_edit_cancel_leave_database_unchanged(tmp_path):
    _app()
    manager = PromptLibraryManager(tmp_path / "data")
    original = _create(manager, tags=("old",))
    before = manager.database_path.read_bytes()

    new_dialog = _dialog(tmp_path, manager)
    new_dialog.title_edit.setText("Cancelled")
    new_dialog.prompt_edit.setPlainText("private body")
    new_dialog.tag_editor.search_edit.setText("pending-new")
    new_dialog.tag_editor.add_pending_tag()
    new_dialog.reject()
    assert manager.database_path.read_bytes() == before

    edit_dialog = _dialog(tmp_path, manager, record=original)
    edit_dialog.title_edit.setText("Cancelled edit")
    edit_dialog.tag_editor.search_edit.setText("pending-edit")
    edit_dialog.tag_editor.add_pending_tag()
    edit_dialog.reject()
    assert manager.get_prompt(original.id) == original
    with _connect(manager) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tags WHERE normalized_name LIKE 'pending-%'"
        ).fetchone()[0] == 0


def test_edit_dialog_updates_title_tags_only_and_prompt_is_read_only(tmp_path):
    _app()
    manager = PromptLibraryManager(tmp_path / "data")
    original = _create(
        manager,
        body="  immutable\nPrompt  ",
        tags=("remove", "keep"),
    )
    by_name = {tag.normalized_name: tag for tag in original.tags}
    dialog = _dialog(tmp_path, manager, record=original)

    assert dialog.model_combo is None and dialog.task_combo is None
    assert dialog.model_value is not None and dialog.task_value is not None
    assert dialog.prompt_edit.isReadOnly()
    assert dialog.prompt_edit.toPlainText() == original.prompt_text
    dialog.title_edit.setText("  Updated  ")
    dialog.tag_editor.selected_button(by_name["remove"].id).click()
    dialog.tag_editor.search_edit.setText("added")
    dialog.tag_editor.add_pending_tag()
    dialog.save_entry()

    updated = dialog.result_record
    assert updated is not None
    assert updated.title == "Updated"
    assert {tag.normalized_name for tag in updated.tags} == {"keep", "added"}
    assert updated.uuid == original.uuid
    assert updated.model_id == original.model_id
    assert updated.task_id == original.task_id
    assert updated.created_at == original.created_at
    assert updated.prompt_text == original.prompt_text


def test_favorite_ui_keeps_query_selection_and_pending_has_no_star(tmp_path):
    app = _app()
    manager = PromptLibraryManager(tmp_path / "data")
    prompt = _create(manager, tags=("woman", "walk"))
    page = _page(tmp_path, manager.data_dir)
    page.activate()
    _select_page_target(page)
    tag = next(tag for tag in prompt.tags if tag.normalized_name == "woman")

    page.tag_selector.search_edit.setText("woman")
    page.tag_selector.candidate_button(tag.id).click()
    favorite = page.tag_selector.favorite_button(tag.id)
    assert favorite is not None and favorite.text() == "☆"
    favorite.click()
    app.processEvents()
    assert page.tag_selector.search_edit.text() == "woman"
    assert page.tag_selector.selected_tag_ids() == (tag.id,)
    assert page.tag_selector.favorite_button(tag.id).text() == "★"
    page.tag_selector.favorite_button(tag.id).click()
    assert manager.is_tag_favorite(tag.id) is False

    dialog = _dialog(tmp_path, manager)
    dialog.tag_editor.search_edit.setText("not-yet-stored")
    dialog.tag_editor.add_pending_tag()
    assert dialog.tag_editor.pending_tag_names() == ("not-yet-stored",)
    assert dialog.tag_editor.favorite_button(-1) is None


def test_delete_cancel_confirm_preserves_tag_and_clears_detail(tmp_path, monkeypatch):
    _app()
    manager = PromptLibraryManager(tmp_path / "data")
    created = _create(manager, title="Delete me", tags=("keep-tag",))
    page = _page(tmp_path, manager.data_dir)
    page.activate()
    _select_page_target(page)
    page.search()
    page.results_table.selectRow(0)
    page.show_selected_prompt()
    assert page.detail_prompt.toPlainText() == created.prompt_text

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    page.delete_selected_prompt()
    assert manager.get_prompt(created.id).id == created.id

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page.delete_selected_prompt()
    with pytest.raises(PromptLibraryValidationError):
        manager.get_prompt(created.id)
    assert page.results_model.rowCount() == 0
    assert page.detail_prompt.toPlainText() == ""
    assert page.results_model.checked_prompt_ids() == ()
    with _connect(manager) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tags WHERE normalized_name = 'keep-tag'"
        ).fetchone()[0] == 1


def test_delete_last_row_clamps_to_last_valid_page(tmp_path, monkeypatch):
    _app()
    manager = PromptLibraryManager(tmp_path / "data")
    for index in range(101):
        _create(manager, title=f"Prompt {index:03d}", body=str(index))
    page = _page(tmp_path, manager.data_dir)
    page.activate()
    _select_page_target(page)
    page.search()
    page._load_page(3)
    assert page._current_page == 3
    assert page.results_model.rowCount() == 1
    page.results_table.selectRow(0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    page.delete_selected_prompt()

    assert page._current_page == 2
    assert page._page_count == 2
    assert page.results_model.rowCount() == 50
    assert "100" in page.results_label.text()


@pytest.mark.parametrize("locale_id", ["ja-JP", "en-US"])
def test_phase3_dialog_locale_and_palette_safe_widgets(tmp_path, locale_id):
    app = _app()
    manager = PromptLibraryManager(tmp_path / locale_id / "data")
    dialog = _dialog(tmp_path / locale_id, manager, locale_id=locale_id)
    dialog.show()
    app.processEvents()
    assert dialog.windowTitle()
    assert dialog.button_box.isVisible()
    assert dialog.tag_editor.add_button.text()
    assert dialog.prompt_edit.styleSheet() == "border: 1px solid palette(mid);"
    dialog.close()
