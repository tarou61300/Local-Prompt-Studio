from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QFrame

from app.main_window import MainWindow
from app.prompt_library_page import PromptLibraryPage
from app.settings_dialog import SettingsDialog
from app.theme import apply_application_theme
from core.config_manager import AppConfig, ConfigManager, THEME_DARK, THEME_NORMAL
from core.localization import Localization
from core.profile_loader import ProfileLoader
from core.prompt_library_manager import (
    PROMPT_LIBRARY_DATABASE_NAME,
    PromptLibraryDatabaseError,
    PromptLibraryManager,
    PromptSummary,
)
from core.renderers import RendererRegistry
from mock_server import start_mock_server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _profiles(tmp_path):
    catalog = ProfileLoader(
        PROJECT_ROOT / "profiles",
        tmp_path / "profile-data",
        RendererRegistry(),
    ).discover()
    profiles = (*catalog.profiles.values(), *catalog.custom_profiles.values())
    return tuple(
        sorted(
            profiles,
            key=lambda profile: (
                profile.manifest.category,
                profile.manifest.name.casefold(),
                profile.manifest.id,
            ),
        )
    )


def _page(
    tmp_path,
    *,
    locale_id: str = "ja-JP",
    data_dir: Path | None = None,
    manager_factory=PromptLibraryManager,
    tag_rows: int = 3,
    result_rows: int = 3,
    detail_minimum_lines: int = 10,
) -> PromptLibraryPage:
    localization = Localization(PROJECT_ROOT / "locales", locale_id)
    page = PromptLibraryPage(
        localization.tr,
        data_dir=data_dir or (tmp_path / "library-data"),
        profiles=_profiles(tmp_path),
        manager_factory=manager_factory,
        tag_rows=tag_rows,
        result_rows=result_rows,
        detail_minimum_lines=detail_minimum_lines,
    )
    return page


def _select_target(page: PromptLibraryPage, model_id: str, task_id: str) -> None:
    model_index = page.model_combo.findData(model_id)
    assert model_index >= 0
    page.model_combo.setCurrentIndex(model_index)
    task_index = page.task_combo.findData(task_id)
    assert task_index >= 0
    page.task_combo.setCurrentIndex(task_index)


def _create_prompt(
    manager: PromptLibraryManager,
    *,
    title: str,
    body: str,
    model_id: str = "minimax_h3",
    task_id: str = "T2VA",
    tags=(),
):
    return manager.create_prompt(
        title=title,
        model_id=model_id,
        task_id=task_id,
        prompt_text=body,
        tag_names=tags,
    )


def test_third_tab_indices_and_prompt_library_database_are_lazy(tmp_path) -> None:
    app = _app()
    server, url = start_mock_server()
    data_dir = tmp_path / "portable-data"
    database_path = data_dir / PROMPT_LIBRARY_DATABASE_NAME
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=ConfigManager(data_dir),
        server_url=url,
        dev_skill_path=SKILL_FIXTURE,
    )
    try:
        assert window.main_tabs.count() == 3
        assert window.main_tabs.currentIndex() == 0
        assert window.main_tabs.currentWidget() is window.prompt_page
        assert window.main_tabs.widget(1) is window.chat_page
        assert window.main_tabs.widget(2) is window.prompt_library_page
        assert window.main_tabs.tabText(1) == window.tr("tabs.ai_chat")
        assert window.main_tabs.tabText(2) == window.tr("tabs.prompt_library")
        assert not database_path.exists()

        window.main_tabs.setCurrentIndex(1)
        app.processEvents()
        assert not database_path.exists()

        window.main_tabs.setCurrentIndex(2)
        app.processEvents()
        assert database_path.is_file()
        assert window.prompt_library_page.manager is not None

        window.main_tabs.setCurrentIndex(0)
        assert window.main_tabs.currentWidget() is window.prompt_page
    finally:
        window.close()
        app.processEvents()
        server.shutdown()
        server.server_close()


def test_model_ids_tasks_and_target_changes_clear_tag_selection(tmp_path) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    created = _create_prompt(
        manager,
        title="Tagged",
        body="body",
        tags=("woman",),
    )
    page = _page(tmp_path, data_dir=data_dir)
    page.activate()
    _select_target(page, "minimax_h3", "T2VA")

    expected_profiles = _profiles(tmp_path)
    assert [
        page.model_combo.itemData(index)
        for index in range(page.model_combo.count())
    ] == [profile.manifest.id for profile in expected_profiles]
    assert [
        page.model_combo.itemText(index)
        for index in range(page.model_combo.count())
    ] == [profile.manifest.name for profile in expected_profiles]

    button = page.tag_selector.candidate_button(created.tags[0].id)
    assert button is not None
    button.click()
    assert page.tag_selector.selected_tag_ids() == (created.tags[0].id,)

    alternate_task = "I2VA"
    page.task_combo.setCurrentIndex(page.task_combo.findData(alternate_task))
    assert page.task_combo.currentData() == alternate_task
    assert page.tag_selector.selected_tag_ids() == ()

    wan_index = page.model_combo.findData("wan_2_2")
    page.model_combo.setCurrentIndex(wan_index)
    wan_profile = next(
        profile for profile in expected_profiles if profile.manifest.id == "wan_2_2"
    )
    assert [
        page.task_combo.itemData(index)
        for index in range(page.task_combo.count())
    ] == list(wan_profile.manifest.supported_tasks)
    assert page.tag_selector.selected_tag_ids() == ()
    assert page.results_model.rowCount() == 0
    page.close()
    app.processEvents()


def test_tag_candidates_favorites_toggle_and_full_database_search(tmp_path) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    names = tuple(f"tag{index:03d}" for index in range(101))
    created = _create_prompt(
        manager,
        title="Many tags",
        body="body",
        tags=names,
    )
    by_name = {tag.normalized_name: tag for tag in created.tags}
    manager.set_tag_favorite(by_name["tag000"].id, True)
    page = _page(tmp_path, data_dir=data_dir)
    page.activate()
    _select_target(page, "minimax_h3", "T2VA")

    assert page.tag_selector.candidate_count() == 100
    assert page.tag_selector.favorite_label.isHidden() is False
    favorite_button = page.tag_selector.candidate_button(by_name["tag000"].id)
    assert favorite_button is not None
    assert favorite_button.text() == "tag000"
    favorite_toggle = page.tag_selector.favorite_button(by_name["tag000"].id)
    assert favorite_toggle is not None
    assert favorite_toggle.text() == "★"
    assert isinstance(favorite_button.parentWidget(), QFrame)
    assert favorite_toggle.parentWidget() is favorite_button.parentWidget()
    assert favorite_button.property("promptLibraryTagSelection") is True
    assert favorite_toggle.property("promptLibraryFavorite") is True
    assert page.tag_selector.candidate_button(by_name["tag100"].id) is None

    page.tag_selector.search_edit.setText("ＴＡＧ１００")
    app.processEvents()
    outside_button = page.tag_selector.candidate_button(by_name["tag100"].id)
    assert outside_button is not None
    assert page.tag_selector.candidate_count() == 1
    outside_button.click()
    assert page.tag_selector.selected_tag_ids() == (by_name["tag100"].id,)
    assert page.tag_selector.selected_button(by_name["tag100"].id) is not None

    page.tag_selector.search_edit.setText("tag000")
    app.processEvents()
    assert page.tag_selector.selected_tag_ids() == (by_name["tag100"].id,)
    page.tag_selector.selected_button(by_name["tag100"].id).click()
    assert page.tag_selector.selected_tag_ids() == ()

    page.tag_selector.search_edit.clear()
    app.processEvents()
    assert page.tag_selector.candidate_count() == 100
    page.close()


def test_tag_selection_auto_searches_once_and_zero_tags_requires_manual_search(
    tmp_path,
    monkeypatch,
) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    both = _create_prompt(
        manager,
        title="Rainy Park",
        body="both",
        tags=("woman", "park"),
    )
    woman_only = _create_prompt(
        manager,
        title="Rainy Street",
        body="woman",
        tags=("woman",),
    )
    _create_prompt(
        manager,
        title="Other task",
        body="other",
        task_id="I2VA",
        tags=("woman", "park"),
    )
    tags = {tag.normalized_name: tag for tag in both.tags}
    search_calls: list[dict[str, object]] = []
    original_search = manager.search_prompts

    def counted_search(**kwargs):
        search_calls.append(dict(kwargs))
        return original_search(**kwargs)

    monkeypatch.setattr(manager, "search_prompts", counted_search)
    page = _page(
        tmp_path,
        data_dir=data_dir,
        manager_factory=lambda _data_dir: manager,
    )
    page.activate()
    _select_target(page, "minimax_h3", "T2VA")

    assert page.results_model.rowCount() == 0
    assert search_calls == []
    page.title_search.setText("Street")
    page.tag_selector.candidate_button(tags["woman"].id).click()
    assert [item.id for item in page.results_model.items] == [woman_only.id]
    assert len(search_calls) == 1
    assert search_calls[-1]["title"] == "Street"
    assert search_calls[-1]["page"] == 1

    page.title_search.clear()
    page.tag_selector.candidate_button(tags["park"].id).click()
    assert [item.id for item in page.results_model.items] == [both.id]
    assert len(search_calls) == 2
    assert search_calls[-1]["tag_ids"] == (
        tags["woman"].id,
        tags["park"].id,
    )

    page.tag_selector.candidate_button(tags["park"].id).click()
    assert {item.id for item in page.results_model.items} == {
        both.id,
        woman_only.id,
    }
    assert len(search_calls) == 3

    favorite_button = page.tag_selector.favorite_button(tags["woman"].id)
    assert favorite_button is not None
    assert favorite_button.text() == "☆"
    selected_before_favorite = page.tag_selector.selected_tag_ids()
    favorite_button.click()
    assert len(search_calls) == 3
    assert page.tag_selector.selected_tag_ids() == selected_before_favorite
    refreshed_favorite = page.tag_selector.favorite_button(tags["woman"].id)
    refreshed_tag = page.tag_selector.candidate_button(tags["woman"].id)
    assert refreshed_favorite is not None
    assert refreshed_favorite.text() == "★"
    assert refreshed_tag is not None
    assert refreshed_tag.isChecked()

    page.tag_selector.candidate_button(tags["woman"].id).click()
    assert page.tag_selector.selected_tag_ids() == ()
    assert page.results_model.rowCount() == 0
    assert len(search_calls) == 3

    page.search_button.click()
    assert page.results_model.rowCount() == 2
    assert len(search_calls) == 4
    page.close()
    app.processEvents()

def test_results_are_bounded_paginated_and_page_change_clears_state(tmp_path) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    for index in range(55):
        _create_prompt(
            manager,
            title=f"Prompt {index:02d}",
            body=f"body {index}",
            tags=("batch",),
        )
    page = _page(tmp_path, data_dir=data_dir)
    page.activate()
    _select_target(page, "minimax_h3", "T2VA")
    page.search_button.click()

    assert page.results_model.rowCount() == 50
    assert "55" in page.results_label.text()
    assert page.page_label.text().endswith("1 / 2")
    assert page.next_button.isEnabled()
    assert "prompt_text" not in {field.name for field in fields(PromptSummary)}

    first_check = page.results_model.index(0, page.results_model.CHECK_COLUMN)
    page.results_model.setData(
        first_check,
        Qt.CheckState.Checked,
        Qt.ItemDataRole.CheckStateRole,
    )
    page.results_table.selectRow(0)
    page.show_button.click()
    assert page.results_model.checked_prompt_ids()
    assert page.detail_prompt.toPlainText()

    page.next_button.click()
    assert page.results_model.rowCount() == 5
    assert page.page_label.text().endswith("2 / 2")
    assert page.results_model.checked_prompt_ids() == ()
    assert not page.results_table.currentIndex().isValid()
    assert page.detail_prompt.toPlainText() == ""
    assert page.previous_button.isEnabled()

    page.previous_button.click()
    assert page.results_model.rowCount() == 50
    assert page.page_label.text().endswith("1 / 2")
    page.close()
    app.processEvents()


def test_detail_and_single_multiple_clipboard_copy_load_exact_bodies_lazily(
    tmp_path,
    monkeypatch,
) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    first = _create_prompt(
        manager,
        title="TITLE MUST NOT BE COPIED A",
        body="  first\n\n日本語🌙  ",
        tags=("one",),
    )
    second = _create_prompt(
        manager,
        title="TITLE MUST NOT BE COPIED B",
        body="second\nbody",
        tags=("two",),
    )
    get_calls: list[int] = []
    original_get = manager.get_prompt

    def counted_get(prompt_id: int):
        get_calls.append(prompt_id)
        return original_get(prompt_id)

    monkeypatch.setattr(manager, "get_prompt", counted_get)
    page = _page(
        tmp_path,
        data_dir=data_dir,
        manager_factory=lambda _data_dir: manager,
    )
    page.activate()
    _select_target(page, "minimax_h3", "T2VA")
    page.search_button.click()
    assert get_calls == []

    page.results_table.selectRow(0)
    selected = page.results_model.items[0]
    expected = {first.id: first.prompt_text, second.id: second.prompt_text}
    assert get_calls == [selected.id]
    assert page.detail_prompt.isReadOnly()
    assert page.detail_prompt.toPlainText() == expected[selected.id]

    page.results_table.selectRow(1)
    second_selected = page.results_model.items[1]
    assert get_calls == [selected.id, second_selected.id]
    assert page.detail_prompt.toPlainText() == expected[second_selected.id]

    page.copy_button.click()
    assert QApplication.clipboard().text() == expected[second_selected.id]
    assert "TITLE MUST NOT BE COPIED" not in QApplication.clipboard().text()

    for row in range(2):
        page.results_model.setData(
            page.results_model.index(row, page.results_model.CHECK_COLUMN),
            Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole,
        )
    display_order = [item.id for item in page.results_model.items]
    page.copy_checked_button.click()
    assert QApplication.clipboard().text() == "\n\n---\n\n".join(
        expected[prompt_id] for prompt_id in display_order
    )
    assert "TITLE MUST NOT BE COPIED" not in QApplication.clipboard().text()
    page.close()
    app.processEvents()


def test_empty_no_match_and_database_error_are_page_local(tmp_path) -> None:
    app = _app()
    empty_page = _page(tmp_path, data_dir=tmp_path / "empty")
    empty_page.activate()
    assert empty_page.state_label.text() == empty_page.tr("library.no_prompts")
    _select_target(empty_page, "minimax_h3", "T2VA")
    empty_page.search_button.click()
    assert empty_page.results_model.rowCount() == 0
    assert empty_page.state_label.text() == empty_page.tr("library.no_prompts")

    existing_dir = tmp_path / "existing"
    existing_manager = PromptLibraryManager(existing_dir)
    _create_prompt(
        existing_manager,
        title="Existing",
        body="body",
        task_id="T2VA",
    )
    no_match_page = _page(tmp_path, data_dir=existing_dir)
    no_match_page.activate()
    _select_target(no_match_page, "minimax_h3", "I2VA")
    no_match_page.search_button.click()
    assert no_match_page.results_model.rowCount() == 0
    assert no_match_page.state_label.text() == no_match_page.tr("library.no_matches")

    def fail_manager(_data_dir):
        raise PromptLibraryDatabaseError("PROMPT_LIBRARY_DATABASE_OPERATION_FAILED")

    failed_page = _page(tmp_path, manager_factory=fail_manager)
    failed_page.activate()
    assert failed_page.manager is None
    assert failed_page.state_label.text() == failed_page.tr("library.database_error")
    assert failed_page.isEnabled()
    empty_page.close()
    no_match_page.close()
    failed_page.close()
    app.processEvents()


def test_prompt_library_locale_and_normal_dark_theme_follow_application(tmp_path) -> None:
    app = _app()
    normal_page = _page(tmp_path / "ja", locale_id="ja-JP")
    english_page = _page(tmp_path / "en", locale_id="en-US")
    try:
        assert normal_page.search_button.text() == "検索"
        assert normal_page.copy_checked_button.text() == "選択したPromptをコピー"
        assert english_page.search_button.text() == "Search"
        assert english_page.copy_checked_button.text() == "Copy selected prompts"

        apply_application_theme(app, THEME_NORMAL)
        normal_base = app.palette().color(QPalette.ColorRole.Base)
        normal_page.show()
        app.processEvents()
        assert normal_page.results_table.palette().color(
            QPalette.ColorRole.Base
        ) == normal_base

        apply_application_theme(app, THEME_DARK)
        dark_base = app.palette().color(QPalette.ColorRole.Base)
        app.processEvents()
        assert dark_base != normal_base
        assert normal_page.results_table.palette().color(
            QPalette.ColorRole.Base
        ) == dark_base
        assert "palette(highlight)" in normal_page.tag_selector.styleSheet()
    finally:
        normal_page.close()
        english_page.close()
        apply_application_theme(app, THEME_NORMAL)
        app.processEvents()


def test_prompt_library_layout_remains_usable_at_supported_small_sizes(tmp_path) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    _create_prompt(
        manager,
        title="Layout prompt",
        body="body",
        tags=("layout",),
    )
    page = _page(tmp_path, data_dir=data_dir)
    page.activate()
    _select_target(page, "minimax_h3", "T2VA")
    page.show()
    try:
        for width, height in ((1118, 846), (1366, 768), (1920, 1080)):
            page.resize(width, height)
            app.processEvents()
            assert (
                page.tag_selector.candidate_scroll.horizontalScrollBar().maximum()
                == 0
            )
            assert page.results_table.height() >= 100
            assert page.detail_prompt.height() >= page.detail_prompt.minimumHeight()
            assert page.search_button.isVisibleTo(page)
            assert page.copy_checked_button.isVisibleTo(page)
    finally:
        page.close()
        app.processEvents()

def test_prompt_library_display_defaults_auto_height_and_outer_scroll(tmp_path) -> None:
    app = _app()
    page = _page(tmp_path)
    page.show()
    try:
        app.processEvents()
        assert page.tag_selector._visible_rows == 3
        assert page._result_rows == 3
        assert page.detail_prompt.minimum_lines == 10
        assert page.outer_scroll.widgetResizable()
        assert (
            page.outer_scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert (
            page.detail_prompt.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        tag_height = page.tag_selector.candidate_scroll.minimumHeight()
        result_height = page.results_table.minimumHeight()
        detail_height = page.detail_prompt.minimumHeight()

        page.detail_prompt.setPlainText("one\ntwo\nthree")
        app.processEvents()
        assert page.detail_prompt.minimumHeight() >= detail_height
        short_height = page.detail_prompt.minimumHeight()
        page.detail_prompt.setPlainText("\n".join(f"line {i}" for i in range(20)))
        app.processEvents()
        assert page.detail_prompt.minimumHeight() > short_height

        page.apply_display_settings(8, 9, 12)
        app.processEvents()
        assert page.tag_selector.candidate_scroll.minimumHeight() > tag_height
        assert page.results_table.minimumHeight() > result_height
        assert page.detail_prompt.minimum_lines == 12
    finally:
        page.close()
        app.processEvents()


def test_prompt_library_tag_flow_is_dense_and_keeps_long_names(tmp_path) -> None:
    app = _app()
    data_dir = tmp_path / "data"
    manager = PromptLibraryManager(data_dir)
    names = tuple(f"tag-{index}" for index in range(12)) + (
        "a-deliberately-long-tag-name-for-layout-verification",
    )
    created = _create_prompt(
        manager,
        title="Dense tags",
        body="body",
        tags=names,
    )
    page = _page(tmp_path, data_dir=data_dir)
    page.resize(1000, 800)
    page.show()
    page.activate()
    _select_target(page, "minimax_h3", "T2VA")
    try:
        app.processEvents()
        margins = page.tag_selector.candidate_root.contentsMargins()
        assert (margins.left(), margins.right()) == (3, 3)
        assert page.tag_selector.other_layout.horizontalSpacing() == 8

        wrappers = [
            page.tag_selector.candidate_button(tag.id).parentWidget()
            for tag in created.tags
        ]
        row_counts: dict[int, int] = {}
        for widget in wrappers:
            row_y = widget.geometry().y()
            row_counts[row_y] = row_counts.get(row_y, 0) + 1
        assert max(row_counts.values()) > 4

        long_tag = next(
            tag for tag in created.tags if tag.name.startswith("a-deliberately")
        )
        long_button = page.tag_selector.candidate_button(long_tag.id)
        assert long_button is not None
        assert long_button.text() == long_tag.name
        assert long_button.width() >= long_button.sizeHint().width()
        assert page.tag_selector.candidate_scroll.horizontalScrollBar().maximum() == 0
    finally:
        page.close()
        app.processEvents()


def test_prompt_library_display_settings_persist_and_localize(tmp_path) -> None:
    app = _app()
    manager = ConfigManager(tmp_path / "data")
    manager.save(AppConfig())
    ja = Localization(PROJECT_ROOT / "locales", "ja-JP")
    dialog = SettingsDialog(manager, PROJECT_ROOT, localization=ja)
    try:
        assert dialog.prompt_library_tag_rows.value() == 3
        assert dialog.prompt_library_result_rows.value() == 3
        assert dialog.prompt_library_detail_lines.value() == 10
        assert dialog.prompt_library_group.title() == "Prompt Library"
        dialog.prompt_library_tag_rows.setValue(7)
        dialog.prompt_library_result_rows.setValue(8)
        dialog.prompt_library_detail_lines.setValue(14)
        dialog.accept()
    finally:
        dialog.close()
        app.processEvents()

    loaded = manager.load()
    assert loaded.prompt_library_tag_rows == 7
    assert loaded.prompt_library_result_rows == 8
    assert loaded.prompt_library_detail_lines == 14
    en = Localization(PROJECT_ROOT / "locales", "en-US")
    reopened = SettingsDialog(manager, PROJECT_ROOT, localization=en)
    try:
        assert reopened.prompt_library_tag_rows.value() == 7
        assert reopened.prompt_library_result_rows.value() == 8
        assert reopened.prompt_library_detail_lines.value() == 14
        assert en.tr("settings.prompt_library.result_rows") == "Result rows"
    finally:
        reopened.close()
        app.processEvents()
