from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from app.settings_dialog import SettingsDialog
from core.config_manager import AppConfig, ConfigManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_dialog(
    tmp_path: Path,
    monkeypatch,
    *,
    width: int,
    height: int,
    locale: str,
) -> SettingsDialog:
    monkeypatch.setattr(
        "app.settings_dialog.LlamaServerManager.detect_vulkan_devices",
        lambda self: [],
    )
    monkeypatch.setattr(
        "app.settings_dialog.LlamaServerManager.runtime_available",
        lambda self, backend: True,
    )
    available = QRect(0, 0, width, height)
    monkeypatch.setattr(
        SettingsDialog,
        "_available_geometry",
        lambda self: QRect(available),
    )
    manager = ConfigManager(tmp_path / f"data-{width}-{height}-{locale}")
    manager.save(AppConfig(ui_locale=locale))
    dialog = SettingsDialog(manager, PROJECT_ROOT)
    dialog.show()
    _app().processEvents()
    return dialog


@pytest.mark.parametrize(
    ("width", "height"),
    ((1280, 720), (1366, 768), (1920, 1080)),
)
@pytest.mark.parametrize("locale", ("ja-JP", "en-US"))
def test_settings_dialog_stays_on_screen_with_fixed_actions_and_scrolls(
    tmp_path, monkeypatch, width, height, locale
):
    app = _app()
    dialog = _make_dialog(
        tmp_path,
        monkeypatch,
        width=width,
        height=height,
        locale=locale,
    )
    try:
        available = QRect(0, 0, width, height)
        frame = dialog.frameGeometry()
        assert frame.left() >= available.left()
        assert frame.top() >= available.top()
        assert frame.right() <= available.right()
        assert frame.bottom() <= available.bottom()
        assert dialog.height() <= int(height * dialog.AVAILABLE_GEOMETRY_RATIO)
        assert dialog.width() <= int(width * dialog.AVAILABLE_GEOMETRY_RATIO)
        if height == 1080:
            assert dialog.height() == dialog.PREFERRED_HEIGHT

        assert dialog.settings_scroll.widgetResizable()
        assert (
            dialog.settings_scroll.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        assert (
            dialog.settings_scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert dialog.settings_scroll.verticalScrollBar().maximum() > 0
        assert dialog.settings_scroll.horizontalScrollBar().maximum() == 0

        save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel = dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert not dialog.settings_scroll.isAncestorOf(dialog.buttons)
        assert dialog.settings_scroll.isAncestorOf(dialog.mmproj_path)
        assert save.isVisibleTo(dialog)
        assert cancel.isVisibleTo(dialog)
        button_bar_top = dialog.buttons.mapTo(dialog, QPoint(0, 0)).y()
        assert button_bar_top + dialog.buttons.height() <= dialog.contentsRect().bottom() + 1

        dialog.settings_scroll.ensureWidgetVisible(dialog.mmproj_path, 16, 16)
        app.processEvents()
        mmproj_top_left = dialog.mmproj_path.mapTo(
            dialog.settings_scroll.viewport(), QPoint(0, 0)
        )
        mmproj_rect = QRect(mmproj_top_left, dialog.mmproj_path.size())
        assert dialog.settings_scroll.viewport().rect().intersects(mmproj_rect)
    finally:
        dialog.close()
        app.processEvents()


def test_long_model_paths_do_not_create_horizontal_settings_scrollbar(
    tmp_path, monkeypatch
):
    app = _app()
    dialog = _make_dialog(
        tmp_path,
        monkeypatch,
        width=1280,
        height=720,
        locale="ja-JP",
    )
    try:
        long_name = "C:\\Models\\" + "very-long-vision-model-name-" * 30 + ".gguf"
        dialog.model_path.setText(long_name)
        dialog.chat_model_path.setText(long_name)
        dialog.mmproj_path.setText(long_name.replace(".gguf", "-mmproj.gguf"))
        app.processEvents()
        assert dialog.settings_scroll.horizontalScrollBar().maximum() == 0
        assert dialog.model_path.width() <= dialog.settings_scroll.viewport().width()
        assert dialog.chat_model_path.width() <= dialog.settings_scroll.viewport().width()
        assert dialog.mmproj_path.width() <= dialog.settings_scroll.viewport().width()
    finally:
        dialog.close()
        app.processEvents()
