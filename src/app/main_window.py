from __future__ import annotations

from collections.abc import Callable
import sqlite3
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QTextCursor
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
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.comfyui_bridge import ComfyUIBridgeError, ComfyUIBridgeService
from core.chat_engine import ChatEngine
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
    UNREQUESTED_SEMANTIC_TAG,
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
from core.version import (
    APP_DISPLAY_VERSION,
    APP_RELEASE_DATE,
    PRODUCT_NAME,
    REPOSITORY_URL,
)

from .settings_dialog import SettingsDialog
from .chat_page import ChatPage
from .ime_aware_text_edit import ImeAwarePlaceholderPlainTextEdit
from .workers import ChatThread, ComfyUISendThread, GenerationThread


CAMERAS = ("Free", "Static camera", "Slow push-in", "Slow pull-out", "Pan", "Tilt", "Tracking", "Handheld")
SHOTS = ("Single continuous shot", "Allow cuts")
MOTIONS = ("Low", "Natural", "Medium", "High")


MAIN_MODE_TABS_STYLE = """
QTabWidget#main_mode_tabs::pane {
    border: 1px solid palette(mid);
    top: -1px;
    background: palette(window);
}
QTabBar#main_mode_tab_bar::tab {
    min-height: 30px;
    padding: 0px 14px;
    margin-right: 2px;
    color: palette(text);
    background: palette(button);
    border: 1px solid palette(mid);
    border-bottom-color: palette(mid);
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    font-weight: normal;
}
QTabBar#main_mode_tab_bar::tab:selected {
    background: palette(window);
    border-bottom-color: palette(window);
    margin-top: 0px;
    margin-bottom: -1px;
    font-weight: 600;
}
QTabBar#main_mode_tab_bar::tab:!selected {
    margin-top: 2px;
}
QTabBar#main_mode_tab_bar::tab:!selected:hover {
    background: palette(midlight);
}
"""


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
        self.chat_worker: ChatThread | None = None
        self._chat_active = False
        self.chat_messages: list[dict[str, str]] = []
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
        central.setObjectName("main_window_central")
        root = QVBoxLayout(central)

        self.header_widget = QWidget()
        self.header_widget.setObjectName("main_header")
        header_layout = QVBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(3)
        title = QLabel(self.tr("app.title"))
        title.setObjectName("product_title")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        header_layout.addWidget(title)
        subtitle = QLabel(self.tr("app.subtitle"))
        subtitle.setStyleSheet("color: palette(mid);")
        header_layout.addWidget(subtitle)

        system_summary_row = QHBoxLayout()
        self.system_summary = QLabel()
        self.system_summary.setObjectName("system_summary")
        self.system_summary.setWordWrap(True)
        self.system_summary.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        system_summary_row.addWidget(self.system_summary, 1)
        self.unload_model_button = QPushButton(self.tr("model.unload"))
        self.unload_model_button.setObjectName("unload_model_button")
        self.unload_model_button.setToolTip(self.tr("model.unload_tooltip"))
        self.unload_model_button.setEnabled(False)
        self.unload_model_button.clicked.connect(self._unload_model)
        system_summary_row.addWidget(self.unload_model_button)
        self.system_details_toggle = QToolButton()
        self.system_details_toggle.setObjectName("system_details_toggle")
        self.system_details_toggle.setText(self.tr("system.details"))
        self.system_details_toggle.setCheckable(True)
        self.system_details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.system_details_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.system_details_toggle.toggled.connect(self._toggle_system_details)
        system_summary_row.addWidget(self.system_details_toggle)
        header_layout.addLayout(system_summary_row)

        self.system_details_group = QGroupBox(self.tr("system.details_title"))
        self.system_details_group.setObjectName("system_details_group")
        system_details_layout = QVBoxLayout(self.system_details_group)
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        system_details_layout.addWidget(self.readiness)
        self.memory_status = QLabel()
        self.memory_status.setWordWrap(True)
        system_details_layout.addWidget(self.memory_status)
        self.system_details_group.setVisible(False)
        header_layout.addWidget(self.system_details_group)
        root.addWidget(self.header_widget)

        self.prompt_page = QWidget()
        self.prompt_page.setObjectName("prompt_generation_page")
        prompt_root = QVBoxLayout(self.prompt_page)
        prompt_root.setContentsMargins(0, 0, 0, 0)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setObjectName("main_columns_splitter")
        self.main_splitter.setChildrenCollapsible(False)

        self.left_settings_scroll = QScrollArea()
        self.left_settings_scroll.setObjectName("left_settings_scroll")
        self.left_settings_scroll.setWidgetResizable(True)
        self.left_settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.left_settings_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.left_settings_scroll.setMinimumWidth(270)
        self.left_settings_scroll.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        self.left_settings_widget = QWidget()
        self.left_settings_widget.setObjectName("left_settings")
        left_layout = QVBoxLayout(self.left_settings_widget)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        profile_group = QGroupBox(self.tr("target.title"))
        profile_group.setObjectName("profile_group")
        profile_layout = QFormLayout(profile_group)
        profile_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        profile_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        profile_layout.setVerticalSpacing(5)
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
        self.auto_quality_tags = QCheckBox(self.tr("profile.auto_quality_tags"))
        self.auto_quality_tags.setObjectName("auto_quality_tags")
        self.auto_quality_tags.setChecked(self.config.auto_quality_tags)
        for combo in (
            self.profile_category,
            self.profile_model,
            self.profile_variant,
            self.mode,
            self.processing,
        ):
            self._stabilize_combo(combo)
        for help_label in (self.profile_variant_help, self.prompt_style_help):
            help_label.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Minimum,
            )
        profile_layout.addRow(self.tr("profile.category"), self.profile_category)
        profile_layout.addRow(self.tr("profile.model"), self.profile_model)
        profile_layout.addRow(self.tr("profile.variant"), self.profile_variant)
        profile_layout.addRow(self.profile_variant_help)
        profile_layout.addRow(self.tr("profile.task"), self.mode)
        profile_layout.addRow(self.tr("profile.style"), self.processing)
        profile_layout.addRow(self.prompt_style_help)
        profile_layout.addRow(self.auto_quality_tags)
        left_layout.addWidget(profile_group)

        self.legacy_video_settings_group = QGroupBox(self.tr("video.settings"))
        self.legacy_video_settings_group.setObjectName("video_settings_group")
        grid = QGridLayout(self.legacy_video_settings_group)
        self.duration = QSpinBox()
        self.duration.setRange(4, 15)
        self.duration.setValue(10)
        self.duration.setSuffix(self.tr("unit.seconds_suffix"))
        self.duration.setMinimumHeight(30)
        self.duration.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.camera = self._combo(CAMERAS)
        self.shot = self._combo(SHOTS)
        self.motion = self._combo(MOTIONS)
        entries = (
            ("Duration", self.duration),
            ("Motion", self.motion),
            ("Camera", self.camera),
            ("Shot", self.shot),
        )
        for index, (label, widget) in enumerate(entries):
            grid.addWidget(QLabel(label), index * 2, 0)
            grid.addWidget(widget, index * 2 + 1, 0)
            widget.setMinimumWidth(180)
        audio_column = QVBoxLayout()
        self.environment_audio = QCheckBox("Environmental / scene audio")
        self.environment_audio.setChecked(True)
        self.dialogue_audio = QCheckBox("Dialogue")
        self.dialogue_audio.setChecked(True)
        self.music_audio = QCheckBox("Background music")
        for audio_option in (
            self.environment_audio,
            self.dialogue_audio,
            self.music_audio,
        ):
            audio_option.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
        audio_column.addWidget(self.environment_audio)
        audio_column.addWidget(self.dialogue_audio)
        audio_column.addWidget(self.music_audio)
        grid.addWidget(QLabel("Audio"), 8, 0)
        grid.addLayout(audio_column, 9, 0)
        grid.setColumnStretch(0, 1)
        left_layout.addWidget(self.legacy_video_settings_group)

        self.mode_section = QWidget()
        self.mode_section.setObjectName("mode_supplement_section")
        self.mode_section.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        mode_section_layout = QVBoxLayout(self.mode_section)
        mode_section_layout.setContentsMargins(0, 0, 0, 0)
        self.mode_supplement_toggle = QToolButton()
        self.mode_supplement_toggle.setObjectName("mode_supplement_toggle")
        self.mode_supplement_toggle.setText(self.tr("mode.supplement"))
        self.mode_supplement_toggle.setCheckable(True)
        self.mode_supplement_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.mode_supplement_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.mode_supplement_toggle.toggled.connect(
            self._toggle_mode_supplement
        )
        mode_section_layout.addWidget(self.mode_supplement_toggle)
        self.mode_group = QGroupBox(self.tr("mode.notes"))
        mode_layout = QVBoxLayout(self.mode_group)
        self.common_note_label = QLabel(self.tr("mode.common_note"))
        self.common_note = QPlainTextEdit()
        self.common_note.setObjectName("common_supplement")
        self.common_note.setMinimumHeight(38)
        self.common_note.setMaximumHeight(48)
        self.start_note_label = QLabel(self.tr("mode.start_note"))
        self.start_note = QPlainTextEdit()
        self.start_note.setMinimumHeight(38)
        self.start_note.setMaximumHeight(48)
        self.end_note_label = QLabel(self.tr("mode.end_note"))
        self.end_note = QPlainTextEdit()
        self.end_note.setMinimumHeight(38)
        self.end_note.setMaximumHeight(48)
        mode_layout.addWidget(self.common_note_label)
        mode_layout.addWidget(self.common_note)
        mode_layout.addWidget(self.start_note_label)
        mode_layout.addWidget(self.start_note)
        mode_layout.addWidget(self.end_note_label)
        mode_layout.addWidget(self.end_note)
        self.references = QTableWidget(0, 3)
        self.references.setMinimumHeight(110)
        self.references.setMaximumHeight(150)
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
        self.mode_group.setVisible(False)
        mode_section_layout.addWidget(self.mode_group)
        left_layout.addStretch()
        self.left_settings_scroll.setWidget(self.left_settings_widget)
        self.main_splitter.addWidget(self.left_settings_scroll)

        self.right_workspace = QWidget()
        self.right_workspace.setObjectName("right_workspace")
        right_layout = QVBoxLayout(self.right_workspace)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.addWidget(self.mode_section, 0)
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setObjectName("workspace_splitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        request_group = QGroupBox(self.tr("input.request"))
        request_group.setObjectName("request_group")
        request_group.setMinimumHeight(185)
        request_layout = QVBoxLayout(request_group)
        request_layout.setContentsMargins(9, 12, 9, 9)
        request_layout.setSpacing(6)
        self.request_text = ImeAwarePlaceholderPlainTextEdit()
        self.request_text.setMinimumHeight(105)
        self.request_text.setPlaceholderText(self.tr("input.placeholder"))
        self.request_text.setToolTip(self.tr("input.literal_hint"))
        request_layout.addWidget(self.request_text, 1)
        self.literal_hint = QLabel(self.tr("input.literal_hint"))
        self.literal_hint.setObjectName("literal_syntax_hint")
        self.literal_hint.setWordWrap(True)
        literal_hint_height = self.literal_hint.fontMetrics().lineSpacing() * 2 + 4
        self.literal_hint.setMinimumHeight(literal_hint_height)
        self.literal_hint.setMaximumHeight(literal_hint_height)
        self.literal_hint.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.literal_hint.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.literal_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.literal_hint.setToolTip(self.tr("input.literal_hint"))
        request_layout.addWidget(self.literal_hint)
        self.workspace_splitter.addWidget(request_group)

        self.prompt_splitter = QSplitter(Qt.Orientation.Vertical)
        self.prompt_splitter.setObjectName("prompt_splitter")
        self.prompt_splitter.setChildrenCollapsible(False)
        self.prompt_splitter.setMinimumHeight(170)
        self.output_group = QGroupBox(self.tr("output.prompt"))
        self.output_group.setMinimumHeight(150)
        output_layout = QVBoxLayout(self.output_group)
        self.output_text = QPlainTextEdit()
        self.output_text.setMinimumHeight(105)
        output_layout.addWidget(self.output_text)
        self.prompt_splitter.addWidget(self.output_group)

        self.negative_output_group = QGroupBox(self.tr("output.negative"))
        self.negative_output_group.setMinimumHeight(120)
        negative_output_layout = QVBoxLayout(self.negative_output_group)
        self.negative_output_text = QPlainTextEdit()
        self.negative_output_text.setObjectName("negative_output_text")
        self.negative_output_text.setMinimumHeight(75)
        negative_output_layout.addWidget(self.negative_output_text)
        self.prompt_splitter.addWidget(self.negative_output_group)

        self.prompt_splitter.setStretchFactor(0, 3)
        self.prompt_splitter.setStretchFactor(1, 2)
        self.prompt_splitter.setSizes([210, 140])
        self.workspace_splitter.addWidget(self.prompt_splitter)
        self.workspace_splitter.setStretchFactor(0, 2)
        self.workspace_splitter.setStretchFactor(1, 3)
        self.workspace_splitter.setSizes([240, 360])
        right_layout.addWidget(self.workspace_splitter, 1)
        self.main_splitter.addWidget(self.right_workspace)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([290, 750])
        prompt_root.addWidget(self.main_splitter, 1)

        self.action_bar = QWidget()
        self.action_bar.setObjectName("bottom_action_bar")
        buttons = QHBoxLayout(self.action_bar)
        buttons.setContentsMargins(0, 0, 0, 0)
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
        prompt_root.addWidget(self.action_bar)
        self.status_label = QLabel(self.tr("common.ready"))
        self.status_label.setWordWrap(True)
        prompt_root.addWidget(self.status_label)

        self.chat_page = ChatPage(self.tr)
        self.chat_page.setObjectName("ai_chat_page")
        self.main_tabs = QTabWidget()
        self.main_tabs.setObjectName("main_mode_tabs")
        self.main_tabs.tabBar().setObjectName("main_mode_tab_bar")
        self.main_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.main_tabs.setStyleSheet(MAIN_MODE_TABS_STYLE)
        self.main_tabs.addTab(self.prompt_page, "")
        self.main_tabs.addTab(self.chat_page, self.tr("tabs.ai_chat"))
        root.addWidget(self.main_tabs, 1)
        self.setCentralWidget(central)
        self.output_text.textChanged.connect(self._update_send_button_state)
        self.negative_output_text.textChanged.connect(self._update_send_button_state)
        self.profile_category.currentIndexChanged.connect(self._profile_category_changed)
        self.profile_model.currentIndexChanged.connect(self._profile_model_changed)
        self.profile_variant.currentIndexChanged.connect(self._profile_variant_changed)
        self.mode.currentTextChanged.connect(self._task_changed)
        self.processing.currentTextChanged.connect(self._update_prompt_style_help)
        self.auto_quality_tags.toggled.connect(self._persist_auto_quality_tags)
        self._populate_profile_selectors()
        self.chat_page.set_target_catalog(
            tuple(
                (
                    profile.manifest.id,
                    profile.manifest.name,
                    profile.manifest.supported_tasks,
                )
                for profile in self._available_profiles()
            )
        )
        self.chat_page.send_requested.connect(self._send_chat_message)
        self.chat_page.cancel_requested.connect(self._cancel_chat)
        self.chat_page.new_chat_requested.connect(self._new_chat)
        self.chat_page.target_profile_requested.connect(
            self._select_chat_target_profile
        )
        self.chat_page.target_task_requested.connect(self._select_chat_target_task)
        self.chat_page.transfer_requested.connect(self._transfer_chat_response)
        self.chat_page.open_prompt_requested.connect(self._open_prompt_destination)
        self._sync_prompt_target_ui()
        self._update_send_button_state()
        self._update_unload_button_state()
        self._update_mode_fields(self.mode.currentText())

    @staticmethod
    def _combo(values: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        MainWindow._stabilize_combo(combo)
        return combo

    @staticmethod
    def _stabilize_combo(combo: QComboBox) -> None:
        combo.setMinimumHeight(30)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(12)
        combo.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )

    def _toggle_system_details(self, expanded: bool) -> None:
        self.system_details_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.system_details_group.setVisible(expanded)

    def _toggle_mode_supplement(self, expanded: bool) -> None:
        self.mode_supplement_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.mode_group.setVisible(
            expanded and bool(getattr(self, "_mode_supplement_available", False))
        )
        self._sync_mode_section_height()

    def _sync_mode_section_height(self) -> None:
        layout = self.mode_section.layout()
        if layout is not None:
            layout.activate()
        self.mode_section.setMaximumHeight(self.mode_section.sizeHint().height())
        self.mode_section.updateGeometry()

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
        self._sync_prompt_target_ui()
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

    def _task_changed(self, mode: str) -> None:
        self._update_mode_fields(mode)
        self._sync_prompt_target_ui()

    def _prompt_tab_title(self) -> str:
        profile_name = self.profile.manifest.name if self.profile else "—"
        task_name = self.mode.currentText()
        return f"{profile_name} / {task_name}" if task_name else profile_name

    def _sync_prompt_target_ui(self) -> None:
        if not hasattr(self, "main_tabs"):
            return
        title = self._prompt_tab_title()
        self.main_tabs.setTabText(0, title)
        self.main_tabs.setTabToolTip(0, title)
        if self.profile is None or not hasattr(self, "chat_page"):
            return
        start_visible, end_visible, _refs_visible = self._supplement_capabilities(
            self.mode.currentText()
        )
        destinations: list[tuple[str, str]] = [
            ("common", self.tr("chat.destination.common"))
        ]
        if start_visible:
            destinations.append(("start", self.tr("chat.destination.start")))
        if end_visible:
            destinations.append(("end", self.tr("chat.destination.end")))
        self.chat_page.sync_target(
            profile_id=self.profile.manifest.id,
            profile_name=self.profile.manifest.name,
            task=self.mode.currentText(),
            destinations=destinations,
        )

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

    def _persist_auto_quality_tags(self, enabled: bool) -> None:
        config = self.config_manager.load()
        config.auto_quality_tags = enabled
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
        start_visible, end_visible, refs_visible = self._supplement_capabilities(mode)
        self.common_note_label.setVisible(True)
        self.common_note.setVisible(True)
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
        self._mode_supplement_available = True
        self.mode_section.setVisible(True)
        self.mode_group.setVisible(
            self._mode_supplement_available
            and self.mode_supplement_toggle.isChecked()
        )
        self._sync_mode_section_height()

    def _supplement_capabilities(self, mode: str) -> tuple[bool, bool, bool]:
        start_visible = mode in {"I2V", "I2VA", "FL2VA"}
        end_visible = mode in {"FL2VA", "L2VA"}
        refs_visible = bool(
            mode == "Ref2VA"
            and self.profile
            and self.profile.manifest.capabilities.get("legacy_h3_controls", False)
        )
        return start_visible, end_visible, refs_visible

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
            common_supplement=self.common_note.toPlainText(),
            start_frame_note=self.start_note.toPlainText(),
            end_frame_note=self.end_note.toPlainText(),
            references=refs,
            auto_quality_tags=self.auto_quality_tags.isChecked(),
        )

    def _select_chat_target_profile(self, profile_id: str) -> None:
        try:
            profile = self.profile_catalog.get(profile_id)
        except KeyError:
            return
        category_index = self.profile_category.findData(profile.manifest.category)
        if category_index < 0:
            return
        self.profile_category.blockSignals(True)
        self.profile_category.setCurrentIndex(category_index)
        self.profile_category.blockSignals(False)
        self._populate_models(profile.manifest.category, profile_id)
        self._select_current_profile(persist=True)

    def _select_chat_target_task(self, task: str) -> None:
        task_index = self.mode.findData(task)
        if task_index >= 0:
            if self.mode.currentIndex() == task_index:
                self._task_changed(self.mode.currentText())
            else:
                self.mode.setCurrentIndex(task_index)

    @staticmethod
    def _append_supplement(widget: QPlainTextEdit, text: str) -> None:
        existing = widget.toPlainText()
        combined = f"{existing.rstrip()}\n\n{text}" if existing.strip() else text
        widget.setPlainText(combined)
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.clearSelection()
        widget.setTextCursor(cursor)

    def _transfer_chat_response(self, text: str, destination: str) -> None:
        widgets = {
            "common": self.common_note,
            "start": self.start_note,
            "end": self.end_note,
        }
        start_visible, end_visible, _refs_visible = self._supplement_capabilities(
            self.mode.currentText()
        )
        allowed = {"common"}
        if start_visible:
            allowed.add("start")
        if end_visible:
            allowed.add("end")
        if destination not in allowed:
            return
        self._append_supplement(widgets[destination], text)
        labels = {
            "common": self.tr("chat.destination.common"),
            "start": self.tr("chat.destination.start"),
            "end": self.tr("chat.destination.end"),
        }
        self.chat_page.show_transfer_complete(
            destination,
            labels[destination],
            self._prompt_tab_title(),
        )

    def _open_prompt_destination(self, destination: str) -> None:
        widgets = {
            "common": self.common_note,
            "start": self.start_note,
            "end": self.end_note,
        }
        widget = widgets.get(destination)
        if widget is None:
            return
        self.main_tabs.setCurrentWidget(self.prompt_page)
        self.mode_supplement_toggle.setChecked(True)
        self._sync_mode_section_height()

        def focus_destination() -> None:
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            cursor = widget.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.clearSelection()
            widget.setTextCursor(cursor)

        focus_destination()
        QTimer.singleShot(0, focus_destination)

    def _send_chat_message(self, text: str) -> None:
        if self._generation_active or self._chat_active:
            return
        message = text.strip()
        if not message:
            return
        self.config = self.config_manager.load()
        self.chat_messages.append({"role": "user", "content": message})
        self.chat_page.add_message("user", message)
        self.chat_page.input_text.clear()
        self.chat_worker = ChatThread(
            engine=ChatEngine(),
            server=self.server,
            config=self.config,
            conversation=self.chat_messages,
            mock_mode=self.mock_mode,
            parent=self,
        )
        self.chat_worker.status_changed.connect(self._set_chat_status)
        self.chat_worker.result_ready.connect(self._chat_complete)
        self.chat_worker.error_occurred.connect(self._chat_error)
        self.chat_worker.finished.connect(self._chat_finished)
        self._chat_active = True
        self._update_llm_controls()
        self.chat_worker.start()

    def _cancel_chat(self) -> None:
        if self.chat_worker is not None and self.chat_worker.isRunning():
            self.chat_worker.requestInterruption()
            self.server.cancel()
            self.chat_page.set_status(self.tr("chat.error.cancelled"))

    def _new_chat(self) -> None:
        if self._chat_active:
            return
        self.chat_messages.clear()
        self.chat_page.clear_messages()
        self.chat_page.input_text.clear()
        self.chat_page.set_status("")

    def _set_chat_status(self, status: str) -> None:
        self.chat_page.set_status(self.tr(status))

    def _chat_complete(self, response: str) -> None:
        self.chat_messages.append({"role": "assistant", "content": response})
        self.chat_page.add_message("assistant", response)
        self.chat_page.set_status(self.tr("chat.status.complete"))

    def _chat_error(self, error: str) -> None:
        if error == "CHAT_CONTEXT_OVERFLOW":
            message = self.tr("chat.error.context")
        elif error == "CHAT_CANCELLED":
            message = self.tr("chat.error.cancelled")
        else:
            message = error or self.tr("chat.error.generic")
        self.chat_page.set_status(message, error=True)

    def _chat_finished(self) -> None:
        self._chat_active = False
        finished_worker = self.chat_worker
        self.chat_worker = None
        if finished_worker is not None:
            finished_worker.deleteLater()
        self._update_llm_controls()
        self._refresh_memory_display()

    def _update_llm_controls(self) -> None:
        any_busy = self._generation_active or self._chat_active
        self.chat_page.set_busy(
            any_llm_busy=any_busy,
            chat_busy=self._chat_active,
        )
        if self._chat_active:
            self.generate_button.setEnabled(False)
            self.regenerate_button.setEnabled(False)
        elif not self._generation_active and self._send_worker is None:
            self.generate_button.setEnabled(True)
            self.regenerate_button.setEnabled(True)
        self._update_unload_button_state()

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

    def _update_unload_button_state(self) -> None:
        self.unload_model_button.setEnabled(
            not self._generation_active
            and not self._chat_active
            and self.server.is_owned_server_running
        )

    def _unload_model(self) -> None:
        if (
            self._generation_active
            or self._chat_active
            or not self.server.is_owned_server_running
        ):
            self._update_unload_button_state()
            return
        try:
            self.server.stop()
        except Exception as exc:
            QMessageBox.warning(
                self,
                self.tr("model.unload_error_title"),
                self.tr("model.unload_failed", error=str(exc)),
            )
        else:
            self.status_label.setText(self.tr("model.unloaded"))
        finally:
            self._update_unload_button_state()
            self._refresh_memory_display()

    def _invalidate_generation_output(self) -> None:
        self.output_text.clear()
        self.negative_output_text.clear()
        self._update_send_button_state()

    def _set_send_conflicting_actions_enabled(self, enabled: bool) -> None:
        self.generate_button.setEnabled(enabled and not self._chat_active)
        self.regenerate_button.setEnabled(enabled and not self._chat_active)
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
        if self._chat_active:
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
        self._update_unload_button_state()
        self._invalidate_generation_output()
        for selector in (
            self.profile_category,
            self.profile_model,
            self.profile_variant,
            self.mode,
            self.auto_quality_tags,
        ):
            selector.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.regenerate_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._update_llm_controls()
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
                common_supplement=self.common_note.toPlainText(),
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
        elif message == UNREQUESTED_SEMANTIC_TAG:
            message = self.tr("error.unrequested_semantic_tag")
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
            self.auto_quality_tags,
        ):
            selector.setEnabled(True)
        self.generate_button.setEnabled(True)
        self.regenerate_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._update_llm_controls()
        self._update_send_button_state()
        self._update_unload_button_state()
        self._refresh_memory_display()

    def _refresh_readiness(self) -> None:
        readiness_warning = False
        if self.config.model_path:
            info = inspect_model(self.config.model_path)
            model = (
                f"{info.display_name} / {info.filename} / {info.size_gib:.2f} GiB"
                if info.exists
                else self.tr("readiness.not_found", filename=info.filename)
            )
            summary_model = (
                info.display_name
                if info.exists
                else self.tr("readiness.not_found", filename=info.filename)
            )
            readiness_warning = not info.exists
        else:
            model = self.tr("readiness.not_set")
            summary_model = model
        mock = " / Mock server" if self.mock_mode else ""
        backend = backend_spec(self.config.inference_backend)
        runtime = (
            "Mock"
            if self.mock_mode
            else ("Ready" if self.server.runtime_available(backend.backend_id) else "Missing")
        )
        readiness_warning = readiness_warning or runtime == "Missing"
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
            readiness_warning = readiness_warning or selected is None
        segments = [f"{self.tr('readiness.model')}: {model}"]
        if self.profile and self.profile.requires_dependency("prompt_skill"):
            skill_valid = self.skill_manager.status().valid
            skill = (
                self.tr("readiness.installed")
                if skill_valid
                else self.tr("readiness.not_installed")
            )
            segments.append(f"{self.tr('readiness.skill')}: {skill}")
            readiness_warning = readiness_warning or not skill_valid
        segments.extend(
            (
                f"{self.tr('readiness.backend')}: {backend.display_name}{device_text}",
                f"{self.tr('readiness.runtime')}: {runtime}",
                f"{self.tr('readiness.context')}: {self.config.context_size}{mock}",
            )
        )
        self.readiness.setText(" / ".join(segments))
        self.readiness.setStyleSheet("")
        self._system_summary_model = summary_model
        self._system_summary_backend = backend.display_name
        self._system_readiness_warning = readiness_warning
        self._update_system_summary()

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
            self._system_memory_warning = False
            self._update_system_summary()
            self._update_unload_button_state()
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
        self._system_memory_warning = bool(assessment.warnings)
        self._update_system_summary()
        self._update_unload_button_state()

    def _update_system_summary(self) -> None:
        if not hasattr(self, "system_summary"):
            return
        memory = self._last_memory_info
        if memory is None:
            ram = self.tr("system.ram_unknown")
        else:
            ram = f"{memory.available_gib:.1f} / {memory.total_gib:.1f} GB"
        summary = self.tr(
            "system.summary",
            model=getattr(
                self,
                "_system_summary_model",
                self.tr("readiness.not_set"),
            ),
            backend=getattr(self, "_system_summary_backend", ""),
            context=self.config.context_size,
            ram=ram,
        )
        if bool(getattr(self, "_system_memory_warning", False)):
            summary += f"   ⚠ {self.tr('system.ram_warning')}"
            self.system_summary.setStyleSheet("color: #d68a00;")
        elif bool(getattr(self, "_system_readiness_warning", False)):
            summary += f"   ⚠ {self.tr('system.check_details')}"
            self.system_summary.setStyleSheet("color: #d68a00;")
        else:
            self.system_summary.setStyleSheet("")
        self.system_summary.setText(summary)

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
                repository=REPOSITORY_URL,
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
        if self.chat_worker is not None and self.chat_worker.isRunning():
            self.chat_worker.requestInterruption()
            self.server.cancel()
            self.chat_worker.wait(2000)
        self.server.stop()
        event.accept()
