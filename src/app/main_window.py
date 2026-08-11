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
from core.model_manager import inspect_model
from core.prompt_engine import H3Reference, PromptEngine, PromptSettings, REFERENCE_LIMITS
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
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.config_manager = config_manager
        self.config = config_manager.load()
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

        self.setWindowTitle(f"{PRODUCT_NAME} {APP_DISPLAY_VERSION}")
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
        toolbar.setMovable(False)
        self.settings_action = QAction("設定", self)
        self.settings_action.triggered.connect(self._open_settings)
        toolbar.addAction(self.settings_action)
        about_action = QAction("このアプリについて", self)
        about_action.triggered.connect(self._show_about)
        toolbar.addAction(about_action)
        self.addToolBar(toolbar)

        central = QWidget()
        root = QVBoxLayout(central)
        title = QLabel("MMH3 Prompt Builder")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        root.addWidget(title)
        subtitle = QLabel("Local Prompt Builder for MiniMax H3")
        subtitle.setStyleSheet("color: palette(mid);")
        root.addWidget(subtitle)
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        root.addWidget(self.readiness)
        self.memory_status = QLabel()
        self.memory_status.setWordWrap(True)
        root.addWidget(self.memory_status)

        settings_group = QGroupBox("動画設定")
        grid = QGridLayout(settings_group)
        self.mode = self._combo(("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"))
        self.mode.currentTextChanged.connect(self._update_mode_fields)
        self.duration = QSpinBox()
        self.duration.setRange(4, 15)
        self.duration.setValue(10)
        self.duration.setSuffix(" 秒")
        self.processing = self._combo(("Faithful", "Balanced", "Creative"))
        self.camera = self._combo(CAMERAS)
        self.shot = self._combo(SHOTS)
        self.motion = self._combo(MOTIONS)
        entries = (
            ("Mode", self.mode),
            ("Duration", self.duration),
            ("Prompt Processing", self.processing),
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
        root.addWidget(settings_group)

        self.mode_group = QGroupBox("モード補足（ファイル自体の解析・送信は行いません）")
        mode_layout = QVBoxLayout(self.mode_group)
        self.start_note_label = QLabel("開始画像についての補足（任意）")
        self.start_note = QPlainTextEdit()
        self.start_note.setMaximumHeight(70)
        self.end_note_label = QLabel("終了画像についての補足（任意）")
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
        add_ref = QPushButton("Referenceを追加")
        add_ref.clicked.connect(self._add_reference)
        remove_ref = QPushButton("選択行を削除")
        remove_ref.clicked.connect(self._remove_reference)
        ref_actions.addWidget(add_ref)
        ref_actions.addWidget(remove_ref)
        ref_actions.addStretch()
        mode_layout.addLayout(ref_actions)
        root.addWidget(self.mode_group)

        splitter = QSplitter(Qt.Vertical)
        request_group = QGroupBox("Request（日本語・英語）")
        request_layout = QVBoxLayout(request_group)
        self.request_text = QPlainTextEdit()
        self.request_text.setPlaceholderText("例：雨の東京の路地を、赤い傘の女性がゆっくり歩く。台詞は「もうすぐ着くよ」")
        request_layout.addWidget(self.request_text)
        splitter.addWidget(request_group)

        output_group = QGroupBox("H3 Prompt（編集可能）")
        output_layout = QVBoxLayout(output_group)
        self.output_text = QPlainTextEdit()
        output_layout.addWidget(self.output_text)
        splitter.addWidget(output_group)
        splitter.setSizes([250, 250])
        root.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        self.generate_button = QPushButton("Generate H3 Prompt")
        self.generate_button.clicked.connect(self.generate)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_generation)
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(lambda: self.output_text.selectAll() or self.output_text.copy())
        save_button = QPushButton("Save as TXT")
        save_button.clicked.connect(self._save_output)
        self.send_comfyui_button = QPushButton("Send to ComfyUI")
        self.send_comfyui_button.setToolTip(
            "Send the current edited output to the selected ComfyUI text field."
        )
        self.send_comfyui_button.clicked.connect(self._send_to_comfyui)
        self.regenerate_button = QPushButton("Regenerate")
        self.regenerate_button.clicked.connect(self.generate)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_text)
        for button in (
            self.generate_button,
            self.cancel_button,
            copy_button,
            save_button,
            self.send_comfyui_button,
            self.regenerate_button,
            clear,
        ):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.status_label = QLabel("準備完了")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.setCentralWidget(central)
        self.output_text.textChanged.connect(self._update_send_button_state)
        self._update_send_button_state()
        self._update_mode_fields(self.mode.currentText())

    @staticmethod
    def _combo(values: tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        return combo

    def _update_mode_fields(self, mode: str) -> None:
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
        self.send_comfyui_button.setEnabled(
            bool(self.output_text.toPlainText())
            and self._send_worker is None
            and not self._generation_active
            and not self._send_close_requested
        )

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
        if not self.skill_manager.status().valid:
            QMessageBox.warning(self, "Skill未導入", "MiniMax H3 Prompt Skillが未導入または破損しています。初回セットアップまたは設定を開いてください。")
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
        self.worker = GenerationThread(
            engine=PromptEngine(self.skill_manager),
            server=self.server,
            config=self.config,
            request_text=request,
            settings=self._collect_settings(),
            mock_mode=self.mock_mode,
            parent=self,
        )
        self.worker.status_changed.connect(self.status_label.setText)
        self.worker.result_ready.connect(self._generation_complete)
        self.worker.error_occurred.connect(self._generation_error)
        self.worker.finished.connect(self._generation_finished)
        self._generation_active = True
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

    def _generation_complete(self, output: str) -> None:
        self.output_text.setPlainText(output)
        self.status_label.setText("完了")
        try:
            self.history.add(
                enabled=self.config.history_enabled,
                mode=self.mode.currentText(),
                request=self.request_text.toPlainText(),
                output=output,
            )
        except (OSError, sqlite3.Error):
            QMessageBox.warning(
                self,
                "履歴を保存できませんでした",
                PORTABLE_WRITE_ERROR,
            )

    def _generation_error(self, message: str) -> None:
        self.status_label.setText("エラー")
        QMessageBox.warning(self, "生成できませんでした", message)

    def _generation_finished(self) -> None:
        self._generation_active = False
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
                else f"見つかりません（{info.filename}）"
            )
        else:
            model = "未設定"
        skill = "Installed" if self.skill_manager.status().valid else "Not installed"
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
                f" / Device: {selected.display_name} / {selected.uma_label}"
                if selected is not None
                else " / Device: Not detected"
            )
        self.readiness.setText(
            f"LLMモデル: {model} / H3 Skill: {skill} / "
            f"Backend: {backend.display_name}{device_text} / Runtime: {runtime} / "
            f"Context: {self.config.context_size}{mock}"
        )
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
        if SettingsDialog(self.config_manager, self.project_root, self).exec():
            self.config = self.config_manager.load()
            self.skill_manager = SkillManager(
                self.config.skill_location
                or self.config_manager.data_dir / "skills" / "h3-prompt-writing"
            )
            self._refresh_readiness()
            self._refresh_memory_display()

    def _save_output(self) -> None:
        if not self.output_text.toPlainText():
            QMessageBox.information(self, "Save as TXT", "保存するH3 Promptがありません。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "H3 Promptを保存", "h3-prompt.txt", "Text File (*.txt)")
        if path:
            try:
                Path(path).write_text(self.output_text.toPlainText(), encoding="utf-8")
                self.status_label.setText(f"保存しました: {path}")
            except OSError as exc:
                QMessageBox.warning(self, "保存できませんでした", str(exc))

    def _clear_text(self) -> None:
        self.request_text.clear()
        self.output_text.clear()
        self.status_label.setText("準備完了")

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "MMH3 Prompt Builderについて",
            f"{PRODUCT_NAME} {APP_DISPLAY_VERSION}\n"
            f"Release date: {APP_RELEASE_DATE}\n"
            "Local Prompt Builder for MiniMax H3\n\n"
            "Windows x64 Portable ZIP / CPU + Vulkan GPU\n"
            "llama.cpp b9637\n\n"
            "MiniMax公式製品ではない、非公式コミュニティツールです。\n"
            "通常のPrompt生成はローカルで完結し、テレメトリはありません。\n"
            "設定・ログ・履歴・取得Skillはdataフォルダへ保存します。",
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
