from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from app.prompt_library_page import PromptLibraryPage
from core.localization import Localization
from core.profile_loader import ProfileLoader
from core.prompt_library_datasets import (
    DATASET_REGISTRY_FILENAME,
    PromptLibraryDatasetRegistry,
)
from core.prompt_library_manager import PromptLibraryManager
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
    return tuple((*catalog.profiles.values(), *catalog.custom_profiles.values()))


def _page(tmp_path, data_dir: Path, locale_id: str = "ja-JP"):
    localization = Localization(PROJECT_ROOT / "locales", locale_id)
    return PromptLibraryPage(
        localization.tr,
        data_dir=data_dir,
        profiles=_profiles(tmp_path),
    )


def _select_target(page: PromptLibraryPage) -> None:
    page.model_combo.setCurrentIndex(page.model_combo.findData("minimax_h3"))
    page.task_combo.setCurrentIndex(page.task_combo.findData("T2VA"))


def _create_prompt(manager: PromptLibraryManager, title: str, body: str):
    return manager.create_prompt(
        title=title,
        model_id="minimax_h3",
        task_id="T2VA",
        prompt_text=body,
        tag_names=("dataset-tag",),
    )


def test_dataset_ui_recognizes_legacy_default_and_localizes_controls(tmp_path) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    created = _create_prompt(manager, "Legacy", "legacy")
    page = _page(tmp_path, data_dir)
    page.activate()
    _select_target(page)
    page.search_button.click()
    try:
        assert page.dataset_combo.currentData() == "default"
        assert page.dataset_combo.currentText() == "Default"
        assert page.dataset_label.text() == "データセット"
        assert page.new_dataset_button.text() == "新規データセット"
        assert page.results_model.items[0].id == created.id
        assert page.manager.database_path == manager.database_path
    finally:
        page.close()
        app.processEvents()


def test_dataset_switch_clears_search_detail_checked_tags_and_keeps_target(
    tmp_path,
) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    registry = PromptLibraryDatasetRegistry(data_dir)
    dataset_a = registry.create_dataset("Dataset A")
    prompt_a = _create_prompt(PromptLibraryManager(dataset_a.data_dir), "A", "A body")
    dataset_b = registry.create_dataset("Dataset B")
    prompt_b = _create_prompt(PromptLibraryManager(dataset_b.data_dir), "B", "B body")
    registry.set_active(dataset_a.id)
    page = _page(tmp_path, data_dir)
    page.activate()
    _select_target(page)
    page.search_button.click()
    page.results_table.selectRow(0)
    page.results_model.setData(
        page.results_model.index(0, page.results_model.CHECK_COLUMN),
        Qt.CheckState.Checked,
        Qt.ItemDataRole.CheckStateRole,
    )
    tag_id = page.results_model.items[0].tags[0].id
    assert page.detail_prompt.toPlainText() == prompt_a.prompt_text
    page.tag_selector.candidate_button(tag_id).click()
    page.results_table.selectRow(0)
    assert page.detail_prompt.toPlainText() == prompt_a.prompt_text
    assert page.results_model.checked_prompt_ids() == ()
    page.results_model.setData(
        page.results_model.index(0, page.results_model.CHECK_COLUMN),
        Qt.CheckState.Checked,
        Qt.ItemDataRole.CheckStateRole,
    )
    assert page.results_model.checked_prompt_ids()

    page.dataset_combo.setCurrentIndex(page.dataset_combo.findData(dataset_b.id))

    try:
        assert page.model_combo.currentData() == "minimax_h3"
        assert page.task_combo.currentData() == "T2VA"
        assert page.tag_selector.selected_tag_ids() == ()
        assert page.title_search.text() == ""
        assert page.results_model.rowCount() == 0
        assert page.results_model.checked_prompt_ids() == ()
        assert page.detail_prompt.toPlainText() == ""
        assert PromptLibraryDatasetRegistry(data_dir).active_dataset_id == dataset_b.id
        page.search_button.click()
        assert [item.id for item in page.results_model.items] == [prompt_b.id]
    finally:
        page.close()
        app.processEvents()


def test_dataset_new_load_and_export_buttons_use_managed_independent_databases(
    tmp_path,
    monkeypatch,
) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    external_manager = PromptLibraryManager(tmp_path / "external")
    imported_prompt = _create_prompt(external_manager, "Imported", "exact imported")
    export_path = tmp_path / "exports" / "exported.sqlite3"
    names = iter((("Created", True), ("Imported", True)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: next(names))
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(external_manager.database_path), ""),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(export_path), ""),
    )
    page = _page(tmp_path, data_dir, locale_id="en-US")
    page.activate()
    assert page.dataset_label.text() == "Dataset"
    assert page.load_dataset_button.text() == "Load dataset"

    page.new_dataset_button.click()
    created_id = str(page.dataset_combo.currentData())
    assert created_id != "default"
    _create_prompt(page.manager, "Created prompt", "created body")

    page.load_dataset_button.click()
    imported_id = str(page.dataset_combo.currentData())
    assert imported_id not in {"default", created_id}
    _select_target(page)
    page.search_button.click()
    assert [item.id for item in page.results_model.items] == [imported_prompt.id]

    page.export_dataset_button.click()

    try:
        assert export_path.is_file()
        exported = PromptLibraryDatasetRegistry(data_dir).active_dataset()
        assert exported.id == imported_id
        copied = PromptLibraryManager(export_path.parent / "managed-copy")
        copied.database_path.write_bytes(export_path.read_bytes())
        assert copied.get_prompt(imported_prompt.id).prompt_text == "exact imported"
    finally:
        page.close()
        app.processEvents()


def test_invalid_dataset_load_is_page_local_and_does_not_replace_manager(
    tmp_path,
    monkeypatch,
) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    invalid = tmp_path / "invalid.sqlite3"
    invalid.write_bytes(b"not sqlite")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(invalid), ""),
    )
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *args, **kwargs: ("Invalid", True),
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )
    page = _page(tmp_path, data_dir)
    page.activate()
    original_path = page.manager.database_path

    page.load_dataset_button.click()

    try:
        assert page.manager.database_path == original_path
        assert page.dataset_combo.count() == 1
        assert warnings == [page.tr("library.dataset.error.invalid")]
        assert page.isEnabled()
    finally:
        page.close()
        app.processEvents()


def test_corrupt_registry_disables_only_dataset_controls_and_keeps_default(
    tmp_path,
) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    _create_prompt(manager, "Keep", "keep")
    (data_dir / DATASET_REGISTRY_FILENAME).write_text("not-json", encoding="utf-8")
    page = _page(tmp_path, data_dir)
    page.activate()
    try:
        assert page.feedback_label.text() == page.tr("library.dataset.error.invalid")
        _select_target(page)
        page.search_button.click()
        assert page.manager.database_path == manager.database_path
        assert not page.dataset_combo.isEnabled()
        assert not page.new_dataset_button.isEnabled()
        assert page.results_model.rowCount() == 1
    finally:
        page.close()
        app.processEvents()
