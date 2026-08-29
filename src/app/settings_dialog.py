from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.theme import apply_application_theme
from app.workers import ComfyUIPairThread, ComfyUITestThread
from core.comfyui_bridge import (
    BridgeStatus,
    ComfyUIBridgeError,
    ComfyUIBridgeService,
    normalize_comfyui_base_url,
)
from core.config_manager import (
    AppConfig,
    ConfigManager,
    CONTEXT_PRESETS,
    PROMPT_LIBRARY_DETAIL_LINES_RANGE,
    PROMPT_LIBRARY_RESULT_ROWS_RANGE,
    PROMPT_LIBRARY_TAG_ROWS_RANGE,
    normalize_model_path_key,
)
from core.inference_backends import (
    BACKEND_CPU,
    BACKEND_VULKAN,
    BACKENDS,
    GPU_LAYERS_AUTO,
)
from core.llama_manager import LlamaServerManager
from core.localization import (
    DEFAULT_UI_LOCALE,
    LOCALE_DEFINITIONS,
    SUPPORTED_LOCALES,
    Localization,
)
from core.model_manager import inspect_model
from core.skill_manager import SkillError, SkillManager
from core.system_memory import (
    assess_memory,
    format_assessment_details,
    format_memory_status,
    get_system_memory,
)


BRIDGE_ERROR_MESSAGES = {
    "bridge_unavailable": "MMH3 Prompt Bridge could not be reached.",
    "unsupported_bridge_version": "This MMH3 Prompt Bridge version is not supported.",
    "remote_http_not_allowed": "Enter a valid local HTTP or remote HTTPS ComfyUI URL.",
    "no_browser_session": "Open or reload ComfyUI in your browser and try again.",
    "pairing_rejected": "Pairing was rejected in ComfyUI.",
    "pairing_expired": "Pairing timed out. Please try again.",
    "pairing_cancelled": "Pairing was cancelled.",
    "pairing_capacity_reached": "ComfyUI Bridge has too many pending pairings. Try again later.",
    "credential_persistence_failed": "The pairing could not be saved securely.",
    "credential_unavailable": "The local pairing information could not be updated safely.",
    "timeout": "The ComfyUI Bridge request timed out.",
    "malformed_response": "ComfyUI Bridge returned an invalid response.",
}
BRIDGE_ERROR_KEYS = {
    code: f"comfyui.error.{code}" for code in BRIDGE_ERROR_MESSAGES
}


def bridge_error_message(code: str, tr: Callable[..., str] | None = None) -> str:
    if tr is not None:
        return tr(BRIDGE_ERROR_KEYS.get(code, "comfyui.error.generic"))
    return BRIDGE_ERROR_MESSAGES.get(code, "The ComfyUI Bridge operation failed.")


class PairingVerificationDialog(QDialog):
    cancel_requested = Signal()

    def __init__(
        self,
        verification_code: str,
        parent=None,
        *,
        tr: Callable[..., str],
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self.setWindowTitle(self.tr("comfyui.pairing.title"))
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumWidth(430)
        self._allow_close = False
        self._cancel_pending = False

        layout = QVBoxLayout(self)
        title = QLabel(self.tr("comfyui.pairing.title"))
        layout.addWidget(title)
        instruction = QLabel(self.tr("comfyui.pairing.verify_code"))
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        rendered_code = (
            f"{verification_code[:3]} {verification_code[3:]}"
            if len(verification_code) == 6 and verification_code.isdigit()
            else verification_code
        )
        self.code_label = QLabel(rendered_code)
        self.code_label.setAlignment(Qt.AlignCenter)
        self.code_label.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(self.code_label)
        self.waiting_label = QLabel(self.tr("comfyui.pairing.waiting"))
        self.waiting_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.waiting_label)
        self.cancel_button = QPushButton(self.tr("common.cancel"))
        self.cancel_button.clicked.connect(self._request_cancel)
        layout.addWidget(self.cancel_button)

    def _request_cancel(self) -> None:
        if self._cancel_pending:
            return
        self._cancel_pending = True
        self.cancel_button.setEnabled(False)
        self.waiting_label.setText(self.tr("comfyui.pairing.cancelling"))
        self.cancel_requested.emit()

    def finish(self) -> None:
        self._allow_close = True
        self.close()

    def reject(self) -> None:
        if self._allow_close:
            super().reject()
        else:
            self._request_cancel()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        self._request_cancel()
        event.ignore()


class SettingsDialog(QDialog):
    PREFERRED_WIDTH = 760
    PREFERRED_HEIGHT = 760
    AVAILABLE_GEOMETRY_RATIO = 0.9

    def __init__(
        self,
        config_manager: ConfigManager,
        project_root: Path,
        parent=None,
        bridge_service_factory: Callable[[str], ComfyUIBridgeService] | None = None,
        localization: Localization | None = None,
        focus_chat_model: bool = False,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.project_root = project_root
        self.config = config_manager.load()
        self.localization = localization or Localization(
            project_root / "locales", self.config.ui_locale
        )
        self.tr = self.localization.tr
        self.setWindowTitle(self.tr("settings.title"))
        self.setObjectName("settings_dialog")
        self._bridge_service_factory = bridge_service_factory or (
            lambda base_url: ComfyUIBridgeService(
                base_url,
                data_dir=self.config_manager.data_dir,
            )
        )
        self._test_worker: ComfyUITestThread | None = None
        self._pair_worker: ComfyUIPairThread | None = None
        self._pairing_dialog: PairingVerificationDialog | None = None
        self._test_result: BridgeStatus | None = None
        self._test_error_code: str | None = None
        self._pair_succeeded = False
        self._pair_error_code: str | None = None
        self._close_requested = False
        self._close_completed = False
        self._application_quit_pending = False
        self._application = QApplication.instance()
        if self._application is not None:
            self._application.installEventFilter(self)
        try:
            self._saved_comfyui_url = normalize_comfyui_base_url(self.config.comfyui_url)
        except ComfyUIBridgeError:
            self._saved_comfyui_url = normalize_comfyui_base_url(AppConfig().comfyui_url)
        initial_bridge_service = self._bridge_service_factory(self._saved_comfyui_url)
        self._paired_url = (
            self._saved_comfyui_url
            if initial_bridge_service.has_valid_credential()
            else None
        )
        self.runtime_manager = LlamaServerManager(project_root / "runtime")
        self.vulkan_devices = self.runtime_manager.detect_vulkan_devices()

        root_layout = QVBoxLayout(self)
        self.settings_scroll = QScrollArea(self)
        self.settings_scroll.setObjectName("settings_scroll_area")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settings_content = QWidget()
        self.settings_content.setObjectName("settings_scroll_content")
        self.settings_content.setMinimumWidth(0)
        self.settings_scroll.setWidget(self.settings_content)
        root_layout.addWidget(self.settings_scroll, 1)

        layout = QVBoxLayout(self.settings_content)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.addLayout(form)

        self.model_path = QLineEdit(self.config.model_path)
        self._configure_path_field(self.model_path)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_path, 1)
        browse_model = QPushButton(self.tr("settings.choose_gguf"))
        browse_model.clicked.connect(self._choose_model)
        model_row.addWidget(browse_model)
        form.addRow(self.tr("settings.model_path"), model_row)

        self.model_info = QLabel()
        self.model_info.setWordWrap(True)
        form.addRow("", self.model_info)
        self.model_path.textChanged.connect(self._update_model_info)

        self.backend = QComboBox()
        for spec in BACKENDS.values():
            self.backend.addItem(spec.display_name, spec.backend_id)
        vulkan_index = self.backend.findData(BACKEND_VULKAN)
        vulkan_runtime = self.runtime_manager.runtime_available(BACKEND_VULKAN)
        if not vulkan_runtime or not self.vulkan_devices:
            self.backend.model().item(vulkan_index).setEnabled(False)
        backend_index = self.backend.findData(self.config.inference_backend)
        if backend_index < 0 or not self.backend.model().item(backend_index).isEnabled():
            backend_index = self.backend.findData(BACKEND_CPU)
        self.backend.setCurrentIndex(backend_index)
        form.addRow(self.tr("settings.inference_backend"), self.backend)

        self.backend_device = QComboBox()
        for device in self.vulkan_devices:
            self.backend_device.addItem(device.display_name, device.identifier)
        configured_device = self.backend_device.findData(self.config.backend_device)
        if configured_device >= 0:
            self.backend_device.setCurrentIndex(configured_device)
        form.addRow(self.tr("settings.vulkan_device"), self.backend_device)

        self.backend_info = QLabel()
        self.backend_info.setWordWrap(True)
        form.addRow("", self.backend_info)

        self.cpu_threads = QSpinBox()
        self.cpu_threads.setRange(0, 256)
        self.cpu_threads.setSpecialValueText(self.tr("common.auto"))
        self.cpu_threads.setValue(self.config.cpu_threads)
        form.addRow(self.tr("settings.cpu_threads"), self.cpu_threads)

        self.gpu_layers = QSpinBox()
        self.gpu_layers.setRange(-1, 999)
        self.gpu_layers.setSpecialValueText(self.tr("common.auto"))
        self.gpu_layers.setValue(self.config.gpu_layers)
        form.addRow(self.tr("settings.gpu_offload"), self.gpu_layers)
        self.backend.currentIndexChanged.connect(self._update_backend_controls)
        self.backend.currentIndexChanged.connect(lambda _index: self._update_memory_warning())
        self.backend_device.currentIndexChanged.connect(self._update_backend_controls)
        self.backend_device.currentIndexChanged.connect(
            lambda _index: self._update_memory_warning()
        )

        self.context_size = QComboBox()
        for value, _description in CONTEXT_PRESETS:
            description_key = f"settings.context_preset.{value}"
            self.context_size.addItem(
                f"{value} — {self.tr(description_key)}", value
            )
        context_index = self.context_size.findData(self.config.context_size)
        if context_index < 0:
            self.context_size.addItem(
                f"{self.config.context_size} — {self.tr('settings.context_preset.custom')}",
                self.config.context_size,
            )
            context_index = self.context_size.count() - 1
        self.context_size.setCurrentIndex(context_index)
        self.context_size.currentIndexChanged.connect(self._update_memory_warning)
        form.addRow(self.tr("settings.context_size"), self.context_size)

        self.memory_info = QLabel()
        self.memory_info.setWordWrap(True)
        form.addRow(self.tr("settings.memory_estimate"), self.memory_info)

        self._mmproj_mapping = dict(self.config.model_mmproj_paths)
        self._current_mmproj_model_key = ""
        self.chat_model_group = QGroupBox(self.tr("settings.chat_model.title"))
        self.chat_model_group.setObjectName("chat_model_settings")
        chat_model_form = QFormLayout(self.chat_model_group)
        chat_model_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        chat_model_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.use_prompt_model_for_chat = QCheckBox(
            self.tr("settings.chat_model.use_prompt")
        )
        self.use_prompt_model_for_chat.setObjectName("use_prompt_model_for_chat")
        self.use_prompt_model_for_chat.setChecked(
            self.config.use_prompt_model_for_chat
        )
        chat_model_form.addRow(self.use_prompt_model_for_chat)

        self.chat_model_path = QLineEdit(self.config.chat_model_path)
        self._configure_path_field(self.chat_model_path)
        self.chat_model_path.setObjectName("chat_model_path")
        self.chat_model_browse = QPushButton(self.tr("settings.choose_gguf"))
        self.chat_model_browse.setObjectName("chat_model_browse")
        self.chat_model_browse.clicked.connect(self._choose_chat_model)
        chat_model_row = QHBoxLayout()
        chat_model_row.addWidget(self.chat_model_path, 1)
        chat_model_row.addWidget(self.chat_model_browse)
        chat_model_form.addRow(
            self.tr("settings.chat_model.separate_model"), chat_model_row
        )

        self.mmproj_path = QLineEdit()
        self._configure_path_field(self.mmproj_path)
        self.mmproj_path.setObjectName("chat_mmproj_path")
        mmproj_tooltip = self.tr("settings.chat_model.mmproj_tooltip")
        self.mmproj_path.setToolTip(mmproj_tooltip)
        self.mmproj_browse = QPushButton(self.tr("settings.choose_gguf"))
        self.mmproj_browse.setObjectName("chat_mmproj_browse")
        self.mmproj_browse.setToolTip(mmproj_tooltip)
        self.mmproj_browse.clicked.connect(self._choose_mmproj)
        mmproj_row = QHBoxLayout()
        mmproj_row.addWidget(self.mmproj_path, 1)
        mmproj_row.addWidget(self.mmproj_browse)
        chat_model_form.addRow(self.tr("settings.chat_model.mmproj"), mmproj_row)
        layout.addWidget(self.chat_model_group)

        self.use_prompt_model_for_chat.toggled.connect(
            self._chat_model_mode_changed
        )
        self.model_path.editingFinished.connect(self._switch_mmproj_target)
        self.chat_model_path.editingFinished.connect(self._switch_mmproj_target)
        self._update_chat_model_controls()
        self._switch_mmproj_target()

        default_skill = config_manager.data_dir / "skills" / "h3-prompt-writing"
        self.skill_location = QLineEdit(self.config.skill_location or str(default_skill))
        self._configure_path_field(self.skill_location)
        skill_row = QHBoxLayout()
        skill_row.addWidget(self.skill_location, 1)
        browse_skill = QPushButton(self.tr("settings.choose_folder"))
        browse_skill.clicked.connect(self._choose_skill_folder)
        skill_row.addWidget(browse_skill)
        form.addRow(self.tr("settings.skill_location"), skill_row)

        skill_actions = QHBoxLayout()
        check_update = QPushButton(self.tr("settings.skill_update"))
        check_update.clicked.connect(self._check_update)
        skill_actions.addWidget(check_update)
        open_skill = QPushButton(self.tr("settings.skill_open"))
        open_skill.clicked.connect(self._open_skill_folder)
        skill_actions.addWidget(open_skill)
        skill_actions.addStretch()
        form.addRow("", skill_actions)

        self.history_enabled = QCheckBox(self.tr("settings.history_enabled"))
        self.history_enabled.setChecked(self.config.history_enabled)
        form.addRow(self.tr("settings.history"), self.history_enabled)

        self.theme = QComboBox()
        self.theme.setObjectName("theme")
        self.theme.addItem(self.tr("settings.theme.normal"), "normal")
        self.theme.addItem(self.tr("settings.theme.dark"), "dark")
        theme_index = self.theme.findData(self.config.theme)
        self.theme.setCurrentIndex(max(0, theme_index))
        form.addRow(self.tr("settings.theme"), self.theme)

        self.ui_locale = QComboBox()
        self.ui_locale.setObjectName("ui_locale")
        for definition in LOCALE_DEFINITIONS:
            self.ui_locale.addItem(definition.native_name, definition.locale_id)
        locale_index = self.ui_locale.findData(self.config.ui_locale)
        if locale_index < 0:
            locale_index = self.ui_locale.findData(DEFAULT_UI_LOCALE)
        self.ui_locale.setCurrentIndex(locale_index)
        form.addRow(self.tr("settings.language"), self.ui_locale)

        self.prompt_library_group = QGroupBox(
            self.tr("settings.prompt_library.title")
        )
        self.prompt_library_group.setObjectName("prompt_library_display_settings")
        prompt_library_form = QFormLayout(self.prompt_library_group)
        self.prompt_library_tag_rows = QSpinBox()
        self.prompt_library_tag_rows.setObjectName("prompt_library_tag_rows")
        self.prompt_library_tag_rows.setRange(*PROMPT_LIBRARY_TAG_ROWS_RANGE)
        self.prompt_library_tag_rows.setValue(self.config.prompt_library_tag_rows)
        prompt_library_form.addRow(
            self.tr("settings.prompt_library.tag_rows"),
            self.prompt_library_tag_rows,
        )
        self.prompt_library_result_rows = QSpinBox()
        self.prompt_library_result_rows.setObjectName("prompt_library_result_rows")
        self.prompt_library_result_rows.setRange(*PROMPT_LIBRARY_RESULT_ROWS_RANGE)
        self.prompt_library_result_rows.setValue(
            self.config.prompt_library_result_rows
        )
        prompt_library_form.addRow(
            self.tr("settings.prompt_library.result_rows"),
            self.prompt_library_result_rows,
        )
        self.prompt_library_detail_lines = QSpinBox()
        self.prompt_library_detail_lines.setObjectName(
            "prompt_library_detail_lines"
        )
        self.prompt_library_detail_lines.setRange(*PROMPT_LIBRARY_DETAIL_LINES_RANGE)
        self.prompt_library_detail_lines.setValue(
            self.config.prompt_library_detail_lines
        )
        prompt_library_form.addRow(
            self.tr("settings.prompt_library.detail_lines"),
            self.prompt_library_detail_lines,
        )
        layout.addWidget(self.prompt_library_group)

        comfyui_group = QGroupBox(self.tr("comfyui.settings.title"))
        comfyui_form = QFormLayout(comfyui_group)
        comfyui_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        comfyui_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.comfyui_url = QLineEdit(self._saved_comfyui_url)
        self._configure_path_field(self.comfyui_url)
        self.comfyui_url.textChanged.connect(self._update_comfyui_paired_state)
        comfyui_form.addRow(self.tr("comfyui.url"), self.comfyui_url)

        self.comfyui_test_button = QPushButton(self.tr("comfyui.test_connection"))
        self.comfyui_test_button.clicked.connect(self._test_comfyui_connection)
        comfyui_form.addRow("", self.comfyui_test_button)

        self.comfyui_pairing_status = QLabel()
        comfyui_form.addRow(self.tr("comfyui.pairing.status"), self.comfyui_pairing_status)
        self.comfyui_pair_button = QPushButton()
        self.comfyui_pair_button.clicked.connect(self._pair_with_comfyui)
        comfyui_form.addRow("", self.comfyui_pair_button)
        self.comfyui_feedback = QLabel()
        self.comfyui_feedback.setWordWrap(True)
        comfyui_form.addRow("", self.comfyui_feedback)
        layout.addWidget(comfyui_group)

        reset_button = QPushButton(self.tr("settings.reset"))
        reset_button.clicked.connect(self._reset_fields)
        layout.addWidget(reset_button)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(self.tr("settings.save"))
        buttons.button(QDialogButtonBox.Cancel).setText(self.tr("settings.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.setObjectName("settings_action_buttons")
        root_layout.addWidget(buttons)
        self.buttons = buttons
        self._update_comfyui_paired_state()
        self._update_backend_controls()
        self._update_model_info()
        self._fit_to_available_geometry()
        if focus_chat_model:
            QTimer.singleShot(0, self._focus_chat_model_settings)

    @staticmethod
    def _configure_path_field(field: QLineEdit) -> None:
        field.setMinimumWidth(0)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _target_screen(self):
        parent = self.parentWidget()
        if parent is not None:
            window = parent.window()
            handle = window.windowHandle()
            if handle is not None and handle.screen() is not None:
                return handle.screen()
            screen = QApplication.screenAt(window.frameGeometry().center())
            if screen is not None:
                return screen
        return QApplication.primaryScreen()

    def _available_geometry(self) -> QRect:
        screen = self._target_screen()
        return screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 720)

    def _fit_to_available_geometry(self) -> None:
        available = self._available_geometry()
        maximum_width = max(1, int(available.width() * self.AVAILABLE_GEOMETRY_RATIO))
        maximum_height = max(1, int(available.height() * self.AVAILABLE_GEOMETRY_RATIO))
        self.setMinimumWidth(min(650, maximum_width))
        width = min(self.PREFERRED_WIDTH, maximum_width)
        height = min(self.PREFERRED_HEIGHT, maximum_height)
        self.resize(width, height)
        self._center_in_available_geometry(available)

    def _center_in_available_geometry(self, available: QRect | None = None) -> None:
        available = available or self._available_geometry()
        frame = self.frameGeometry()
        target = QPoint(
            available.left() + (available.width() - frame.width()) // 2,
            available.top() + (available.height() - frame.height()) // 2,
        )
        self.move(self.pos() + target - frame.topLeft())

    def _ensure_frame_on_screen(self) -> None:
        available = self._available_geometry()
        frame = self.frameGeometry()
        delta_x = 0
        delta_y = 0
        if frame.left() < available.left():
            delta_x = available.left() - frame.left()
        elif frame.right() > available.right():
            delta_x = available.right() - frame.right()
        if frame.top() < available.top():
            delta_y = available.top() - frame.top()
        elif frame.bottom() > available.bottom():
            delta_y = available.bottom() - frame.bottom()
        if delta_x or delta_y:
            self.move(self.pos() + QPoint(delta_x, delta_y))

    def _focus_chat_model_settings(self) -> None:
        self.settings_scroll.ensureWidgetVisible(self.mmproj_path, 16, 16)
        self.mmproj_path.setFocus()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._ensure_frame_on_screen()

    def _normalized_entered_comfyui_url(self) -> str:
        return normalize_comfyui_base_url(self.comfyui_url.text())

    def _current_url_is_paired(self) -> bool:
        try:
            return self._normalized_entered_comfyui_url() == self._paired_url
        except ComfyUIBridgeError:
            return False

    def _update_comfyui_paired_state(self) -> None:
        paired = self._current_url_is_paired()
        self.comfyui_pairing_status.setText(
            self.tr("comfyui.pairing.paired" if paired else "comfyui.pairing.not_paired")
        )
        self.comfyui_pair_button.setText(
            self.tr("comfyui.pair_again" if paired else "comfyui.pair")
        )

    def _set_comfyui_controls_enabled(self, enabled: bool) -> None:
        self.comfyui_url.setEnabled(enabled)
        self.comfyui_test_button.setEnabled(enabled)
        self.comfyui_pair_button.setEnabled(enabled)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(enabled)

    def _show_bridge_error(self, title: str, code: str) -> None:
        QMessageBox.warning(self, title, bridge_error_message(code, self.tr))

    def _test_comfyui_connection(self) -> None:
        if self._test_worker is not None or self._pair_worker is not None:
            return
        try:
            service = self._bridge_service_factory(self._normalized_entered_comfyui_url())
        except ComfyUIBridgeError as exc:
            self._show_bridge_error(self.tr("comfyui.connection.title"), exc.code)
            return
        self._test_result = None
        self._test_error_code = None
        self.comfyui_feedback.setText(self.tr("comfyui.connection.testing"))
        worker = ComfyUITestThread(service, parent=self)
        self._test_worker = worker
        worker.result_ready.connect(self._test_connection_succeeded)
        worker.error_occurred.connect(self._test_connection_failed)
        worker.finished.connect(self._test_connection_finished)
        worker.finished.connect(worker.deleteLater)
        self._set_comfyui_controls_enabled(False)
        worker.start()

    def _test_connection_succeeded(self, status: BridgeStatus) -> None:
        self._test_result = status

    def _test_connection_failed(self, code: str) -> None:
        self._test_error_code = code

    def _test_connection_finished(self) -> None:
        self._test_worker = None
        if self._close_requested:
            self._finish_requested_close_if_idle()
            return
        self._set_comfyui_controls_enabled(True)
        if self._test_result is not None:
            self.comfyui_feedback.setText(
                self.tr(
                    "comfyui.connection.detected",
                    version=self._test_result.version,
                )
            )
        elif self._test_error_code is not None:
            self.comfyui_feedback.setText(self.tr("comfyui.connection.failed"))
            self._show_bridge_error(
                self.tr("comfyui.connection.title"), self._test_error_code
            )
        else:
            self.comfyui_feedback.clear()

    def _save_pairing_url_if_changed(self, pairing_url: str) -> bool:
        if pairing_url == self._saved_comfyui_url:
            return True
        try:
            self._bridge_service_factory(
                self._saved_comfyui_url
            ).invalidate_credentials()
        except ComfyUIBridgeError as exc:
            self._paired_url = None
            self._update_comfyui_paired_state()
            self._show_bridge_error(self.tr("comfyui.pairing.title"), exc.code)
            return False
        self._paired_url = None
        config = self.config_manager.load()
        config.comfyui_url = pairing_url
        try:
            self.config_manager.save(config)
        except OSError:
            QMessageBox.warning(
                self,
                self.tr("comfyui.pairing.title"),
                self.tr("error.portable_write"),
            )
            self._update_comfyui_paired_state()
            return False
        self._saved_comfyui_url = pairing_url
        self.comfyui_url.setText(pairing_url)
        self._update_comfyui_paired_state()
        return True

    def _pair_with_comfyui(self) -> None:
        if self._test_worker is not None or self._pair_worker is not None:
            return
        try:
            pairing_url = self._normalized_entered_comfyui_url()
        except ComfyUIBridgeError as exc:
            self._show_bridge_error(self.tr("comfyui.pairing.title"), exc.code)
            return
        if not self._save_pairing_url_if_changed(pairing_url):
            return
        service = self._bridge_service_factory(pairing_url)
        self._pairing_url = pairing_url
        self._pair_succeeded = False
        self._pair_error_code = None
        self.comfyui_feedback.setText(self.tr("comfyui.pairing.starting"))
        worker = ComfyUIPairThread(service, parent=self)
        self._pair_worker = worker
        worker.verification_code_ready.connect(self._show_pairing_code)
        worker.pairing_succeeded.connect(self._pairing_succeeded)
        worker.error_occurred.connect(self._pairing_failed)
        worker.finished.connect(self._pairing_finished)
        worker.finished.connect(worker.deleteLater)
        self._set_comfyui_controls_enabled(False)
        worker.start()

    def _show_pairing_code(self, verification_code: str) -> None:
        if self._close_requested or self._pair_worker is None:
            return
        dialog = PairingVerificationDialog(verification_code, self, tr=self.tr)
        dialog.cancel_requested.connect(self._cancel_pairing)
        self._pairing_dialog = dialog
        self.comfyui_feedback.setText(self.tr("comfyui.pairing.waiting_in_comfyui"))
        dialog.show()

    def _cancel_pairing(self) -> None:
        if self._pair_worker is not None:
            self._pair_worker.cancel()
            self.comfyui_feedback.setText(self.tr("comfyui.pairing.cancelling"))

    def _pairing_succeeded(self) -> None:
        self._pair_succeeded = True

    def _pairing_failed(self, code: str) -> None:
        self._pair_error_code = code

    def _pairing_finished(self) -> None:
        self._pair_worker = None
        if self._pairing_dialog is not None:
            dialog = self._pairing_dialog
            self._pairing_dialog = None
            dialog.finish()
            dialog.deleteLater()
        if self._pair_succeeded:
            self._paired_url = self._pairing_url
        self._update_comfyui_paired_state()
        if self._close_requested:
            self._finish_requested_close_if_idle()
            return
        self._set_comfyui_controls_enabled(True)
        if self._pair_succeeded:
            self.comfyui_feedback.setText(self.tr("comfyui.pairing.completed"))
        elif self._pair_error_code == "pairing_cancelled":
            self.comfyui_feedback.setText(
                bridge_error_message("pairing_cancelled", self.tr)
            )
        elif self._pair_error_code is not None:
            self.comfyui_feedback.setText(self.tr("comfyui.pairing.failed"))
            self._show_bridge_error(
                self.tr("comfyui.pairing.title"), self._pair_error_code
            )
        else:
            self.comfyui_feedback.clear()

    def _request_close_after_workers(self) -> None:
        if self._close_requested:
            return
        self._close_requested = True
        self._set_comfyui_controls_enabled(False)
        if self._test_worker is not None:
            self._test_worker.requestInterruption()
        if self._pair_worker is not None:
            self._pair_worker.cancel()
        if self._pairing_dialog is not None:
            self._pairing_dialog.cancel_button.setEnabled(False)
            self._pairing_dialog.waiting_label.setText(
                self.tr("comfyui.pairing.cancelling")
            )
        self._finish_requested_close_if_idle()

    def _finish_requested_close_if_idle(self) -> None:
        if self._test_worker is not None or self._pair_worker is not None:
            return
        if self._pairing_dialog is not None:
            dialog = self._pairing_dialog
            self._pairing_dialog = None
            dialog.finish()
            dialog.deleteLater()
        if not self._close_completed:
            self._close_completed = True
            super().reject()
        if self._application_quit_pending:
            self._application_quit_pending = False
            application = self._application
            if application is not None:
                QTimer.singleShot(0, application.quit)

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self._application
            and event.type() == QEvent.Quit
            and (self._test_worker is not None or self._pair_worker is not None)
        ):
            self._application_quit_pending = True
            self._request_close_after_workers()
            return True
        return super().eventFilter(watched, event)

    def _choose_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("settings.choose_model_title"), "", "GGUF Model (*.gguf)"
        )
        if path:
            self.model_path.setText(str(Path(path).resolve()))
            self._switch_mmproj_target()

    def _choose_chat_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("settings.chat_model.choose_model"),
            "",
            "GGUF Model (*.gguf)",
        )
        if path:
            self.chat_model_path.setText(str(Path(path).resolve()))
            self._switch_mmproj_target()

    def _choose_mmproj(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("settings.chat_model.choose_mmproj"),
            "",
            "GGUF Model (*.gguf)",
        )
        if path:
            self.mmproj_path.setText(str(Path(path).resolve()))

    def _effective_chat_model_text(self) -> str:
        if self.use_prompt_model_for_chat.isChecked():
            return self.model_path.text().strip()
        return self.chat_model_path.text().strip()

    def _store_current_mmproj(self) -> None:
        key = self._current_mmproj_model_key
        if not key:
            return
        value = self.mmproj_path.text().strip()
        if value:
            self._mmproj_mapping[key] = str(Path(value).expanduser().resolve(strict=False))
        else:
            self._mmproj_mapping.pop(key, None)

    def _switch_mmproj_target(self) -> None:
        self._store_current_mmproj()
        key = normalize_model_path_key(self._effective_chat_model_text())
        self._current_mmproj_model_key = key
        self.mmproj_path.blockSignals(True)
        self.mmproj_path.setText(self._mmproj_mapping.get(key, ""))
        self.mmproj_path.blockSignals(False)
        enabled = bool(key)
        self.mmproj_path.setEnabled(enabled)
        self.mmproj_browse.setEnabled(enabled)

    def _chat_model_mode_changed(self) -> None:
        self._update_chat_model_controls()
        self._switch_mmproj_target()

    def _update_chat_model_controls(self) -> None:
        separate = not self.use_prompt_model_for_chat.isChecked()
        self.chat_model_path.setEnabled(separate)
        self.chat_model_browse.setEnabled(separate)

    def _choose_skill_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, self.tr("settings.choose_skill_folder_title")
        )
        if path:
            self.skill_location.setText(str(Path(path).resolve()))

    def _update_model_info(self) -> None:
        path = self.model_path.text().strip()
        if not path:
            self.model_info.setText(self.tr("settings.model.not_set"))
            self._update_memory_warning()
            return
        info = inspect_model(path)
        if not info.exists:
            self.model_info.setText(self.tr("settings.model.not_found"))
            self._update_memory_warning()
            return
        recommended = self.tr(
            "settings.model.recommended"
            if info.is_recommended
            else "settings.model.not_recommended"
        )
        self.model_info.setText(
            self.tr(
                "settings.model.details",
                model=info.display_name,
                filename=info.filename,
                bytes=info.size_bytes,
                gib=info.size_gib,
                recommendation=recommended,
            )
        )
        self._update_memory_warning()

    def _update_memory_warning(self) -> None:
        memory = get_system_memory()
        context_size = int(self.context_size.currentData())
        model = inspect_model(self.model_path.text().strip())
        if not model.exists:
            self.memory_info.setText(
                format_memory_status(memory, self.tr)
                + "\n"
                + self.tr("settings.memory.choose_model", context=context_size)
            )
            self.memory_info.setStyleSheet("")
            return
        assessment = assess_memory(
            context_size,
            model_name=model.display_name,
            model_filename=model.filename,
            model_size_bytes=model.size_bytes,
            memory=memory,
            reported_gpu_memory_bytes=self._selected_vulkan_memory_bytes(),
            tr=self.tr,
        )
        text = format_assessment_details(assessment, self.tr)
        if assessment.warnings:
            text += "\n⚠ " + "\n⚠ ".join(assessment.warnings)
            self.memory_info.setText(text)
            self.memory_info.setStyleSheet("color: #d68a00;")
        else:
            self.memory_info.setText(
                text + "\n" + self.tr("settings.memory.context_standard")
            )
            self.memory_info.setStyleSheet("")

    def _update_backend_controls(self) -> None:
        is_vulkan = self.backend.currentData() == BACKEND_VULKAN
        self.backend_device.setEnabled(is_vulkan and bool(self.vulkan_devices))
        self.gpu_layers.setEnabled(is_vulkan)
        if not is_vulkan:
            suffix_key = ""
            if not self.runtime_manager.runtime_available(BACKEND_VULKAN):
                suffix_key = "settings.backend.cpu_no_vulkan_runtime"
            elif not self.vulkan_devices:
                suffix_key = "settings.backend.cpu_no_vulkan_device"
            self.backend_info.setText(
                self.tr("settings.backend.cpu", detail=self.tr(suffix_key) if suffix_key else "")
            )
        elif self.vulkan_devices:
            selected_id = self.backend_device.currentData()
            selected = next(
                (device for device in self.vulkan_devices if device.identifier == selected_id),
                self.vulkan_devices[0],
            )
            memory_text = self.tr("settings.backend.gpu_memory_unknown")
            if selected.reported_memory_bytes is not None:
                memory_text = self.tr(
                    "settings.backend.gpu_memory",
                    memory=selected.reported_memory_bytes / (1024**3),
                )
            self.backend_info.setText(
                self.tr(
                    "settings.backend.vulkan_detected",
                    device=selected.display_name,
                    uma=self.tr(
                        "backend.memory_classification."
                        f"{selected.memory_classification}"
                    ),
                    memory=memory_text,
                )
            )
        elif not self.runtime_manager.runtime_available(BACKEND_VULKAN):
            self.backend_info.setText(
                self.tr("settings.backend.vulkan_runtime_missing")
            )
        elif not self.vulkan_devices:
            self.backend_info.setText(
                self.tr("settings.backend.vulkan_not_detected")
            )
        else:
            self.backend_info.setText(self.tr("settings.backend.vulkan_unavailable"))

    def _selected_vulkan_memory_bytes(self) -> int | None:
        if self.backend.currentData() != BACKEND_VULKAN:
            return None
        identifier = self.backend_device.currentData()
        selected = next(
            (device for device in self.vulkan_devices if device.identifier == identifier),
            None,
        )
        return selected.reported_memory_bytes if selected is not None else None

    def _skill_manager(self) -> SkillManager:
        return SkillManager(self.skill_location.text().strip())

    def _check_update(self) -> None:
        try:
            update = self._skill_manager().check_for_update()
            text = self.tr(
                "settings.skill.update_available"
                if update
                else "settings.skill.up_to_date"
            )
            QMessageBox.information(
                self, self.tr("settings.skill.update_title"), text
            )
        except SkillError as exc:
            QMessageBox.warning(
                self, self.tr("settings.skill.update_title"), str(exc)
            )

    def _open_skill_folder(self) -> None:
        path = Path(self.skill_location.text().strip())
        if not path.exists():
            QMessageBox.warning(
                self,
                self.tr("settings.skill.folder_title"),
                self.tr("settings.skill.folder_not_found"),
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _reset_fields(self) -> None:
        default = AppConfig()
        self.model_path.clear()
        self.backend.setCurrentIndex(self.backend.findData(default.inference_backend))
        self.cpu_threads.setValue(default.cpu_threads)
        self.gpu_layers.setValue(default.gpu_layers)
        self.context_size.setCurrentIndex(self.context_size.findData(default.context_size))
        self.skill_location.setText(
            str(self.config_manager.data_dir / "skills" / "h3-prompt-writing")
        )
        self.history_enabled.setChecked(default.history_enabled)
        self.theme.setCurrentIndex(self.theme.findData(default.theme))
        self.ui_locale.setCurrentIndex(self.ui_locale.findData(default.ui_locale))
        self.prompt_library_tag_rows.setValue(default.prompt_library_tag_rows)
        self.prompt_library_result_rows.setValue(default.prompt_library_result_rows)
        self.prompt_library_detail_lines.setValue(default.prompt_library_detail_lines)
        self.comfyui_url.setText(default.comfyui_url)
        self.use_prompt_model_for_chat.setChecked(default.use_prompt_model_for_chat)
        self.chat_model_path.setText(default.chat_model_path)
        self._mmproj_mapping.clear()
        self._current_mmproj_model_key = ""
        self.mmproj_path.clear()
        self._switch_mmproj_target()
        self._update_comfyui_paired_state()

    def accept(self) -> None:
        if self._test_worker is not None or self._pair_worker is not None:
            return
        try:
            normalized_comfyui_url = self._normalized_entered_comfyui_url()
        except ComfyUIBridgeError as exc:
            self._show_bridge_error(self.tr("comfyui.url"), exc.code)
            return
        config = self.config_manager.load()
        try:
            saved_comfyui_url = normalize_comfyui_base_url(config.comfyui_url)
        except ComfyUIBridgeError:
            saved_comfyui_url = self._saved_comfyui_url
        if normalized_comfyui_url != saved_comfyui_url:
            try:
                self._bridge_service_factory(saved_comfyui_url).invalidate_credentials()
            except ComfyUIBridgeError as exc:
                self._paired_url = None
                self._update_comfyui_paired_state()
                self._show_bridge_error(self.tr("comfyui.pairing.title"), exc.code)
                return
            self._paired_url = None
        config.model_path = self.model_path.text().strip()
        self._switch_mmproj_target()
        self._store_current_mmproj()
        config.use_prompt_model_for_chat = self.use_prompt_model_for_chat.isChecked()
        config.chat_model_path = self.chat_model_path.text().strip()
        config.model_mmproj_paths = dict(self._mmproj_mapping)
        config.inference_backend = str(self.backend.currentData())
        config.backend_device = (
            str(self.backend_device.currentData() or "")
            if config.inference_backend == BACKEND_VULKAN
            else ""
        )
        config.cpu_threads = self.cpu_threads.value()
        config.gpu_layers = self.gpu_layers.value()
        config.context_size = int(self.context_size.currentData())
        config.skill_location = self.skill_location.text().strip()
        config.history_enabled = self.history_enabled.isChecked()
        config.theme = str(self.theme.currentData())
        config.prompt_library_tag_rows = self.prompt_library_tag_rows.value()
        config.prompt_library_result_rows = self.prompt_library_result_rows.value()
        config.prompt_library_detail_lines = self.prompt_library_detail_lines.value()
        previous_locale = config.ui_locale
        selected_locale = self.ui_locale.currentData()
        config.ui_locale = (
            str(selected_locale)
            if selected_locale in SUPPORTED_LOCALES
            else DEFAULT_UI_LOCALE
        )
        config.comfyui_url = normalized_comfyui_url
        try:
            self.config_manager.save(config)
        except OSError:
            QMessageBox.warning(
                self,
                self.tr("error.settings_save_title"),
                self.tr("error.portable_write"),
            )
            return
        self._saved_comfyui_url = normalized_comfyui_url
        if self._application is not None:
            apply_application_theme(self._application, config.theme)
        if config.ui_locale != previous_locale:
            QMessageBox.information(
                self,
                self.tr("settings.language"),
                self.tr("settings.language.restart"),
            )
        super().accept()

    def reject(self) -> None:
        if self._test_worker is not None or self._pair_worker is not None:
            self._request_close_after_workers()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._test_worker is not None or self._pair_worker is not None:
            self._request_close_after_workers()
            event.ignore()
            return
        event.accept()
