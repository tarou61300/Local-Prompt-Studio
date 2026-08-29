from __future__ import annotations

from collections.abc import Callable
import sqlite3
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QTextCursor
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
    QMenu,
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
from core.chat_attachments import ChatImageAttachment, ChatImageError
from core.chat_renderers import PromptTransferRenderer, ReferenceImageRenderer
from core.config_manager import ConfigManager
from core.history_manager import HistoryManager
from core.inference_backends import BACKEND_VULKAN, backend_spec
from core.llama_manager import LlamaServerManager
from core.literal_content import parse_literal_content
from core.localization import Localization, locale_matches_language
from core.model_manager import inspect_model
from core.profile_loader import ProfileLoader
from core.profile_models import LoadedProfile
from core.prompt_engine import (
    H3Reference,
    PromptEngine,
    PromptSettings,
    REFERENCE_LIMITS,
    parse_task_schema_validation_error,
)
from core.prompt_translation import PromptTranslationService
from core.renderers import (
    ANIMA_HYBRID_OUTPUT_INVALID,
    DANBOORU_OUTPUT_INVALID,
    PROTECTED_TERM_NOT_PRESERVED,
    RenderResult,
    RendererRegistry,
    is_literal_validation_error,
    parse_literal_validation_error,
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

from .chat_page import ChatMessageWidget, ChatPage
from .ime_aware_text_edit import ImeAwarePlaceholderPlainTextEdit
from .prompt_library_page import PromptLibraryPage
from .prompt_translation_dialog import PromptTranslationDialog
from .request_guide import request_guide_entries
from .settings_dialog import SettingsDialog
from .theme import apply_prompt_editor_theme, current_application_theme
from .workers import (
    ChatThread,
    ComfyUISendThread,
    GENERATION_CANCELLED,
    GENERATION_UNKNOWN_ERROR,
    GenerationThread,
    TranslationThread,
)


PROCESSING_OPTIONS = (
    ("profile.style.option.faithful", "Faithful"),
    ("profile.style.option.balanced", "Balanced"),
    ("profile.style.option.creative", "Creative"),
)
CAMERA_OPTIONS = (
    ("video.camera.free", "Free"),
    ("video.camera.static", "Static camera"),
    ("video.camera.push_in", "Slow push-in"),
    ("video.camera.pull_out", "Slow pull-out"),
    ("video.camera.pan", "Pan"),
    ("video.camera.tilt", "Tilt"),
    ("video.camera.tracking", "Tracking"),
    ("video.camera.handheld", "Handheld"),
)
SHOT_OPTIONS = (
    ("video.shot.single", "Single continuous shot"),
    ("video.shot.cuts", "Allow cuts"),
)
MOTION_OPTIONS = (
    ("video.motion.low", "Low"),
    ("video.motion.natural", "Natural"),
    ("video.motion.medium", "Medium"),
    ("video.motion.high", "High"),
)
REFERENCE_KIND_OPTIONS = (
    ("reference.kind.picture", "Picture"),
    ("reference.kind.video", "Video"),
    ("reference.kind.audio", "Audio"),
)
CAMERAS = tuple(value for _key, value in CAMERA_OPTIONS)
SHOTS = tuple(value for _key, value in SHOT_OPTIONS)
MOTIONS = tuple(value for _key, value in MOTION_OPTIONS)


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
COMFYUI_SEND_ERROR_KEYS = {
    code: f"comfyui.send_error.{code}" for code in COMFYUI_SEND_ERROR_MESSAGES
}


_VISUAL_STYLE_KEYS = (
    "unspecified",
    "2d_animation",
    "live_action",
    "3d_cg",
)
_VISUAL_STYLE_SUPPORTED_RENDERERS = frozenset({"minimax_h3"})


def comfyui_send_error_message(
    code: str,
    tr: Callable[..., str] | None = None,
) -> str:
    if tr is not None:
        return tr(COMFYUI_SEND_ERROR_KEYS.get(code, "comfyui.send_error.generic"))
    return COMFYUI_SEND_ERROR_MESSAGES.get(
        code,
        "The text could not be sent to ComfyUI.",
    )


def task_schema_validation_user_message(
    localization: Localization,
    message: str,
) -> str | None:
    details = parse_task_schema_validation_error(message)
    if details is None:
        return None
    task, fields = details
    fields_section = ""
    if fields:
        fields_section = "\n\n" + localization.tr(
            "error.task_schema_validation_fields",
            fields=", ".join(fields),
        )
    return localization.tr(
        "error.task_schema_validation",
        task=task,
        fields_section=fields_section,
    )


def literal_validation_user_message(
    localization: Localization,
    message: str,
) -> str | None:
    if not is_literal_validation_error(message):
        return None
    details = parse_literal_validation_error(message)
    if details is None:
        return localization.tr("error.literal_not_preserved")
    items = []
    for index, item in enumerate(details.missing, start=1):
        items.append(
            localization.tr(
                "error.literal_diagnostic_item",
                index=index,
                source_role=localization.tr(
                    f"error.literal_source.{item.source_role}"
                ),
                detection_type=localization.tr(
                    f"error.literal_detection.{item.detection_type}"
                ),
                character_count=item.character_count,
                short_hash=item.short_hash,
            )
        )
    return localization.tr(
        "error.literal_not_preserved_diagnostic",
        detected_count=details.detected_count,
        missing_count=details.missing_count,
        items="\n\n".join(items),
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
        self.translation_service = PromptTranslationService()
        self.translation_worker: TranslationThread | None = None
        self.translation_dialog: PromptTranslationDialog | None = None
        self._translation_active = False
        self._translation_source_language_code = ""
        self._pending_translation: tuple[int, str, str, bool] | None = None
        self._last_protected_terms: tuple[str, ...] = ()
        self.chat_messages: list[dict[str, Any]] = []
        self._pending_chat_user_message: dict[str, Any] | None = None
        self._pending_chat_message_widget: ChatMessageWidget | None = None
        self._pending_chat_draft = ""
        self._chat_analysis_type = "chat"
        self._chat_retain_attachment = False
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
        self._visual_style_key = "unspecified"
        self._managed_visual_style_block: str | None = None
        self._visual_style_edit_in_progress = False

        self.setWindowTitle(f"{self.tr('app.title')} {APP_DISPLAY_VERSION}")
        self.resize(1120, 820)
        self.setMinimumSize(850, 640)
        self._build_ui()
        self.refresh_theme()
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
        subtitle.setObjectName("app_subtitle")
        subtitle.setStyleSheet("color: palette(placeholder-text);")
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
            "color: palette(placeholder-text); font-size: 11px;"
        )
        self.mode = QComboBox()
        self.mode.setObjectName("profile_task")
        self.processing = self._combo(PROCESSING_OPTIONS)
        self.processing.setObjectName("prompt_style")
        self.prompt_style_help = QLabel()
        self.prompt_style_help.setObjectName("prompt_style_help")
        self.prompt_style_help.setWordWrap(True)
        self.prompt_style_help.setStyleSheet(
            "color: palette(placeholder-text); font-size: 11px;"
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
        self.camera = self._combo(CAMERA_OPTIONS)
        self.shot = self._combo(SHOT_OPTIONS)
        self.motion = self._combo(MOTION_OPTIONS)
        entries = (
            (self.tr("video.duration"), self.duration),
            (self.tr("video.motion"), self.motion),
            (self.tr("video.camera"), self.camera),
            (self.tr("video.shot"), self.shot),
        )
        for index, (label, widget) in enumerate(entries):
            grid.addWidget(QLabel(label), index * 2, 0)
            grid.addWidget(widget, index * 2 + 1, 0)
            widget.setMinimumWidth(180)
        audio_column = QVBoxLayout()
        self.environment_audio = QCheckBox(self.tr("video.audio.environment"))
        self.environment_audio.setChecked(True)
        self.dialogue_audio = QCheckBox(self.tr("video.audio.dialogue"))
        self.dialogue_audio.setChecked(True)
        self.music_audio = QCheckBox(self.tr("video.audio.music"))
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
        grid.addWidget(QLabel(self.tr("video.audio")), 8, 0)
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
        self.mode_controls = QWidget()
        self.mode_controls.setObjectName("mode_controls")
        mode_controls_layout = QHBoxLayout(self.mode_controls)
        mode_controls_layout.setContentsMargins(0, 0, 0, 0)
        mode_controls_layout.setSpacing(8)
        mode_controls_layout.addWidget(self.mode_supplement_toggle)
        self.visual_style_button = QToolButton()
        self.visual_style_button.setObjectName("visual_style_button")
        self.visual_style_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.visual_style_button.setToolTip(self.tr("visual_style.tooltip"))
        self.visual_style_menu = QMenu(self.visual_style_button)
        self.visual_style_action_group = QActionGroup(self.visual_style_menu)
        self.visual_style_action_group.setExclusive(True)
        self.visual_style_actions: dict[str, QAction] = {}
        for key in _VISUAL_STYLE_KEYS:
            action = self.visual_style_menu.addAction(
                self.tr(f"visual_style.option.{key}")
            )
            action.setCheckable(True)
            action.setData(key)
            action.triggered.connect(
                lambda _checked=False, selected=key: self._set_visual_style(selected)
            )
            self.visual_style_action_group.addAction(action)
            self.visual_style_actions[key] = action
        self.visual_style_button.setMenu(self.visual_style_menu)
        mode_controls_layout.addWidget(self.visual_style_button)
        mode_controls_layout.addStretch()
        mode_section_layout.addWidget(self.mode_controls)
        self._update_visual_style_button()
        self.mode_group = QGroupBox(self.tr("mode.notes"))
        mode_group_layout = QVBoxLayout(self.mode_group)
        mode_group_layout.setContentsMargins(6, 8, 6, 6)
        self.mode_notes_scroll = QScrollArea()
        self.mode_notes_scroll.setObjectName("mode_supplement_scroll")
        self.mode_notes_scroll.setWidgetResizable(True)
        self.mode_notes_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.mode_notes_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.mode_notes_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.mode_notes_scroll.setMinimumHeight(140)
        self.mode_notes_scroll.setMaximumHeight(340)
        self.mode_content = QWidget()
        self.mode_content.setObjectName("mode_supplement_content")
        mode_layout = QVBoxLayout(self.mode_content)
        mode_layout.setContentsMargins(3, 3, 3, 3)
        mode_layout.setSpacing(6)
        self.common_note_label = QLabel(self.tr("mode.common_note"))
        self.common_note = QPlainTextEdit()
        self.common_note.setObjectName("common_supplement")
        self.common_note.setMinimumHeight(72)
        self.common_note.setMaximumHeight(90)
        self.common_note.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.common_note.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.start_note_label = QLabel(self.tr("mode.start_note"))
        self.start_note = QPlainTextEdit()
        self.start_note.setMinimumHeight(72)
        self.start_note.setMaximumHeight(90)
        self.start_note.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.start_note.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.end_note_label = QLabel(self.tr("mode.end_note"))
        self.end_note = QPlainTextEdit()
        self.end_note.setMinimumHeight(72)
        self.end_note.setMaximumHeight(90)
        self.end_note.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.end_note.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        mode_layout.addWidget(self.common_note_label)
        mode_layout.addWidget(self.common_note)
        mode_layout.addWidget(self.start_note_label)
        mode_layout.addWidget(self.start_note)
        mode_layout.addWidget(self.end_note_label)
        mode_layout.addWidget(self.end_note)
        self.references = QTableWidget(0, 3)
        self.references.setMinimumHeight(110)
        self.references.setMaximumHeight(150)
        self.references.setHorizontalHeaderLabels(
            [
                self.tr("reference.header.type"),
                self.tr("reference.header.number"),
                self.tr("reference.header.description"),
            ]
        )
        self.references.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        mode_layout.addWidget(self.references)
        self.reference_actions = QWidget()
        ref_actions = QHBoxLayout(self.reference_actions)
        ref_actions.setContentsMargins(0, 0, 0, 0)
        add_ref = QPushButton(self.tr("reference.add"))
        add_ref.clicked.connect(self._add_reference)
        remove_ref = QPushButton(self.tr("reference.remove"))
        remove_ref.clicked.connect(self._remove_reference)
        ref_actions.addWidget(add_ref)
        ref_actions.addWidget(remove_ref)
        ref_actions.addStretch()
        mode_layout.addWidget(self.reference_actions)
        self.mode_notes_scroll.setWidget(self.mode_content)
        mode_group_layout.addWidget(self.mode_notes_scroll)
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
        self.request_group = QGroupBox(self.tr("input.request"))
        self.request_group.setObjectName("request_group")
        self.request_group.setMinimumHeight(185)
        request_layout = QVBoxLayout(self.request_group)
        request_layout.setContentsMargins(9, 12, 9, 9)
        request_layout.setSpacing(6)
        self.request_text = ImeAwarePlaceholderPlainTextEdit()
        self.request_text.setObjectName("request_input_editor")
        self.request_text.setMinimumHeight(105)
        self.request_text.setPlaceholderText(self.tr("input.placeholder"))
        self.request_text.setToolTip(self.tr("input.literal_hint"))
        self.request_text.document().contentsChange.connect(
            self._track_visual_style_request_edit
        )
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
        guide_row = QHBoxLayout(self.literal_hint)
        guide_row.setContentsMargins(0, 0, 0, 0)
        guide_row.addStretch()
        self.request_guide_button = QToolButton()
        self.request_guide_button.setObjectName("request_guide_button")
        self.request_guide_button.setText(self.tr("input.guide"))
        self.request_guide_button.setToolTip(self.tr("input.guide_tooltip"))
        self.request_guide_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        guide_menu = QMenu(self.request_guide_button)
        for entry in request_guide_entries(
            self.tr,
            profile_id=self.profile.manifest.id if self.profile else None,
        ):
            action = guide_menu.addAction(f"{entry.title}: {entry.example}")
            action.setData(entry.key)
            action.triggered.connect(
                lambda _checked=False, key=entry.key: self._insert_request_guide(key)
            )
        self.request_guide_button.setMenu(guide_menu)
        guide_row.addWidget(self.request_guide_button)
        request_layout.addWidget(self.literal_hint)
        self.workspace_splitter.addWidget(self.request_group)

        self.prompt_splitter = QSplitter(Qt.Orientation.Vertical)
        self.prompt_splitter.setObjectName("prompt_splitter")
        self.prompt_splitter.setChildrenCollapsible(False)
        self.prompt_splitter.setMinimumHeight(170)
        self.output_group = QGroupBox(self.tr("output.prompt"))
        self.output_group.setMinimumHeight(150)
        output_layout = QVBoxLayout(self.output_group)
        output_actions = QHBoxLayout()
        output_actions.setContentsMargins(0, 0, 0, 0)
        output_actions.addStretch()
        self.edit_prompt_button = QPushButton()
        self.edit_prompt_button.setObjectName("edit_prompt_translation_button")
        self.edit_prompt_button.setText(self.tr("translation.edit"))
        self.edit_prompt_button.setToolTip(self.tr("translation.edit_tooltip"))
        self.edit_prompt_button.setMinimumSize(150, 30)
        self.edit_prompt_button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.edit_prompt_button.clicked.connect(self._open_prompt_translation)
        output_actions.addWidget(self.edit_prompt_button)
        output_layout.addLayout(output_actions)
        self.output_text = QPlainTextEdit()
        self.output_text.setObjectName("prompt_output_editor")
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
        self.send_comfyui_button = QPushButton(self.tr("comfyui.send"))
        self.send_comfyui_button.setToolTip(self.tr("comfyui.send_current"))
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
        self.prompt_library_page = PromptLibraryPage(
            self.tr,
            data_dir=self.config_manager.data_dir,
            profiles=self._available_profiles(),
            tag_rows=self.config.prompt_library_tag_rows,
            result_rows=self.config.prompt_library_result_rows,
            detail_minimum_lines=self.config.prompt_library_detail_lines,
        )
        self.prompt_library_page.setObjectName("prompt_library_page")
        self.main_tabs = QTabWidget()
        self.main_tabs.setObjectName("main_mode_tabs")
        self.main_tabs.tabBar().setObjectName("main_mode_tab_bar")
        self.main_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.main_tabs.setStyleSheet(MAIN_MODE_TABS_STYLE)
        self.main_tabs.addTab(self.prompt_page, "")
        self.main_tabs.addTab(self.chat_page, self.tr("tabs.ai_chat"))
        self.main_tabs.addTab(
            self.prompt_library_page,
            self.tr("tabs.prompt_library"),
        )
        self.main_tabs.currentChanged.connect(self._main_tab_changed)
        root.addWidget(self.main_tabs, 1)
        self.setCentralWidget(central)
        self.output_text.textChanged.connect(self._update_send_button_state)
        self.negative_output_text.textChanged.connect(self._update_send_button_state)
        self.profile_category.currentIndexChanged.connect(self._profile_category_changed)
        self.profile_model.currentIndexChanged.connect(self._profile_model_changed)
        self.profile_variant.currentIndexChanged.connect(self._profile_variant_changed)
        self.mode.currentTextChanged.connect(self._task_changed)
        self.processing.currentIndexChanged.connect(
            lambda _index: self._update_prompt_style_help()
        )
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
        self.chat_page.image_requested.connect(self._select_chat_image)
        self.chat_page.image_path_requested.connect(self._attach_chat_image)
        self.chat_page.analyze_requested.connect(self._analyze_attached_image)
        self.chat_page.settings_requested.connect(self._open_chat_model_settings)
        self.chat_page.cancel_requested.connect(self._cancel_chat)
        self.chat_page.new_chat_requested.connect(self._new_chat)
        self.chat_page.target_profile_requested.connect(
            self._select_chat_target_profile
        )
        self.chat_page.target_task_requested.connect(self._select_chat_target_task)
        self.chat_page.transfer_requested.connect(self._transfer_chat_response)
        self.chat_page.transfer_prepare_requested.connect(
            self._prepare_chat_transfer
        )
        self.chat_page.open_prompt_requested.connect(self._open_prompt_destination)
        self.chat_page.unload_requested.connect(self._unload_model)
        self._sync_prompt_target_ui()
        self._update_chat_model_status()
        self._update_send_button_state()
        self._update_unload_button_state()
        self._update_mode_fields(self.mode.currentText())

    def refresh_theme(self) -> None:
        app = QApplication.instance()
        theme = current_application_theme(app) if app is not None else None
        for editor in (self.request_text, self.output_text):
            apply_prompt_editor_theme(editor, theme)

    def _combo(self, values: tuple[tuple[str, str], ...]) -> QComboBox:
        combo = QComboBox()
        for key, value in values:
            combo.addItem(self.tr(key), value)
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
        # On short screens keep both work editors usable while the three
        # supplement editors retain their own readable, scrollable height.
        if expanded:
            self.request_group.setMinimumHeight(145)
            self.request_text.setMinimumHeight(65)
            self.prompt_splitter.setMinimumHeight(110)
            self.output_group.setMinimumHeight(95)
            self.output_text.setMinimumHeight(65)
        else:
            self.request_group.setMinimumHeight(185)
            self.request_text.setMinimumHeight(105)
            self.prompt_splitter.setMinimumHeight(170)
            self.output_group.setMinimumHeight(150)
            self.output_text.setMinimumHeight(105)
        self._sync_mode_section_height()

    def _sync_mode_section_height(self) -> None:
        if self.mode_group.isVisible() and hasattr(self, "mode_content"):
            self.mode_content.layout().activate()
            content_height = self.mode_content.sizeHint().height() + 6
            workspace_reserve = (
                self.request_group.minimumHeight()
                + self.prompt_splitter.minimumHeight()
                + self.workspace_splitter.handleWidth()
                + 24
            )
            available_scroll_height = max(
                140,
                self.right_workspace.height()
                - workspace_reserve
                - self.mode_controls.sizeHint().height()
                - 34,
            )
            scroll_height = min(content_height, available_scroll_height, 340)
            self.mode_notes_scroll.setMinimumHeight(scroll_height)
            self.mode_notes_scroll.setMaximumHeight(scroll_height)
            group_chrome_height = self.mode_group.fontMetrics().lineSpacing() + 20
            group_height = scroll_height + group_chrome_height
            self.mode_group.setMinimumHeight(group_height)
            self.mode_group.setMaximumHeight(group_height)
        layout = self.mode_section.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
            if self.mode_group.isVisible():
                section_height = (
                    self.mode_controls.sizeHint().height()
                    + layout.spacing()
                    + self.mode_group.minimumHeight()
                )
            else:
                section_height = self.mode_controls.sizeHint().height()
        else:
            section_height = self.mode_section.sizeHint().height()
        self.mode_section.setMinimumHeight(section_height)
        self.mode_section.setMaximumHeight(section_height)
        self.mode_section.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "mode_section"):
            QTimer.singleShot(0, self._sync_mode_section_height)

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
        self._update_translation_button_visibility()
        if separate_negative:
            self.send_comfyui_button.setToolTip(
                self.tr("comfyui.send_positive_only")
            )
        else:
            self.negative_output_text.clear()
            self.send_comfyui_button.setToolTip(
                self.tr("comfyui.send_current")
            )

    def _update_translation_button_visibility(self) -> None:
        visible = bool(
            self.profile is not None
            and not locale_matches_language(
                self.localization.locale_id,
                self.profile.manifest.output_language,
            )
        )
        self.edit_prompt_button.setVisible(visible)

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

    def _main_tab_changed(self, index: int) -> None:
        if index == 2:
            self.prompt_library_page.activate()

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
            ("request", self.tr("chat.destination.request")),
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
                processing or str(self.processing.currentData() or "Faithful"),
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
                self.tr("error.portable_write"),
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
                self.tr("error.portable_write"),
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
        self.reference_actions.setVisible(refs_visible)
        self._mode_supplement_available = True
        self.mode_section.setVisible(True)
        renderer_id = self.profile.manifest.renderer if self.profile else ""
        self.visual_style_button.setVisible(
            renderer_id in _VISUAL_STYLE_SUPPORTED_RENDERERS
        )
        self.mode_group.setVisible(
            self._mode_supplement_available
            and self.mode_supplement_toggle.isChecked()
        )
        self._sync_mode_section_height()

    def _track_visual_style_request_edit(
        self,
        position: int,
        chars_removed: int,
        chars_added: int,
    ) -> None:
        _ = chars_removed, chars_added
        managed = self._managed_visual_style_block
        if (
            not self._visual_style_edit_in_progress
            and managed is not None
            and position < len(managed)
        ):
            self._managed_visual_style_block = None

    def _update_visual_style_button(self) -> None:
        value = self.tr(f"visual_style.option.{self._visual_style_key}")
        self.visual_style_button.setText(
            self.tr("visual_style.current", value=value)
        )
        for key, action in self.visual_style_actions.items():
            action.setChecked(key == self._visual_style_key)

    def _set_visual_style(self, key: str) -> None:
        if key not in _VISUAL_STYLE_KEYS:
            return
        current = self.request_text.toPlainText()
        old_block = self._managed_visual_style_block
        old_block_is_intact = bool(old_block and current.startswith(old_block))
        new_block = (
            ""
            if key == "unspecified"
            else f'{self.tr(f"visual_style.request.{key}")}\n'
        )
        edit_cursor = QTextCursor(self.request_text.document())
        edit_cursor.setPosition(0)
        self._visual_style_edit_in_progress = True
        try:
            if old_block_is_intact and old_block is not None:
                edit_cursor.setPosition(
                    len(old_block),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                edit_cursor.insertText(new_block)
            elif new_block:
                edit_cursor.insertText(new_block)
        finally:
            self._visual_style_edit_in_progress = False
        self._managed_visual_style_block = new_block or None
        self._visual_style_key = key
        self._update_visual_style_button()

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
        for key, value in REFERENCE_KIND_OPTIONS:
            kind.addItem(self.tr(key), value)
        self.references.setCellWidget(row, 0, kind)
        number = QSpinBox()
        number.setRange(1, 9)
        number.setValue(row + 1)
        kind.currentIndexChanged.connect(
            lambda index, combo=kind, box=number: box.setMaximum(
                REFERENCE_LIMITS[str(combo.itemData(index))]
            )
        )
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
                refs.append(
                    H3Reference(
                        str(kind_widget.currentData()),
                        number_widget.value(),
                        description_item.text() if description_item else "",
                    )
                )
        return PromptSettings(
            mode=self.mode.currentText(),
            duration=self.duration.value(),
            processing=str(self.processing.currentData()),
            camera=str(self.camera.currentData()),
            shot=str(self.shot.currentData()),
            motion=str(self.motion.currentData()),
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

    def _insert_request_guide(self, key: str) -> None:
        entries = request_guide_entries(
            self.tr,
            profile_id=self.profile.manifest.id if self.profile else None,
        )
        entry = next((item for item in entries if item.key == key), None)
        if entry is None:
            return
        self._append_supplement(self.request_text, entry.example)
        self.request_text.setFocus(Qt.FocusReason.OtherFocusReason)

    def _transfer_chat_response(self, text: str, destination: str) -> None:
        widgets = {
            "request": self.request_text,
            "common": self.common_note,
            "start": self.start_note,
            "end": self.end_note,
        }
        start_visible, end_visible, _refs_visible = self._supplement_capabilities(
            self.mode.currentText()
        )
        allowed = {"request", "common"}
        if start_visible:
            allowed.add("start")
        if end_visible:
            allowed.add("end")
        if destination not in allowed:
            return
        self._append_supplement(widgets[destination], text)
        labels = {
            "request": self.tr("chat.destination.request"),
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
            "request": self.request_text,
            "common": self.common_note,
            "start": self.start_note,
            "end": self.end_note,
        }
        widget = widgets.get(destination)
        if widget is None:
            return
        self.main_tabs.setCurrentWidget(self.prompt_page)
        if destination != "request":
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

    def _select_chat_image(self) -> None:
        if (
            self._generation_active
            or self._chat_active
            or self._translation_active
        ):
            return
        if not self._chat_image_attachment_allowed():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("chat.image.choose"),
            "",
            self.tr("chat.image.filter"),
        )
        if path:
            self._attach_chat_image(path)

    def _chat_image_attachment_allowed(self) -> bool:
        self.config = self.config_manager.load()
        self._update_chat_model_status()
        model_path = self.config.effective_chat_model_path().strip()
        mmproj_path = self.config.mmproj_for_model(model_path) if model_path else ""
        if not mmproj_path:
            self.chat_page.show_mmproj_guidance(self.tr("chat.image.no_mmproj"))
            return False
        if not Path(mmproj_path).is_file():
            self.chat_page.show_mmproj_guidance(self.tr("chat.image.mmproj_missing"))
            return False
        state_reader = getattr(self.server, "multimodal_state_for", None)
        if callable(state_reader) and state_reader(model_path, mmproj_path) == "unsupported":
            self.chat_page.show_mmproj_guidance(self.tr("chat.image.unsupported"))
            return False
        return True

    def _attach_chat_image(self, path: str) -> None:
        if (
            self._generation_active
            or self._chat_active
            or self._translation_active
        ):
            return
        if not self._chat_image_attachment_allowed():
            return
        try:
            attachment = ChatImageAttachment.from_file(path)
        except ChatImageError as exc:
            key = {
                "CHAT_IMAGE_UNSUPPORTED_FORMAT": "chat.error.image_format",
                "CHAT_IMAGE_READ_FAILED": "chat.error.image_read",
                "CHAT_IMAGE_DECODE_FAILED": "chat.error.image_decode",
                "CHAT_IMAGE_ANIMATED_WEBP_UNSUPPORTED": "chat.error.animated_webp",
            }.get(exc.code, "chat.error.image_decode")
            self.chat_page.set_status(self.tr(key), error=True)
            return
        self.chat_page.set_attachment(attachment)
        self.chat_page.set_status("")

    def _analyze_attached_image(self, analysis_type: str) -> None:
        attachment = self.chat_page.attachment
        if (
            attachment is None
            or self._generation_active
            or self._chat_active
            or self._translation_active
        ):
            return
        if analysis_type == "reference_image":
            display_text = self.tr("chat.reference_analysis")
            engine: ChatEngine = ReferenceImageRenderer()
        else:
            display_text = self.tr("chat.normal_analysis_instruction")
            engine = ChatEngine(
                image_only_instruction=self.tr("chat.image_only_instruction")
            )
            analysis_type = "normal_image"
        self._send_chat_message(
            display_text,
            attachment,
            engine=engine,
            preserve_draft=True,
            retain_attachment=True,
            analysis_type=analysis_type,
        )

    def _open_chat_model_settings(self) -> None:
        self._open_settings(focus_chat_model=True)

    def _send_chat_message(
        self,
        text: str,
        attachment: ChatImageAttachment | None = None,
        *,
        engine: ChatEngine | None = None,
        preserve_draft: bool = False,
        retain_attachment: bool = False,
        analysis_type: str = "chat",
    ) -> None:
        if (
            self._generation_active
            or self._chat_active
            or self._translation_active
        ):
            return
        message = text.strip()
        if not message and attachment is None:
            return
        self.config = self.config_manager.load()
        self._update_chat_model_status()
        user_message: dict[str, Any] = {"role": "user", "content": message}
        if attachment is not None:
            user_message["image"] = attachment
        self.chat_messages.append(user_message)
        self._pending_chat_user_message = user_message
        self._pending_chat_draft = self.chat_page.input_text.toPlainText()
        self._chat_analysis_type = analysis_type
        self._chat_retain_attachment = retain_attachment
        self._pending_chat_message_widget = self.chat_page.add_message(
            "user",
            message,
            image_filename=attachment.filename if attachment is not None else "",
        )
        if not preserve_draft:
            self.chat_page.input_text.clear()
        self.chat_worker = ChatThread(
            engine=engine
            or ChatEngine(
                image_only_instruction=self.tr("chat.image_only_instruction")
            ),
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

    def _prepare_chat_transfer(self, source_text: str) -> None:
        if (
            not source_text.strip()
            or self._generation_active
            or self._chat_active
            or self._translation_active
        ):
            return
        self.config = self.config_manager.load()
        self._chat_analysis_type = "prompt_transfer"
        self._chat_retain_attachment = True
        self.chat_worker = ChatThread(
            engine=PromptTransferRenderer(),
            server=self.server,
            config=self.config,
            conversation=[{"role": "user", "content": source_text}],
            mock_mode=self.mock_mode,
            parent=self,
        )
        self.chat_worker.status_changed.connect(self._set_chat_status)
        self.chat_worker.result_ready.connect(self._transfer_render_complete)
        self.chat_worker.error_occurred.connect(self._chat_error)
        self.chat_worker.finished.connect(self._chat_finished)
        self._chat_active = True
        self.chat_page.set_status(self.tr("chat.status.preparing_transfer"))
        self._update_llm_controls()
        self.chat_worker.start()

    def _transfer_render_complete(self, transfer_payload: str) -> None:
        self.chat_page.open_transfer_panel(transfer_payload)
        self.chat_page.set_status(self.tr("chat.status.transfer_ready"))

    def _cancel_chat(self) -> None:
        if self.chat_worker is not None and self.chat_worker.isRunning():
            self.chat_worker.requestInterruption()
            self.server.cancel()
            self.chat_page.set_status(self.tr("chat.error.cancelled"))

    def _new_chat(self) -> None:
        if self._chat_active:
            return
        self.chat_messages.clear()
        self._pending_chat_user_message = None
        self._pending_chat_message_widget = None
        self._pending_chat_draft = ""
        self.chat_page.clear_messages()
        self.chat_page.input_text.clear()
        self.chat_page.set_status("")

    def _set_chat_status(self, status: str) -> None:
        self.chat_page.set_status(self.tr(status))

    def _chat_complete(self, response: str) -> None:
        reference = self._chat_analysis_type == "reference_image"
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": response,
        }
        if reference:
            assistant_message.update(
                {
                    "analysis_type": "reference_image",
                    "transfer_payload": response,
                    "transfer_ready": True,
                }
            )
        self.chat_messages.append(assistant_message)
        self.chat_page.add_message(
            "assistant",
            response,
            transfer_payload=response if reference else "",
            transfer_ready=reference,
            analysis_type="reference_image" if reference else "chat",
        )
        self._pending_chat_user_message = None
        self._pending_chat_message_widget = None
        self._pending_chat_draft = ""
        if not self._chat_retain_attachment:
            self.chat_page.clear_attachment()
        self._update_chat_model_status()
        self.chat_page.set_status(self.tr("chat.status.complete"))

    def _chat_error(self, error: str) -> None:
        pending = self._pending_chat_user_message
        if pending is not None and pending.get("image") is not None:
            if self.chat_messages and self.chat_messages[-1] is pending:
                self.chat_messages.pop()
            widget = self._pending_chat_message_widget
            if widget is not None:
                self.chat_page.remove_message(widget)
            restored_draft = self._pending_chat_draft
            if not restored_draft and self._chat_analysis_type == "chat":
                restored_draft = str(pending.get("content", ""))
            self.chat_page.input_text.setPlainText(restored_draft)
        self._pending_chat_user_message = None
        self._pending_chat_message_widget = None
        self._pending_chat_draft = ""
        if error == "CHAT_CONTEXT_OVERFLOW":
            message = self.tr("chat.error.context")
        elif error == "CHAT_CANCELLED":
            message = self.tr("chat.error.cancelled")
        elif error == "CHAT_MODEL_LOAD_FAILED":
            message = self.tr("chat.error.model_load")
        elif error == "CHAT_MMPROJ_LOAD_FAILED":
            message = self.tr("chat.error.mmproj_load")
        elif error == "CHAT_IMAGE_UNSUPPORTED":
            message = self.tr("chat.error.image_unsupported")
        elif error == "CHAT_IMAGE_DECODE_FAILED":
            message = self.tr("chat.error.image_decode")
        elif error == "CHAT_IMAGE_REQUEST_FAILED":
            message = self.tr("chat.error.image_request")
        elif error == "REFERENCE_IMAGE_OUTPUT_INVALID":
            message = self.tr("chat.error.reference_output")
        elif error == "TRANSFER_OUTPUT_INVALID":
            message = self.tr("chat.error.transfer_output")
        else:
            message = error or self.tr("chat.error.generic")
        self._update_chat_model_status()
        self.chat_page.set_status(message, error=True)

    def _chat_finished(self) -> None:
        self._chat_active = False
        self._chat_analysis_type = "chat"
        self._chat_retain_attachment = False
        finished_worker = self.chat_worker
        self.chat_worker = None
        if finished_worker is not None:
            finished_worker.deleteLater()
        self._update_llm_controls()
        self._update_chat_model_status()
        self._refresh_memory_display()

    def _open_prompt_translation(self) -> None:
        original = self.output_text.toPlainText()
        if (
            not self.edit_prompt_button.isVisible()
            or self.profile is None
            or not original.strip()
            or self._generation_active
            or self._chat_active
            or self._translation_active
        ):
            return
        self._translation_source_language_code = self.profile.manifest.output_language
        dialog = PromptTranslationDialog(
            self.tr,
            original,
            protected_terms=self._last_protected_terms,
            parent=self,
        )
        dialog.translation_requested.connect(self._translation_requested)
        dialog.finished.connect(self._translation_dialog_finished)
        self.translation_dialog = dialog
        self._translation_active = True
        self._update_llm_controls()
        self._update_send_button_state()
        dialog.open()

    def _translation_requested(
        self,
        revision: int,
        direction: str,
        source_text: str,
        structure_protection: bool,
    ) -> None:
        if self.translation_dialog is None:
            return
        request = (revision, direction, source_text, structure_protection)
        if self.translation_worker is not None:
            self._pending_translation = request
            return
        self._start_translation(*request)

    def _start_translation(
        self,
        revision: int,
        direction: str,
        source_text: str,
        structure_protection: bool,
    ) -> None:
        if self.translation_dialog is None:
            return
        self.config = self.config_manager.load()
        self.translation_dialog.mark_translating(revision)
        self.translation_worker = TranslationThread(
            service=self.translation_service,
            server=self.server,
            config=self.config,
            source_text=source_text,
            direction=direction,
            protected_terms=self._last_protected_terms,
            structure_protection=structure_protection,
            revision=revision,
            mock_mode=self.mock_mode,
            source_language_code=self._translation_source_language_code,
            ui_locale_id=self.localization.locale_id,
            parent=self,
        )
        self.translation_worker.status_changed.connect(
            self._set_translation_status
        )
        self.translation_worker.result_ready.connect(
            self._translation_complete
        )
        self.translation_worker.error_occurred.connect(
            self._translation_error
        )
        self.translation_worker.finished.connect(self._translation_finished)
        self._translation_active = True
        self._update_llm_controls()
        self.translation_worker.start()

    def _set_translation_status(self, status: str) -> None:
        if self.translation_dialog is not None:
            self.translation_dialog.status_label.setText(self.tr(status))

    def _translation_complete(
        self,
        revision: int,
        direction: str,
        translated: str,
    ) -> None:
        if self.translation_dialog is not None:
            self.translation_dialog.apply_translation_result(
                revision,
                direction,
                translated,
            )

    def _translation_error(self, revision: int, code: str) -> None:
        if self.translation_dialog is not None:
            self.translation_dialog.apply_translation_error(revision, code)

    def _translation_finished(self) -> None:
        finished_worker = self.translation_worker
        self.translation_worker = None
        if finished_worker is not None:
            finished_worker.deleteLater()
        pending = self._pending_translation
        self._pending_translation = None
        if pending is not None and self.translation_dialog is not None:
            self._start_translation(*pending)
            return
        if self.translation_dialog is None:
            self._translation_active = False
        self._update_llm_controls()
        self._update_send_button_state()
        self._update_unload_button_state()
        self._refresh_memory_display()

    def _translation_dialog_finished(self, result: int) -> None:
        dialog = self.translation_dialog
        if dialog is None:
            return
        if result:
            self.output_text.setPlainText(dialog.original_text())
            self.status_label.setText(self.tr("translation.status.applied"))
        self.translation_dialog = None
        self._pending_translation = None
        if (
            not result
            and self.translation_worker is not None
            and self.translation_worker.isRunning()
        ):
            self.translation_worker.requestInterruption()
            self.server.cancel()
        if self.translation_worker is None:
            self._translation_active = False
        dialog.deleteLater()
        self._update_llm_controls()
        self._update_send_button_state()

    def _update_llm_controls(self) -> None:
        any_busy = (
            self._generation_active
            or self._chat_active
            or self._translation_active
        )
        self.chat_page.set_busy(
            any_llm_busy=any_busy,
            chat_busy=self._chat_active,
        )
        if self._chat_active or self._translation_active:
            self.generate_button.setEnabled(False)
            self.regenerate_button.setEnabled(False)
        elif not self._generation_active and self._send_worker is None:
            self.generate_button.setEnabled(True)
            self.regenerate_button.setEnabled(True)
        self._update_unload_button_state()

    def _update_send_button_state(self) -> None:
        generation_idle = not self._generation_active and not self._translation_active
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
        self.edit_prompt_button.setEnabled(
            bool(self.output_text.toPlainText())
            and not self._generation_active
            and not self._chat_active
            and not self._translation_active
        )

    def _update_unload_button_state(self) -> None:
        enabled = (
            not self._generation_active
            and not self._chat_active
            and not self._translation_active
            and self.server.is_owned_server_running
        )
        self.unload_model_button.setEnabled(enabled)
        if hasattr(self, "chat_page"):
            self.chat_page.set_unload_enabled(enabled)

    def _update_chat_model_status(self) -> None:
        if not hasattr(self, "chat_page"):
            return
        model_path = self.config.effective_chat_model_path().strip()
        model_name = Path(model_path).name if model_path else ""
        mmproj_path = self.config.mmproj_for_model(model_path) if model_path else ""
        if not mmproj_path:
            image_state = "unset"
        elif Path(mmproj_path).is_file():
            state_reader = getattr(self.server, "multimodal_state_for", None)
            image_state = (
                state_reader(model_path, mmproj_path)
                if callable(state_reader)
                else None
            ) or "configured"
        else:
            image_state = "load_error"
        self.chat_page.set_model_status(
            model_name=model_name,
            model_path=model_path,
            image_state=image_state,
            mmproj_path=mmproj_path,
        )

    def _unload_model(self) -> None:
        if (
            self._generation_active
            or self._chat_active
            or self._translation_active
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
            self.chat_page.set_status(self.tr("model.unloaded"))
        finally:
            self._update_unload_button_state()
            self._refresh_memory_display()

    def _invalidate_generation_output(self) -> None:
        self.output_text.clear()
        self.negative_output_text.clear()
        self._update_send_button_state()

    def _set_send_conflicting_actions_enabled(self, enabled: bool) -> None:
        llm_idle = not self._chat_active and not self._translation_active
        self.generate_button.setEnabled(enabled and llm_idle)
        self.regenerate_button.setEnabled(enabled and llm_idle)
        self.settings_action.setEnabled(enabled)

    def _send_to_comfyui(self) -> None:
        if (
            self._send_worker is not None
            or self._generation_active
            or self._translation_active
        ):
            return
        text = self.output_text.toPlainText()
        if not text:
            self._update_send_button_state()
            return
        config = self.config_manager.load()
        try:
            service = self._bridge_service_factory(config.comfyui_url)
        except ComfyUIBridgeError as exc:
            self.status_label.setText(comfyui_send_error_message(exc.code, self.tr))
            return
        except Exception:
            self.status_label.setText(
                comfyui_send_error_message("bridge_unavailable", self.tr)
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
        self.status_label.setText(self.tr("comfyui.sending"))
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
            self.status_label.setText(self.tr("comfyui.sent"))
        elif self._send_error_code is not None:
            self.status_label.setText(
                comfyui_send_error_message(self._send_error_code, self.tr)
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
        if self._chat_active or self._translation_active:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        # Always sample on the button press. The timer value is informational only.
        self._refresh_memory_display()
        request = self.request_text.toPlainText().strip()
        if not request:
            QMessageBox.information(
                self, self.tr("input.request"), self.tr("input.required")
            )
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
                self.tr("error.runtime_missing_title"),
                self.tr(
                    "error.runtime_missing",
                    backend=backend_spec(self.config.inference_backend).display_name,
                ),
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
                    self.tr("error.vulkan_not_detected_title"),
                    self.tr("error.vulkan_not_detected"),
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
                self.tr("memory.warning.title"),
                format_assessment_details(assessment, self.tr)
                + "\n\n"
                + "\n\n".join(warnings)
                + "\n\n"
                + self.tr("memory.warning.continue"),
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Ok:
                self._refresh_memory_display()
                return
        if self.profile is None:
            QMessageBox.warning(
                self,
                self.tr("error.profile_invalid_title"),
                self.tr("error.profile_invalid"),
            )
            return
        variant_id = str(self.profile_variant.currentData() or self.profile.manifest.default_variant)
        prompt_settings = self._collect_settings()
        self._last_protected_terms = tuple(
            dict.fromkeys(
                (
                    *prompt_settings.protected_terms,
                    *(item.text for item in parse_literal_content(request)),
                )
            )
        )
        self.worker = GenerationThread(
            engine=PromptEngine(self.skill_manager, self.profile, variant_id),
            server=self.server,
            config=self.config,
            request_text=request,
            settings=prompt_settings,
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
            self.status_label.setText(self.tr("status.cancelling"))
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
                processing_mode=str(self.processing.currentData()),
                profile_hash=self.profile.content_hash if self.profile else "",
                common_supplement=self.common_note.toPlainText(),
            )
        except (OSError, sqlite3.Error):
            QMessageBox.warning(
                self,
                self.tr("error.history_save_title"),
                self.tr("error.portable_write"),
            )

    def _generation_error(self, message: str) -> None:
        self._invalidate_generation_output()
        self.status_label.setText(self.tr("status.error"))
        schema_message = task_schema_validation_user_message(
            self.localization,
            message,
        )
        literal_message = literal_validation_user_message(
            self.localization,
            message,
        )
        if schema_message is not None:
            message = schema_message
        elif literal_message is not None:
            message = literal_message
        elif message == PROTECTED_TERM_NOT_PRESERVED:
            message = self.tr("error.protected_not_preserved")
        elif message == DANBOORU_OUTPUT_INVALID:
            message = self.tr("error.danbooru_output_invalid")
        elif message == ANIMA_HYBRID_OUTPUT_INVALID:
            message = self.tr("error.anima_hybrid_output_invalid")
        elif message == GENERATION_CANCELLED:
            message = self.tr("error.generation_cancelled")
        elif message == GENERATION_UNKNOWN_ERROR:
            message = self.tr("error.generation_unknown")
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
                f" / {self.tr('readiness.device')}: {selected.display_name} / "
                f"{self.tr(f'backend.memory_classification.{selected.memory_classification}')}"
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
            tr=self.tr,
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
            self.memory_status.setText(format_memory_status(memory, self.tr))
            self.memory_status.setStyleSheet("")
            self._system_memory_warning = False
            self._update_system_summary()
            self._update_unload_button_state()
            return
        text = (
            format_memory_status(memory, self.tr)
            + " / "
            + self.tr(
                "memory.summary",
                model=assessment.model_name,
                filename=assessment.model_filename,
                size=assessment.model_size_gib,
                backend=backend_spec(self.config.inference_backend).display_name,
                context=assessment.context_size,
                estimated=assessment.estimated_required_gib,
            )
        )
        if self.config.inference_backend == BACKEND_VULKAN:
            text += "\n" + self.tr("memory.gpu_not_added")
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

    def _open_settings(self, _checked: bool = False, *, focus_chat_model: bool = False) -> None:
        if SettingsDialog(
            self.config_manager,
            self.project_root,
            self,
            localization=self.localization,
            focus_chat_model=focus_chat_model,
        ).exec():
            self.config = self.config_manager.load()
            self.skill_manager = SkillManager(
                self.config.skill_location
                or self.config_manager.data_dir / "skills" / "h3-prompt-writing"
            )
            self.prompt_library_page.apply_display_settings(
                self.config.prompt_library_tag_rows,
                self.config.prompt_library_result_rows,
                self.config.prompt_library_detail_lines,
            )
            self._refresh_readiness()
            self._refresh_memory_display()
            self._update_chat_model_status()

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
            self.tr("save.text_filter"),
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
        if self.translation_worker is not None and self.translation_worker.isRunning():
            self.translation_worker.requestInterruption()
            self.server.cancel()
            self.translation_worker.wait(2000)
        self.chat_messages.clear()
        self._pending_chat_user_message = None
        self._pending_chat_message_widget = None
        if hasattr(self, "chat_page"):
            self.chat_page.clear_attachment()
        self.server.stop()
        event.accept()
