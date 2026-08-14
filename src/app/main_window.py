from __future__ import annotations

from collections.abc import Callable
import sqlite3
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.comfyui_bridge import ComfyUIBridgeError, ComfyUIBridgeService
from core.config_manager import ConfigManager, PORTABLE_WRITE_ERROR
from core.history_manager import HistoryManager
from core.inference_backends import BACKEND_VULKAN, backend_spec
from core.llama_manager import LlamaServerManager
from core.localization import Localization
from core.model_manager import inspect_model
from core.profile_loader import ProfileLoader
from core.profile_models import LoadedProfile
from core.prompt_engine import H3Reference, PromptEngine, PromptSettings, REFERENCE_LIMITS
from core.renderers import (
    ANIMA_HYBRID_OUTPUT_INVALID,
    DANBOORU_OUTPUT_INVALID,
    LITERAL_CONTENT_NOT_PRESERVED,
    PROTECTED_TERM_NOT_PRESERVED,
    RenderResult,
    RendererRegistry,
)
from core.skill_manager import SkillManager
from core.system_memory import (
    MemoryAssessment,
    MemoryInfo,
    assess_memory,
    format_assessment_details,
    format_memory_status,
    get_system_memory,
)
from core.version import APP_DISPLAY_VERSION, APP_RELEASE_DATE, PRODUCT_NAME

from .settings_dialog import SettingsDialog
from .workers import ComfyUISendThread, GenerationThread


CAMERAS = ("Free", "Static camera", "Slow push-in", "Slow pull-out", "Pan", "Tilt", "Tracking", "Handheld")
SHOTS = ("Single continuous shot", "Allow cuts")
MOTIONS = ("Low", "Natural", "Medium", "High")


COMFYUI_SEND_ERROR_MESSAGES = {
    "credential_unavailable": (
        "ComfyUI is not paired. Open Settings and pair with ComfyUI first."
    ),
    "credential_url_mismatch": (
        "ComfyUI is not paired. Open Settings and pair with ComfyUI first."
    ),
    "unauthorized_client": (
        "ComfyUI pairing is no longer valid. Open Settings and pair again."
    ),
    "no_target": (
        "No MMH3 target is selected in ComfyUI. Right-click a text prompt node and "
        "choose MMH3 Prompt Bridge -> Set as MMH3 Target."
    ),
    "target_not_found": (
        "No MMH3 target is selected in ComfyUI. Right-click a text prompt node and "
        "choose MMH3 Prompt Bridge -> Set as MMH3 Target."
    ),
    "stale_target": (
        "The selected ComfyUI target is no longer active. Select the target again in ComfyUI."
    ),
    "stale_session": (
        "The selected ComfyUI target is no longer active. Select the target again in ComfyUI."
    ),
    "widget_not_found": (
        "The selected ComfyUI text field is no longer available. Select a target again."
    ),
    "invalid_widget": (
        "The selected ComfyUI text field is no longer available. Select a target again."
    ),
    "bridge_busy": "ComfyUI Bridge is busy. Please try again in a moment.",
    "ack_timeout": (
        "ComfyUI did not confirm the update. The text may already have been applied. "
        "Check ComfyUI before sending again."
    ),
    "timeout": (
        "Sending timed out. The text may already have been applied. "
        "Check ComfyUI before sending again."
    ),
    "bridge_unavailable": (
        "Could not confirm delivery to ComfyUI. The text may already have been applied. "
        "Check ComfyUI before sending again."
    ),
    "unsupported_bridge_version": (
        "The installed MMH3 Prompt Bridge version is not compatible with this app."
    ),
    "malformed_response": "ComfyUI Bridge returned an invalid response.",
    "text_too_large": "The current text is too large to send to ComfyUI.",
    "rate_limited": "ComfyUI Bridge is rate-limiting requests. Please try again later.",
    "compatibility_unavailable": (
        "The selected ComfyUI session cannot receive text with this Bridge version."
    ),
}


def comfyui_send_error_message(code: str) -> str:
    return COMFYUI_SEND_ERROR_MESSAGES.get(
        code,
        "The text could not be sent to ComfyUI.",
    )


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        project_root: Path,
        config_manager: ConfigManager,
        server_url: str | None = None,
        dev_skill_path: Path | None = None,
        bridge_service_factory: Callable[[str], ComfyUIBridgeService] | None = None,
        localization: Localization | None = None,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.config_manager = config_manager
        self.config = config_manager.load()
        self.localization = localization or Localization(
            project_root / "locales", self.config.ui_locale
        )
        self.tr = self.localization.tr
        self.renderer_registry = RendererRegistry()
        self.profile_catalog = ProfileLoader(
            project_root / "profiles",
            config_manager.data_dir,
            self.renderer_registry,
        ).discover()
        try:
            self.profile: LoadedProfile | None = self.profile_catalog.get(
                self.config.selected_profile
            )
        except KeyError:
            self.profile = self.profile_catalog.profiles.get("minimax_h3")
        if dev_skill_path is not None:
            self.config.skill_location = str(dev_skill_path)
        skill_path = (
            Path(self.config.skill_location)
            if self.config.skill_location
            else config_manager.data_dir / "skills" / "h3-prompt-writing"
        )
        self.skill_manager = SkillManager(skill_path)
        self.server = LlamaServerManager(
            project_root / "runtime",
            base_url=server_url,
            log_dir=config_manager.data_dir / "llama-server",
        )
        self.mock_mode = server_url is not None
        self.history = HistoryManager(config_manager.data_dir / "history.sqlite3")
        self.worker: GenerationThread | None = None
        self._generation_active = False
        self._bridge_service_factory = bridge_service_factory or (
            lambda base_url: ComfyUIBridgeService(
                base_url,
                data_dir=self.config_manager.data_dir,
            )
        )
        self._send_worker: ComfyUISendThread | None = None
        self._send_succeeded = False
        self._send_error_code: str | None = None
        self._send_close_requested = False
        self._send_close_scheduled = False
        self._application_quit_pending = False
        self._application = QApplication.instance()
        if self._application is not None:
            self._application.installEventFilter(self)
        self._last_memory_info: MemoryInfo | None = None
        self._vulkan_devices = []

        self.setWindowTitle(f"{self.tr('app.title')} {APP_DISPLAY_VERSION}")
        self.resize(1120, 820)
        self.setMinimumSize(850, 640)
        self._build_ui()
        self._refresh_readiness()
        self._refresh_memory_display()
        self.memory_timer = QTimer(self)
        self.memory_timer.setInterval(2500)
        self.memory_timer.timeout.connect(self._refresh_memory_display)
        self.memory_timer.start()

    def _build_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        self.settings_action = QAction(self.tr("toolbar.settings"), self)
        self.settings_action.setObjectName("settings_action")
        self.settings_action.triggered.connect(self._open_settings)
        toolbar.addAction(self.settings_action)
        about_action = QAction(self.tr("toolbar.about"), self)
        about_action.setObjectName("about_action")
        about_action.triggered.connect(self._show_about)
        toolbar.addAction(about_action)
        self.addToolBar(toolbar)

        central = QWidget()
        root = QVBoxLayout(central)
        title = QLabel(self.tr("app.title"))
        title.setObjectName("product_title")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        root.addWidget(title)
        subtitle = QLabel(self.tr("app.subtitle"))
        subtitle.setStyleSheet("color: palette(mid);")
        root.addWidget(subtitle)
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        root.addWidget(self.readiness)
        self.memory_status = QLabel()
        self.memory_status.setWordWrap(True)
        root.addWidget(self.memory_status)

        profile_group = QGroupBox(self.tr("target.title"))
        profile_group.setObjectName("profile_group")
        profile_layout = QFormLayout(profile_group)
        self.profile_category = QComboBox()
        self.profile_category.setObjectName("profile_category")
        self.profile_model = QComboBox()
        self.profile_model.setObjectName("profile_model")
        self.profile_variant = QComboBox()
        self.profile_variant.setObjectName("profile_variant")
        self.profile_variant_help = QLabel()
        self.profile_variant_help.setObjectName("profile_variant_help")
        self.profile_variant_help.setWordWrap(True)
        self.profile_variant_help.setStyleSheet(
            "color: palette(mid); font-size: 11px;"
        )
        self.mode = QComboBox()
        self.mode.setObjectName("profile_task")
        self.processing = self._combo(("Faithful", "Balanced", "Creative"))
        self.processing.setObjectName("prompt_style")
        self.prompt_style_help = QLabel()
        self.prompt_style_help.setObjectName("prompt_style_help")
        self.prompt_style_help.setWordWrap(True)
        self.prompt_style_help.setStyleSheet(
            "color: palette(mid); font-size: 11px;"
        )
        profile_layout.addRow(self.tr("profile.category"), self.profile_category)
        profile_layout.addRow(self.tr("profile.model"), self.profile_model)
        profile_layout.addRow(self.tr("profile.variant"), self.profile_variant)
        profile_layout.addRow("", self.profile_variant_help)
        profile_layout.addRow(self.tr("profile.task"), self.mode)
        profile_layout.addRow(self.tr("profile.style"), self.processing)
        profile_layout.addRow("", self.prompt_style_help)
        root.addWidget(profile_group)

        self.legacy_video_settings_group = QGroupBox(self.tr("video.settings"))
        self.legacy_video_settings_group.setObjectName("video_settings_group")
        grid = QGridLayout(self.legacy_video_settings_group)
        self.duration = QSpinBox()
        self.duration.setRange(4, 15)
        self.duration.setValue(10)
        self.duration.setSuffix(self.tr("unit.seconds_suffix"))
        self.camera = self._combo(CAMERAS)
        self.shot = self._combo(SHOTS)
        self.motion = self._combo(MOTIONS)
        entries = (
            ("Duration", self.duration),
            ("Camera", self.camera),
            ("Shot", self.shot),
            ("Motion", self.motion),
        )
        for index, (label, widget) in enumerate(entries):
            row, column = divmod(index, 3)
            grid.addWidget(QLabel(label), row * 2, column)
            grid.addWidget(widget, row * 2 + 1, column)
        audio_row = QHBoxLayout()
        self.environment_audio = QCheckBox("Environmental / scene audio")
        self.environment_audio.setChecked(True)
        self.dialogue_audio = QCheckBox("Dialogue")
        self.dialogue_audio.setChecked(True)
        self.music_audio = QCheckBox("Background music")
        audio_row.addWidget(self.environment_audio)
        audio_row.addWidget(self.dialogue_audio)
        audio_row.addWidget(self.music_audio)
        audio_row.addStretch()
        grid.addWidget(QLabel("Audio"), 4, 0)
        grid.addLayout(audio_row, 5, 0, 1, 3)
        root.addWidget(self.legacy_video_settings_group)

        self.mode_group = QGroupBox(self.tr("mode.notes"))
        mode_layout = QVBoxLayout(self.mode_group)
        self.start_note_label = QLabel(self.tr("mode.start_note"))
        self.start_note = QPlainTextEdit()
        self.start_note.setMaximumHeight(70)
        self.end_note_label = QLabel(self.tr("mode.end_note"))
        self.end_note = QPlainTextEdit()
        self.end_note.setMaximumHeight(70)
        mode_layout.addWidget(self.start_note_label)
        mode_layout.addWidget(self.start_note)
        mode_layout.addWidget(self.end_note_label)
        mode_layout.addWidget(self.end_note)
        self.references = QTableWidget(0, 3)
        self.references.setHorizontalHeaderLabels(["Reference type", "Number", "Description"])
        self.references.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        mode_layout.addWidget(self.references)
        ref_actions = QHBoxLayout()
        add_ref = QPushButton(self.tr("reference.add"))
        add_ref.clicked.connect(self._add_reference)
        remove_ref = QPushButton(self.tr("reference.remove"))
        remove_ref.clicked.connect(self._remove_reference)
        ref_actions.addWidget(add_ref)
        ref_actions.addWidget(remove_ref)
        ref_actions.addStretch()
        mode_layout.addLayout(ref_actions)
        root.addWidget(self.mode_group)

        splitter = QSplitter(Qt.Vertical)
        request_group = QGroupBox(self.tr("input.request"))
        request_layout = QVBoxLayout(request_group)
        self.request_text = QPlainTextEdit()
        self.request_text.setPlaceholderText(self.tr("input.placeholder"))
        self.request_text.setToolTip(self.tr("input.literal_hint"))
        request_layout.addWidget(self.request_text)
        self.literal_hint = QLabel(self.tr("input.literal_hint"))
        self.literal_hint.setObjectName("literal_syntax_hint")
        self.literal_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.literal_hint.setToolTip(self.tr("input.literal_hint"))
        request_layout.addWidget(self.literal_hint)
        splitter.addWidget(request_group)

        self.output_group = QGroupBox(self.tr("output.prompt"))
        output_layout = QVBoxLayout(self.output_group)
        self.output_text = QPlainTextEdit()
        output_layout.addWidget(self.output_text)
        splitter.addWidget(self.output_group)

        self.negative_output_group = QGroupBox(self.tr("output.negative"))
        negative_output_layout = QVBoxLayout(self.negative_output_group)
        self.negative_output_text = QPlainTextEdit()
        self.negative_output_text.setObjectName("negative_output_text")
        negative_output_layout.addWidget(self.negative_output_text)
        splitter.addWidget(self.negative_output_group)

        splitter.setSizes([250, 250, 160])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton(self.tr("common.generate"))
        self.generate_button.setObjectName("generate_button")
        self.generate_button.clicked.connect(self.generate)
        self.cancel_button = QPushButton(self.tr("common.cancel"))
        self.cancel_button.setObjectName("cancel_button")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_generation)
        self.copy_button = QPushButton(self.tr("common.copy"))
        self.copy_button.setObjectName("copy_button")
        self.copy_button.clicked.connect(
            lambda: self.output_text.selectAll() or self.output_text.copy()
        )
        self.copy_negative_button = QPushButton(self.tr("common.copy_negative"))
        self.copy_negative_button.setObjectName("copy_negative_button")
        self.copy_negative_button.clicked.connect(
            lambda: self.negative_output_text.selectAll()
            or self.negative_output_text.copy()
        )
        save_button = QPushButton(self.tr("common.save"))
        save_button.setObjectName("save_button")
        save_button.clicked.connect(self._save_output)
        self.send_comfyui_button = QPushButton("Send to ComfyUI")
        self.send_comfyui_button.setToolTip(
            "Send the current edited output to the selected ComfyUI text field."
        )
        self.send_comfyui_button.clicked.connect(self._send_to_comfyui)
        self.regenerate_button = QPushButton(self.tr("common.regenerate"))
        self.regenerate_button.clicked.connect(self.generate)
        clear = QPushButton(self.tr("common.clear"))
        clear.clicked.connect(self._clear_text)
        for button in (
            self.generate_button,
            self.cancel_button,
            self.copy_button,
            self.copy_negative_button,
            save_button,
            self.send_comfyui_button,
            self.regenerate_button,
            clear,
        ):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.status_label = QLabel(self.tr("common.ready"))
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.setCentralWidget(central)
        self.output_text.textChanged.connect(self._update_send_button_state)
        self.negative_output_text.textChanged.connect(self._update_send_button_state)
        self.profile_category.currentIndexChanged.connect(self._profile_category_changed)
        self.profile_model.currentIndexChanged.connect(self._profile_model_changed)
        self.profile_variant.currentIndexChanged.connect(self._profile_variant_changed)
        self.mode.currentTextChanged.connect(self._update_mode_fields)
        self.processing.currentTextChanged.connect(self._update_prompt_style_help)
        self._populate_profile_selectors()
        self._update_send_button_state()
        self._update_mode_fields(self.mode.currentText())

    @staticmethod
    def _combo(values: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        return combo

    def _available_profiles(self) -> tuple[LoadedProfile, ...]:
        profiles = (
            *self.profile_catalog.profiles.values(),
            *self.profile_catalog.custom_profiles.values(),
        )
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

    def _populate_profile_selectors(self) -> None:
        profiles = self._available_profiles()
        categories = sorted({profile.manifest.category for profile in profiles})
        self.profile_category.blockSignals(True)
        self.profile_category.clear()
        for category in categories:
            self.profile_category.addItem(
                self.tr(f"profile.category.{category}"),
                category,
            )
        selected_category = self.profile.manifest.category if self.profile else None
        category_index = self.profile_category.findData(selected_category)
        self.profile_category.setCurrentIndex(category_index if category_index >= 0 else 0)
        self.profile_category.blockSignals(False)
        self._populate_models(
            str(self.profile_category.currentData() or ""),
            self.profile.manifest.id if self.profile else None,
        )
        self._select_current_profile(persist=False)

    def _populate_models(self, category: str, preferred_profile_id: str | None) -> None:
        profiles = tuple(
            profile
            for profile in self._available_profiles()
            if profile.manifest.category == category
        )
        self.profile_model.blockSignals(True)
        self.profile_model.clear()
        for profile in profiles:
            self.profile_model.addItem(profile.manifest.name, profile.manifest.id)
        selected_index = self.profile_model.findData(preferred_profile_id)
        self.profile_model.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.profile_model.blockSignals(False)

    def _select_current_profile(self, *, persist: bool) -> None:
        profile_id = self.profile_model.currentData()
        try:
            self.profile = self.profile_catalog.get(str(profile_id))
        except KeyError:
            self.profile = self.profile_catalog.profiles.get("minimax_h3")
        self._populate_variants()
        self._populate_tasks()
        self._update_mode_fields(self.mode.currentText())
        self._update_output_fields()
        self._update_prompt_style_help()
        if persist:
            self._persist_profile_selection()
        if hasattr(self, "readiness"):
            self._refresh_readiness()

    def _update_output_fields(self) -> None:
        separate_negative = bool(
            self.profile
            and self.profile.manifest.capabilities.get(
                "separate_negative_prompt", False
            )
        )
        self.output_group.setTitle(
            self.tr("output.positive")
            if separate_negative
            else self.tr("output.prompt")
        )
        self.negative_output_group.setVisible(separate_negative)
        self.copy_negative_button.setVisible(separate_negative)
        if separate_negative:
            self.send_comfyui_button.setToolTip(
                self.tr("comfyui.send_positive_only")
            )
        else:
            self.negative_output_text.clear()
            self.send_comfyui_button.setToolTip(
                self.tr("comfyui.send_current")
            )

    def _populate_variants(self) -> None:
        self.profile_variant.blockSignals(True)
        self.profile_variant.clear()
        if self.profile is not None:
            for variant in self.profile.variants.values():
                self.profile_variant.addItem(variant.name, variant.id)
            preferred_variant = (
                self.config.selected_variant
                if self.config.selected_profile == self.profile.manifest.id
                else self.profile.manifest.default_variant
            )
            selected_index = self.profile_variant.findData(preferred_variant)
            if selected_index < 0:
                selected_index = self.profile_variant.findData(
                    self.profile.manifest.default_variant
                )
            self.profile_variant.setCurrentIndex(max(0, selected_index))
        self.profile_variant.blockSignals(False)
        self._update_variant_help()

    def _populate_tasks(self) -> None:
        previous_task = self.mode.currentData()
        self.mode.blockSignals(True)
        self.mode.clear()
        if self.profile is not None:
            for task in self.profile.manifest.supported_tasks:
                self.mode.addItem(task, task)
        selected_index = self.mode.findData(previous_task)
        self.mode.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.mode.blockSignals(False)

    def _profile_category_changed(self) -> None:
        self._populate_models(str(self.profile_category.currentData() or ""), None)
        self._select_current_profile(persist=True)

    def _profile_model_changed(self) -> None:
        self._select_current_profile(persist=True)

    def _profile_variant_changed(self) -> None:
        self._update_variant_help()
        self._persist_profile_selection()

    def _update_variant_help(self) -> None:
        text = ""
        if self.profile is not None:
            variant_id = str(
                self.profile_variant.currentData()
                or self.profile.manifest.default_variant
            )
            try:
                text = self.profile.variant(variant_id).description(
                    self.localization.locale_id
                )
            except KeyError:
                pass
        self.profile_variant_help.setText(text)
        self.profile_variant_help.setToolTip(text)
        self.profile_variant.setToolTip(text)
        self.profile_variant_help.setVisible(bool(text))

    def _update_prompt_style_help(self, processing: str | None = None) -> None:
        text = ""
        if self.profile is not None:
            renderer = self.renderer_registry.get(self.profile.manifest.renderer)
            text = renderer.prompt_style_description(
                processing or self.processing.currentText(),
                self.localization.locale_id,
            )
        self.prompt_style_help.setText(text)
        self.prompt_style_help.setToolTip(text)
        self.processing.setToolTip(text)
        self.prompt_style_help.setVisible(bool(text))

    def _persist_profile_selection(self) -> None:
        if self.profile is None:
            return
        config = self.config_manager.load()
        config.selected_profile = self.profile.manifest.id
        config.selected_variant = str(
            self.profile_variant.currentData() or self.profile.manifest.default_variant
        )
        try:
            self.config_manager.save(config)
            self.config = config
        except OSError:
            QMessageBox.warning(
                self,
                self.tr("settings.title"),
                PORTABLE_WRITE_ERROR,
            )

    def _update_mode_fields(self, mode: str) -> None:
        legacy_controls = bool(
            self.profile
            and self.profile.manifest.capabilities.get("legacy_h3_controls", False)
        )
        self.legacy_video_settings_group.setVisible(legacy_controls)
        if not legacy_controls:
            self.mode_group.setVisible(False)
            return
        start_visible = mode in {"I2VA", "FL2VA"}
        end_visible = mode in {"FL2VA", "L2VA"}
        refs_visible = mode == "Ref2VA"
        self.start_note_label.setVisible(start_visible)
        self.start_note.setVisible(start_visible)
        self.end_note_label.setVisible(end_visible)
        self.end_note.setVisible(end_visible)
        self.references.setVisible(refs_visible)
        for index in range(self.mode_group.layout().count() - 1, self.mode_group.layout().count()):
            item = self.mode_group.layout().itemAt(index)
            if item and item.layout():
                for child_index in range(item.layout().count()):
                    widget = item.layout().itemAt(child_index).widget()
                    if widget:
                        widget.setVisible(refs_visible)
        self.mode_group.setVisible(start_visible or end_visible or refs_visible)

    def _add_reference(self) -> None:
        row = self.references.rowCount()
        self.references.insertRow(row)
        kind = QComboBox()
        kind.addItems(["Picture", "Video", "Audio"])
        self.references.setCellWidget(row, 0, kind)
        number = QSpinBox()
        number.setRange(1, 9)
        number.setValue(row + 1)
        kind.currentTextChanged.connect(lambda value, box=number: box.setMaximum(REFERENCE_LIMITS[value]))
        self.references.setCellWidget(row, 1, number)
        self.references.setItem(row, 2, QTableWidgetItem(""))

    def _remove_reference(self) -> None:
        rows = sorted({index.row() for index in self.references.selectedIndexes()}, reverse=True)
        for row in rows:
            self.references.removeRow(row)

    def _collect_settings(self) -> PromptSettings:
        refs: list[H3Reference] = []
        for row in range(self.references.rowCount()):
            kind_widget = self.references.cellWidget(row, 0)
            number_widget = self.references.cellWidget(row, 1)
            description_item = self.references.item(row, 2)
            if isinstance(kind_widget, QComboBox) and isinstance(number_widget, QSpinBox):
                refs.append(H3Reference(kind_widget.currentText(), number_widget.value(), description_item.text() if description_item else ""))
        return PromptSettings(
            mode=self.mode.currentText(),
            duration=self.duration.value(),
            processing=self.processing.currentText(),
            camera=self.camera.currentText(),
            shot=self.shot.currentText(),
            motion=self.motion.currentText(),
            environmental_audio=self.environment_audio.isChecked(),
            dialogue=self.dialogue_audio.isChecked(),
            background_music=self.music_audio.isChecked(),
            start_frame_note=self.start_note.toPlainText(),
            end_frame_note=self.end_note.toPlainText(),
            references=refs,
        )

    def _update_send_button_state(self) -> None:
        generation_idle = not self._generation_active
        self.copy_button.setEnabled(
            bool(self.output_text.toPlainText()) and generation_idle
        )
        self.copy_negative_button.setEnabled(
            bool(self.negative_output_text.toPlainText()) and generation_idle
        )
        self.send_comfyui_button.setEnabled(
            bool(self.output_text.toPlainText())
            and self._send_worker is None
            and generation_idle
            and not self._send_close_requested
        )

    def _invalidate_generation_output(self) -> None:
        self.output_text.clear()
        self.negative_output_text.clear()
        self._update_send_button_state()

    def _set_send_conflicting_actions_enabled(self, enabled: bool) -> None:
        self.generate_button.setEnabled(enabled)
        self.regenerate_button.setEnabled(enabled)
        self.settings_action.setEnabled(enabled)

    def _send_to_comfyui(self) -> None:
        if self._send_worker is not None or self._generation_active:
            return
        text = self.output_text.toPlainText()
        if not text:
            self._update_send_button_state()
            return
        config = self.config_manager.load()
        try:
            service = self._bridge_service_factory(config.comfyui_url)
        except ComfyUIBridgeError as exc:
            self.status_label.setText(comfyui_send_error_message(exc.code))
            return
        except Exception:
            self.status_label.setText(
                comfyui_send_error_message("bridge_unavailable")
            )
            return
        self._send_succeeded = False
        self._send_error_code = None
        worker = ComfyUISendThread(service, text, parent=self)
        self._send_worker = worker
        worker.send_succeeded.connect(self._comfyui_send_succeeded)
        worker.error_occurred.connect(self._comfyui_send_failed)
        worker.finished.connect(self._comfyui_send_finished)
        worker.finished.connect(worker.deleteLater)
        self.status_label.setText("Sending to ComfyUI...")
        self._set_send_conflicting_actions_enabled(False)
        self._update_send_button_state()
        worker.start()

    def _comfyui_send_succeeded(self) -> None:
        self._send_succeeded = True

    def _comfyui_send_failed(self, code: str) -> None:
        self._send_error_code = code

    def _comfyui_send_finished(self) -> None:
        self._send_worker = None
        if self._send_close_requested:
            self._resume_close_after_send()
            return
        self._set_send_conflicting_actions_enabled(True)
        self._update_send_button_state()
        if self._send_succeeded:
            self.status_label.setText("Sent to ComfyUI.")
        elif self._send_error_code is not None:
            self.status_label.setText(
                comfyui_send_error_message(self._send_error_code)
            )

    def _request_close_after_send(self) -> None:
        if self._send_close_requested:
            return
        self._send_close_requested = True
        self._set_send_conflicting_actions_enabled(False)
        self._update_send_button_state()
        if self._send_worker is not None:
            self._send_worker.requestInterruption()

    def _resume_close_after_send(self) -> None:
        if not self._send_close_scheduled:
            self._send_close_scheduled = True
            QTimer.singleShot(0, self.close)
        if self._application_quit_pending:
            self._application_quit_pending = False
            application = self._application
            if application is not None:
                QTimer.singleShot(0, application.quit)

    def generate(self) -> None:
        if self._send_worker is not None:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        # Always sample on the button press. The timer value is informational only.
        self._refresh_memory_display()
        request = self.request_text.toPlainText().strip()
        if not request:
            QMessageBox.information(self, "Request", "Requestを入力してください。")
            return
        self.config = self.config_manager.load()
        self._refresh_readiness()
        if self.mock_mode and self.config.skill_location == "":
            self.config.skill_location = str(self.skill_manager.location)
        self.skill_manager = SkillManager(self.config.skill_location or self.skill_manager.location)
        if (
            self.profile is not None
            and self.profile.requires_dependency("prompt_skill")
            and not self.skill_manager.status().valid
        ):
            QMessageBox.warning(
                self,
                self.tr("readiness.skill"),
                self.tr("error.h3_skill_required"),
            )
            return
        if not self.mock_mode and not self.server.runtime_available(self.config.inference_backend):
            QMessageBox.warning(
                self,
                "Runtime未導入",
                f"{backend_spec(self.config.inference_backend).display_name}用 llama.cpp runtimeがありません。設定でCPUを選択してください。",
            )
            return
        if not self.mock_mode and self.config.inference_backend == BACKEND_VULKAN:
            devices = self.server.detect_vulkan_devices()
            identifiers = {device.identifier for device in devices}
            if not devices or (
                self.config.backend_device and self.config.backend_device not in identifiers
            ):
                QMessageBox.warning(
                    self,
                    "Vulkan GPU未検出",
                    "llama.cppで選択されたVulkan GPUを検出できませんでした。設定でCPUを選択してください。",
                )
                return
        # Take a second fresh sample immediately before making the safety decision.
        warning_memory = get_system_memory()
        self._refresh_memory_display(warning_memory)
        assessment = self._memory_assessment(warning_memory)
        warnings = self._memory_warnings(assessment)
        if warnings and assessment is not None:
            answer = QMessageBox.warning(
                self,
                "メモリ使用量の警告",
                format_assessment_details(assessment)
                + "\n\n"
                + "\n\n".join(warnings)
                + "\n\nCPU/GPU生成は数分かかる場合があります。生成を続けますか？",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Ok:
                self._refresh_memory_display()
                return
        if self.profile is None:
            QMessageBox.warning(self, "PROFILE_INVALID", self.tr("error.profile_invalid"))
            return
        variant_id = str(self.profile_variant.currentData() or self.profile.manifest.default_variant)
        self.worker = GenerationThread(
            engine=PromptEngine(self.skill_manager, self.profile, variant_id),
            server=self.server,
            config=self.config,
            request_text=request,
            settings=self._collect_settings(),
            mock_mode=self.mock_mode,
            parent=self,
        )
        self.worker.status_changed.connect(self._set_generation_status)
        self.worker.result_ready.connect(self._generation_complete)
        self.worker.error_occurred.connect(self._generation_error)
        self.worker.finished.connect(self._generation_finished)
        self._generation_active = True
        self._invalidate_generation_output()
        for selector in (
            self.profile_category,
            self.profile_model,
            self.profile_variant,
            self.mode,
        ):
            selector.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.regenerate_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._update_send_button_state()
        self.worker.start()

    def cancel_generation(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.server.cancel()
            self.status_label.setText("キャンセルしています…")
            self._refresh_memory_display()

    def _generation_complete(self, result: RenderResult) -> None:
        self.output_text.setPlainText(result.positive)
        self.negative_output_text.setPlainText(result.negative or "")
        self.status_label.setText(self.tr("status.complete"))
        history_output = self._combined_output_text(result.positive, result.negative)
        try:
            self.history.add(
                enabled=self.config.history_enabled,
                mode=self.mode.currentText(),
                request=self.request_text.toPlainText(),
                output=history_output,
                profile_id=self.profile.manifest.id if self.profile else "minimax_h3",
                profile_version=self.profile.manifest.profile_version if self.profile else "1.0.0",
                variant_id=str(self.profile_variant.currentData() or "base"),
                renderer_id=self.profile.manifest.renderer if self.profile else "minimax_h3",
                processing_mode=self.processing.currentText(),
                profile_hash=self.profile.content_hash if self.profile else "",
            )
        except (OSError, sqlite3.Error):
            QMessageBox.warning(
                self,
                "履歴を保存できませんでした",
                PORTABLE_WRITE_ERROR,
            )

    def _generation_error(self, message: str) -> None:
        self._invalidate_generation_output()
        self.status_label.setText("エラー")
        if message == LITERAL_CONTENT_NOT_PRESERVED:
            message = self.tr("error.literal_not_preserved")
        elif message == PROTECTED_TERM_NOT_PRESERVED:
            message = self.tr("error.protected_not_preserved")
        elif message == DANBOORU_OUTPUT_INVALID:
            message = self.tr("error.danbooru_output_invalid")
        elif message == ANIMA_HYBRID_OUTPUT_INVALID:
            message = self.tr("error.anima_hybrid_output_invalid")
        QMessageBox.warning(self, self.tr("error.generation_title"), message)

    def _set_generation_status(self, status: str) -> None:
        key = {
            "PROMPT_LONGER_THAN_RECOMMENDED": "warning.prompt_long",
            "PROMPT_SHORTER_THAN_RECOMMENDED": "warning.prompt_short",
            "PROMPT_EXCEEDS_HARD_MAXIMUM": "warning.prompt_hard_maximum",
        }.get(status, status)
        self.status_label.setText(self.tr(key))

    def _generation_finished(self) -> None:
        self._generation_active = False
        for selector in (
            self.profile_category,
            self.profile_model,
            self.profile_variant,
            self.mode,
        ):
            selector.setEnabled(True)
        self.generate_button.setEnabled(True)
        self.regenerate_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._update_send_button_state()
        self._refresh_memory_display()

    def _refresh_readiness(self) -> None:
        if self.config.model_path:
            info = inspect_model(self.config.model_path)
            model = (
                f"{info.display_name} / {info.filename} / {info.size_gib:.2f} GiB"
                if info.exists
                else self.tr("readiness.not_found", filename=info.filename)
            )
        else:
            model = self.tr("readiness.not_set")
        mock = " / Mock server" if self.mock_mode else ""
        backend = backend_spec(self.config.inference_backend)
        runtime = (
            "Mock"
            if self.mock_mode
            else ("Ready" if self.server.runtime_available(backend.backend_id) else "Missing")
        )
        device_text = ""
        if backend.backend_id == BACKEND_VULKAN:
            devices = self.server.detect_vulkan_devices()
            self._vulkan_devices = devices
            selected = next(
                (
                    device
                    for device in devices
                    if device.identifier == self.config.backend_device
                ),
                devices[0] if devices else None,
            )
            device_text = (
                f" / {self.tr('readiness.device')}: {selected.display_name} / {selected.uma_label}"
                if selected is not None
                else f" / {self.tr('readiness.device')}: {self.tr('readiness.not_detected')}"
            )
        segments = [f"{self.tr('readiness.model')}: {model}"]
        if self.profile and self.profile.requires_dependency("prompt_skill"):
            skill = (
                self.tr("readiness.installed")
                if self.skill_manager.status().valid
                else self.tr("readiness.not_installed")
            )
            segments.append(f"{self.tr('readiness.skill')}: {skill}")
        segments.extend(
            (
                f"{self.tr('readiness.backend')}: {backend.display_name}{device_text}",
                f"{self.tr('readiness.runtime')}: {runtime}",
                f"{self.tr('readiness.context')}: {self.config.context_size}{mock}",
            )
        )
        self.readiness.setText(" / ".join(segments))
        self.readiness.setStyleSheet("")

    def _memory_assessment(self, memory: MemoryInfo | None) -> MemoryAssessment | None:
        if not self.config.model_path:
            return None
        model = inspect_model(self.config.model_path)
        if not model.exists:
            return None
        return assess_memory(
            self.config.context_size,
            model_name=model.display_name,
            model_filename=model.filename,
            model_size_bytes=model.size_bytes,
            memory=memory,
            reported_gpu_memory_bytes=(
                next(
                    (
                        device.reported_memory_bytes
                        for device in self._vulkan_devices
                        if device.identifier == self.config.backend_device
                    ),
                    None,
                )
                if self.config.inference_backend == BACKEND_VULKAN
                else None
            ),
        )

    def _memory_warnings(self, assessment: MemoryAssessment | None = None) -> list[str]:
        if self.mock_mode:
            return []
        if assessment is None:
            assessment = self._memory_assessment(get_system_memory())
        if assessment is None:
            return []
        return list(assessment.warnings)

    def _refresh_memory_display(self, memory: MemoryInfo | None = None) -> None:
        if memory is None:
            memory = get_system_memory()
        self._last_memory_info = memory
        assessment = self._memory_assessment(memory)
        if assessment is None:
            self.memory_status.setText(format_memory_status(memory))
            self.memory_status.setStyleSheet("")
            return
        text = (
            format_memory_status(memory)
            + f" / Model: {assessment.model_name}"
            + f" / GGUF: {assessment.model_filename} ({assessment.model_size_gib:.2f} GiB)"
            + f" / Backend: {backend_spec(self.config.inference_backend).display_name}"
            + f" / Context: {assessment.context_size}"
            + f" / Estimated RAM: {assessment.estimated_required_gib:.1f} GB（概算）"
        )
        if self.config.inference_backend == BACKEND_VULKAN:
            text += "\nGPUメモリ情報はSystem RAMへ加算していません（UMA安全優先）。"
        if assessment.warnings:
            text += "\n⚠ " + "\n⚠ ".join(assessment.warnings)
            self.memory_status.setStyleSheet("color: #d68a00;")
        else:
            self.memory_status.setStyleSheet("")
        self.memory_status.setText(text)

    def _open_settings(self) -> None:
        if SettingsDialog(
            self.config_manager,
            self.project_root,
            self,
            localization=self.localization,
        ).exec():
            self.config = self.config_manager.load()
            self.skill_manager = SkillManager(
                self.config.skill_location
                or self.config_manager.data_dir / "skills" / "h3-prompt-writing"
            )
            self._refresh_readiness()
            self._refresh_memory_display()

    @staticmethod
    def _combined_output_text(positive: str, negative: str | None) -> str:
        if not negative:
            return positive
        return f"[Positive]\n{positive}\n\n[Negative]\n{negative}"

    def _save_output(self) -> None:
        positive = self.output_text.toPlainText()
        negative = self.negative_output_text.toPlainText() or None
        if not positive:
            QMessageBox.information(
                self,
                self.tr("common.save"),
                self.tr("save.no_prompt"),
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("common.save"),
            "prompt.txt",
            "Text File (*.txt)",
        )
        if path:
            try:
                Path(path).write_text(
                    self._combined_output_text(positive, negative),
                    encoding="utf-8",
                )
                self.status_label.setText(self.tr("save.saved", path=path))
            except OSError as exc:
                QMessageBox.warning(self, self.tr("save.failed"), str(exc))

    def _clear_text(self) -> None:
        self.request_text.clear()
        self.output_text.clear()
        self.negative_output_text.clear()
        self.status_label.setText(self.tr("common.ready"))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            self.tr("about.title"),
            self.tr(
                "about.body",
                product=PRODUCT_NAME,
                version=APP_DISPLAY_VERSION,
                date=APP_RELEASE_DATE,
            ),
        )

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self._application
            and event.type() == QEvent.Quit
            and self._send_worker is not None
        ):
            self._application_quit_pending = True
            self._request_close_after_send()
            return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.memory_timer.stop()
        if self._send_worker is not None:
            self._request_close_after_send()
            event.ignore()
            return
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.server.cancel()
            self.worker.wait(2000)
        self.server.stop()
        event.accept()
