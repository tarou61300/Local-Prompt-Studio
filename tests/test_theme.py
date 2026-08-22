from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QPalette, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QMenu,
    QStyleFactory,
    QToolBar,
    QToolButton,
    QToolTip,
)

from app import theme as theme_module
from app.main_window import MainWindow
from app.prompt_translation_dialog import PromptTranslationDialog
from app.settings_dialog import SettingsDialog
from app.theme import (
    apply_application_theme,
    build_dark_palette,
    current_application_theme,
)
from core.config_manager import (
    AppConfig,
    ConfigManager,
    THEME_DARK,
    THEME_NORMAL,
)
from core.localization import Localization


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"


class OfflineBridgeService:
    def has_valid_credential(self) -> bool:
        return False

    def invalidate_credentials(self) -> None:
        return None


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    for widget in instance.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(
        None,
        QEvent.Type.DeferredDelete,
    )
    instance.processEvents()
    apply_application_theme(instance, THEME_NORMAL)
    yield instance
    apply_application_theme(instance, THEME_NORMAL)
    for widget in instance.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(
        None,
        QEvent.Type.DeferredDelete,
    )
    instance.processEvents()


@pytest.fixture(autouse=True)
def disable_runtime_detection(monkeypatch):
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.detect_vulkan_devices",
        lambda self: [],
    )
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.runtime_available",
        lambda self, backend: True,
    )


def _make_settings(
    manager: ConfigManager,
    localization: Localization | None = None,
) -> SettingsDialog:
    return SettingsDialog(
        manager,
        PROJECT_ROOT,
        localization=localization,
        bridge_service_factory=lambda _url: OfflineBridgeService(),
    )


def _relative_luminance(color) -> float:
    channels = []
    for component in (color.redF(), color.greenF(), color.blueF()):
        channels.append(
            component / 12.92
            if component <= 0.04045
            else ((component + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first, second) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)

_EFFECTIVE_ROLES = (
    QPalette.ColorRole.Window,
    QPalette.ColorRole.WindowText,
    QPalette.ColorRole.Base,
    QPalette.ColorRole.Text,
    QPalette.ColorRole.Button,
    QPalette.ColorRole.ButtonText,
    QPalette.ColorRole.BrightText,
    QPalette.ColorRole.AlternateBase,
    QPalette.ColorRole.Highlight,
    QPalette.ColorRole.HighlightedText,
    QPalette.ColorRole.PlaceholderText,
)


def _effective_widget_snapshot(widget) -> tuple:
    palette = widget.palette()
    colors = tuple(
        (
            palette.color(QPalette.ColorGroup.Active, role).rgba(),
            palette.color(QPalette.ColorGroup.Disabled, role).rgba(),
        )
        for role in _EFFECTIVE_ROLES
    )
    return (
        widget.style().metaObject().className(),
        widget.backgroundRole(),
        widget.foregroundRole(),
        widget.styleSheet(),
        colors,
    )


def _rendered_mean_luminance(widget) -> float:
    image = widget.grab().toImage()
    if image.isNull():
        raise AssertionError("widget rendering is empty")
    samples = []
    columns = 12
    rows = 8
    for x_index in range(columns):
        x = min(image.width() - 1, (x_index * image.width()) // columns)
        for y_index in range(rows):
            y = min(image.height() - 1, (y_index * image.height()) // rows)
            samples.append(_relative_luminance(image.pixelColor(x, y)))
    return sum(samples) / len(samples)

def test_dark_palette_has_readable_core_and_disabled_roles():
    palette = build_dark_palette()
    active = QPalette.ColorGroup.Active
    disabled = QPalette.ColorGroup.Disabled

    assert _contrast_ratio(
        palette.color(active, QPalette.ColorRole.WindowText),
        palette.color(active, QPalette.ColorRole.Window),
    ) >= 7.0
    assert _contrast_ratio(
        palette.color(active, QPalette.ColorRole.Text),
        palette.color(active, QPalette.ColorRole.Base),
    ) >= 7.0
    assert _contrast_ratio(
        palette.color(active, QPalette.ColorRole.PlaceholderText),
        palette.color(active, QPalette.ColorRole.Window),
    ) >= 4.5
    assert _contrast_ratio(
        palette.color(active, QPalette.ColorRole.ButtonText),
        palette.color(active, QPalette.ColorRole.Button),
    ) >= 7.0
    assert _contrast_ratio(
        palette.color(disabled, QPalette.ColorRole.Text),
        palette.color(disabled, QPalette.ColorRole.Base),
    ) >= 5.0
    assert _contrast_ratio(
        palette.color(disabled, QPalette.ColorRole.ButtonText),
        palette.color(disabled, QPalette.ColorRole.Button),
    ) >= 5.0
    assert _contrast_ratio(
        palette.color(active, QPalette.ColorRole.HighlightedText),
        palette.color(active, QPalette.ColorRole.Highlight),
    ) >= 4.5
    assert _contrast_ratio(
        palette.color(active, QPalette.ColorRole.ToolTipText),
        palette.color(active, QPalette.ColorRole.ToolTipBase),
    ) >= 7.0

def test_theme_switch_restores_normal_and_tooltip_palettes(app):
    normal_palette = QPalette(app.palette())
    normal_tooltip_palette = QPalette(QToolTip.palette())
    normal_style_name = app.style().objectName()

    assert apply_application_theme(app, THEME_DARK) == THEME_DARK
    assert current_application_theme(app) == THEME_DARK
    assert app.style().objectName().lower() == "fusion"
    assert app.palette().color(QPalette.ColorRole.Window) == build_dark_palette().color(
        QPalette.ColorRole.Window
    )
    assert QToolTip.palette().color(
        QPalette.ColorRole.ToolTipBase
    ) == build_dark_palette().color(QPalette.ColorRole.ToolTipBase)

    assert apply_application_theme(app, THEME_NORMAL) == THEME_NORMAL
    assert current_application_theme(app) == THEME_NORMAL
    assert app.style().objectName().lower() == normal_style_name.lower()
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.Button,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.BrightText,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.HighlightedText,
    ):
        assert app.palette().color(role) == normal_palette.color(role)
    assert QToolTip.palette().color(
        QPalette.ColorRole.ToolTipBase
    ) == normal_tooltip_palette.color(QPalette.ColorRole.ToolTipBase)


@pytest.mark.parametrize(
    ("locale_id", "label", "normal_text", "dark_text"),
    [
        ("ja-JP", "外観テーマ", "通常", "ダーク"),
        ("en-US", "Theme", "Normal", "Dark"),
    ],
)
def test_settings_theme_choices_are_localized(
    tmp_path,
    app,
    locale_id,
    label,
    normal_text,
    dark_text,
):
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(ui_locale=locale_id))
    dialog = _make_settings(
        manager,
        Localization(PROJECT_ROOT / "locales", locale_id),
    )
    try:
        assert [dialog.theme.itemData(index) for index in range(dialog.theme.count())] == [
            THEME_NORMAL,
            THEME_DARK,
        ]
        assert [dialog.theme.itemText(index) for index in range(dialog.theme.count())] == [
            normal_text,
            dark_text,
        ]
        assert label in {item.text() for item in dialog.findChildren(QLabel)}
    finally:
        dialog.close()
        app.processEvents()


def test_settings_save_applies_dark_immediately_and_cancel_does_not_change_it(
    tmp_path,
    app,
):
    manager = ConfigManager(tmp_path)
    manager.save(
        AppConfig(
            theme=THEME_NORMAL,
            context_size=16384,
            history_enabled=True,
            auto_quality_tags=False,
            selected_profile="anima",
            selected_variant="turbo_v1_0",
        )
    )
    cancel_before_save = _make_settings(manager)
    cancel_before_save.theme.setCurrentIndex(
        cancel_before_save.theme.findData(THEME_DARK)
    )
    cancel_before_save.reject()
    app.processEvents()
    assert manager.load().theme == THEME_NORMAL
    assert current_application_theme(app) == THEME_NORMAL

    dialog = _make_settings(manager)
    dialog.theme.setCurrentIndex(dialog.theme.findData(THEME_DARK))
    dialog.accept()
    app.processEvents()

    saved = manager.load()
    assert saved.theme == THEME_DARK
    assert saved.context_size == 16384
    assert saved.history_enabled is True
    assert saved.auto_quality_tags is False
    assert saved.selected_profile == "anima"
    assert saved.selected_variant == "turbo_v1_0"
    assert current_application_theme(app) == THEME_DARK

    cancel_dialog = _make_settings(manager)
    try:
        assert cancel_dialog.theme.currentData() == THEME_DARK
        cancel_dialog.theme.setCurrentIndex(cancel_dialog.theme.findData(THEME_NORMAL))
        cancel_dialog.reject()
        app.processEvents()
        assert manager.load().theme == THEME_DARK
        assert current_application_theme(app) == THEME_DARK
    finally:
        cancel_dialog.close()
        app.processEvents()


def test_repeated_dark_normal_switch_restores_effective_widget_state_and_rendering(
    tmp_path,
    app,
):
    host_style_name = app.style().objectName()
    host_palette = QPalette(app.palette())
    host_tooltip_palette = QPalette(QToolTip.palette())
    native_style_name = next(
        (
            key
            for key in QStyleFactory.keys()
            if key.lower().startswith("windows")
        ),
        None,
    )
    if native_style_name is None:
        pytest.skip("Windows native QStyle is unavailable")
    native_style = QStyleFactory.create(native_style_name)
    assert native_style is not None
    app.setStyle(native_style)
    native_palette = QPalette(native_style.standardPalette())
    app.setPalette(native_palette)
    QToolTip.setPalette(native_palette)
    setattr(
        app,
        theme_module._ORIGINAL_STYLE_NAME_ATTRIBUTE,
        native_style_name,
    )
    setattr(
        app,
        theme_module._ORIGINAL_PALETTE_ATTRIBUTE,
        QPalette(native_palette),
    )
    setattr(
        app,
        theme_module._ORIGINAL_TOOLTIP_PALETTE_ATTRIBUTE,
        QPalette(native_palette),
    )
    apply_application_theme(app, THEME_NORMAL)

    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(theme=THEME_NORMAL))
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:1",
        dev_skill_path=SKILL_FIXTURE,
    )
    translation = PromptTranslationDialog(
        lambda key, **_values: key,
        "[Shot 1] 00:10.000 A synthetic scene.",
        parent=window,
    )
    profile_group = window.findChild(QGroupBox, "profile_group")
    toolbar = window.findChild(QToolBar, "main_toolbar")
    assert profile_group is not None
    assert toolbar is not None
    widgets = {
        "main_window": window,
        "central_widget": window.centralWidget(),
        "tabs": window.main_tabs,
        "prompt_page": window.prompt_page,
        "profile_panel": window.left_settings_widget,
        "profile_group": profile_group,
        "request_group": window.request_group,
        "request_editor": window.request_text,
        "prompt_group": window.output_group,
        "prompt_editor": window.output_text,
        "combo_box": window.profile_model,
        "toolbar": toolbar,
        "translation": translation,
        "translation_editor": translation.original_edit,
    }
    try:
        window.resize(1118, 846)
        window.show()
        translation.show()
        app.processEvents()
        normal_style_name = app.style().objectName()
        assert normal_style_name.lower().startswith("windows")
        normal_snapshots = {
            name: _effective_widget_snapshot(widget)
            for name, widget in widgets.items()
        }
        normal_render = _rendered_mean_luminance(window.prompt_page)

        for _cycle in range(2):
            apply_application_theme(app, THEME_DARK)
            app.processEvents()
            assert app.style().objectName().lower() == "fusion"
            dark_render = _rendered_mean_luminance(window.prompt_page)
            assert normal_render - dark_render >= 0.20
            assert any(
                _effective_widget_snapshot(widget) != normal_snapshots[name]
                for name, widget in widgets.items()
            )

            apply_application_theme(app, THEME_NORMAL)
            app.processEvents()
            assert app.style().objectName().lower() == normal_style_name.lower()
            for name, widget in widgets.items():
                assert _effective_widget_snapshot(widget) == normal_snapshots[name]
            restored_render = _rendered_mean_luminance(window.prompt_page)
            assert abs(restored_render - normal_render) <= 0.01
    finally:
        translation.close()
        window.close()
        app.processEvents()
        host_style = QStyleFactory.create(host_style_name)
        assert host_style is not None
        app.setStyle(host_style)
        setattr(
            app,
            theme_module._ORIGINAL_STYLE_NAME_ATTRIBUTE,
            host_style_name,
        )
        setattr(
            app,
            theme_module._ORIGINAL_PALETTE_ATTRIBUTE,
            QPalette(host_palette),
        )
        setattr(
            app,
            theme_module._ORIGINAL_TOOLTIP_PALETTE_ATTRIBUTE,
            QPalette(host_tooltip_palette),
        )
        apply_application_theme(app, THEME_NORMAL)

def test_dark_theme_menu_secondary_custom_and_disabled_controls(tmp_path, app):
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(theme=THEME_DARK))
    apply_application_theme(app, THEME_DARK)
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:1",
        dev_skill_path=SKILL_FIXTURE,
    )
    try:
        window.show()
        app.processEvents()
        active = QPalette.ColorGroup.Active
        disabled = QPalette.ColorGroup.Disabled

        toolbar = window.findChild(QToolBar, "main_toolbar")
        assert toolbar is not None
        toolbar_buttons = [
            button
            for button in toolbar.findChildren(QToolButton)
            if button.defaultAction() is not None
        ]
        assert len(toolbar_buttons) >= 2
        assert all(button.text() for button in toolbar_buttons)
        assert _contrast_ratio(
            toolbar.palette().color(active, QPalette.ColorRole.ButtonText),
            toolbar.palette().color(active, QPalette.ColorRole.Button),
        ) >= 7.0

        custom_buttons = (
            window.mode_supplement_toggle,
            window.visual_style_button,
            window.request_guide_button,
        )
        for button in custom_buttons:
            assert button.text()
            assert _contrast_ratio(
                button.palette().color(active, QPalette.ColorRole.ButtonText),
                button.palette().color(active, QPalette.ColorRole.Button),
            ) >= 7.0
            button.setFocus()
            app.processEvents()

        menus = (
            window.visual_style_button.menu(),
            window.request_guide_button.menu(),
        )
        assert all(isinstance(menu, QMenu) for menu in menus)
        for menu in menus:
            assert _contrast_ratio(
                menu.palette().color(active, QPalette.ColorRole.Text),
                menu.palette().color(active, QPalette.ColorRole.Base),
            ) >= 7.0
            assert _contrast_ratio(
                menu.palette().color(disabled, QPalette.ColorRole.Text),
                menu.palette().color(disabled, QPalette.ColorRole.Base),
            ) >= 5.0
            assert _contrast_ratio(
                menu.palette().color(active, QPalette.ColorRole.HighlightedText),
                menu.palette().color(active, QPalette.ColorRole.Highlight),
            ) >= 4.5

        subtitle = window.findChild(QLabel, "app_subtitle")
        assert subtitle is not None
        secondary_labels = (
            subtitle,
            window.profile_variant_help,
            window.prompt_style_help,
            window.chat_page.empty_label,
        )
        for label in secondary_labels:
            assert "palette(placeholder-text)" in label.styleSheet()
            assert _contrast_ratio(
                label.palette().color(active, QPalette.ColorRole.PlaceholderText),
                label.palette().color(active, QPalette.ColorRole.Window),
            ) >= 4.5

        disabled_buttons = (
            window.unload_model_button,
            window.edit_prompt_button,
            window.cancel_button,
            window.copy_button,
            window.send_comfyui_button,
        )
        for button in disabled_buttons:
            button.setEnabled(False)
            assert _contrast_ratio(
                button.palette().color(disabled, QPalette.ColorRole.ButtonText),
                button.palette().color(disabled, QPalette.ColorRole.Button),
            ) >= 5.0

        for control in (
            window.profile_model,
            window.profile_variant,
            window.auto_quality_tags,
        ):
            expected_role = (
                QPalette.ColorRole.Text
                if isinstance(control, QComboBox)
                else QPalette.ColorRole.WindowText
            )
            background_role = (
                QPalette.ColorRole.Base
                if isinstance(control, QComboBox)
                else QPalette.ColorRole.Window
            )
            assert isinstance(control, (QComboBox, QCheckBox))
            assert _contrast_ratio(
                control.palette().color(active, expected_role),
                control.palette().color(active, background_role),
            ) >= 7.0
    finally:
        window.close()
        app.processEvents()

def test_saved_dark_theme_reaches_prompt_chat_tabs_and_translation_highlights(
    tmp_path,
    app,
):
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(theme=THEME_DARK))
    loaded = manager.load()

    apply_application_theme(app, loaded.theme)
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:1",
        dev_skill_path=SKILL_FIXTURE,
    )
    translation = PromptTranslationDialog(
        lambda key, **_values: key,
        "[Shot 1] 00:10.000 A synthetic scene.",
        parent=window,
    )
    try:
        window.show()
        translation.show()
        app.processEvents()
        dark = build_dark_palette()
        expected_base = dark.color(QPalette.ColorRole.Base)
        expected_window = dark.color(QPalette.ColorRole.Window)
        for widget in (
            window,
            window.main_tabs,
            window.request_text,
            window.output_text,
            window.chat_page,
            window.chat_page.input_text,
            translation,
            translation.original_edit,
        ):
            assert widget.palette().color(QPalette.ColorRole.Window) == expected_window
        for editor in (
            window.request_text,
            window.output_text,
            window.chat_page.input_text,
            translation.original_edit,
        ):
            assert editor.palette().color(QPalette.ColorRole.Base) == expected_base

        selections = translation.original_edit.extraSelections()
        assert selections
        highlight_before = selections[0].format.background().color()
        assert highlight_before != expected_base
        assert _contrast_ratio(
            translation.original_edit.palette().color(QPalette.ColorRole.Text),
            highlight_before,
        ) >= 4.5
        assert _contrast_ratio(
            translation.original_edit.palette().color(
                QPalette.ColorRole.HighlightedText
            ),
            translation.original_edit.palette().color(QPalette.ColorRole.Highlight),
        ) >= 4.5
        assert all(
            selection.format.underlineStyle()
            == QTextCharFormat.UnderlineStyle.SingleUnderline
            for selection in selections
        )

        translation.structure_protection.setChecked(False)
        app.processEvents()
        unprotected = translation.original_edit.extraSelections()
        assert unprotected
        assert all(
            selection.format.underlineStyle()
            == QTextCharFormat.UnderlineStyle.DotLine
            for selection in unprotected
        )
        assert unprotected[0].format.background().color() != expected_base

        window.chat_page.set_status("Synthetic error", error=True)
        assert "#ff7b72" in window.chat_page.status_label.styleSheet()

        apply_application_theme(app, THEME_NORMAL)
        app.processEvents()
        assert "#b00020" in window.chat_page.status_label.styleSheet()
        assert window.palette().color(QPalette.ColorRole.Window) != expected_window
        assert translation.original_edit.extraSelections()
        assert (
            translation.original_edit.extraSelections()[0].format.background().color()
            != highlight_before
        )
    finally:
        translation.close()
        window.close()
        app.processEvents()
