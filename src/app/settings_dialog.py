from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.config_manager import AppConfig, ConfigManager, CONTEXT_PRESETS, PORTABLE_WRITE_ERROR
from core.inference_backends import (
    BACKEND_CPU,
    BACKEND_VULKAN,
    BACKENDS,
    GPU_LAYERS_AUTO,
)
from core.llama_manager import LlamaServerManager
from core.model_manager import inspect_model
from core.skill_manager import SkillError, SkillManager
from core.system_memory import (
    assess_memory,
    format_assessment_details,
    format_memory_status,
    get_system_memory,
)


class SettingsDialog(QDialog):
    def __init__(
        self,
        config_manager: ConfigManager,
        project_root: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setMinimumWidth(650)
        self.config_manager = config_manager
        self.project_root = project_root
        self.config = config_manager.load()
        self.runtime_manager = LlamaServerManager(project_root / "runtime")
        self.vulkan_devices = self.runtime_manager.detect_vulkan_devices()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.model_path = QLineEdit(self.config.model_path)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_path, 1)
        browse_model = QPushButton("GGUFを選択…")
        browse_model.clicked.connect(self._choose_model)
        model_row.addWidget(browse_model)
        form.addRow("LLM Model Path", model_row)

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
        form.addRow("Inference Backend", self.backend)

        self.backend_device = QComboBox()
        for device in self.vulkan_devices:
            self.backend_device.addItem(device.display_name, device.identifier)
        configured_device = self.backend_device.findData(self.config.backend_device)
        if configured_device >= 0:
            self.backend_device.setCurrentIndex(configured_device)
        form.addRow("Vulkan Device", self.backend_device)

        self.backend_info = QLabel()
        self.backend_info.setWordWrap(True)
        form.addRow("", self.backend_info)

        self.cpu_threads = QSpinBox()
        self.cpu_threads.setRange(0, 256)
        self.cpu_threads.setSpecialValueText("Auto")
        self.cpu_threads.setValue(self.config.cpu_threads)
        form.addRow("CPU Threads", self.cpu_threads)

        self.gpu_layers = QSpinBox()
        self.gpu_layers.setRange(-1, 999)
        self.gpu_layers.setSpecialValueText("Auto")
        self.gpu_layers.setValue(self.config.gpu_layers)
        form.addRow("GPU Offload（Advanced）", self.gpu_layers)
        self.backend.currentIndexChanged.connect(self._update_backend_controls)
        self.backend.currentIndexChanged.connect(lambda _index: self._update_memory_warning())
        self.backend_device.currentIndexChanged.connect(self._update_backend_controls)
        self.backend_device.currentIndexChanged.connect(
            lambda _index: self._update_memory_warning()
        )

        self.context_size = QComboBox()
        for value, description in CONTEXT_PRESETS:
            self.context_size.addItem(f"{value} — {description}", value)
        context_index = self.context_size.findData(self.config.context_size)
        if context_index < 0:
            self.context_size.addItem(f"{self.config.context_size} — Custom", self.config.context_size)
            context_index = self.context_size.count() - 1
        self.context_size.setCurrentIndex(context_index)
        self.context_size.currentIndexChanged.connect(self._update_memory_warning)
        form.addRow("Context Size（Advanced）", self.context_size)

        self.memory_info = QLabel()
        self.memory_info.setWordWrap(True)
        form.addRow("メモリ目安", self.memory_info)

        default_skill = config_manager.data_dir / "skills" / "h3-prompt-writing"
        self.skill_location = QLineEdit(self.config.skill_location or str(default_skill))
        skill_row = QHBoxLayout()
        skill_row.addWidget(self.skill_location, 1)
        browse_skill = QPushButton("フォルダを選択…")
        browse_skill.clicked.connect(self._choose_skill_folder)
        skill_row.addWidget(browse_skill)
        form.addRow("Skill Location", skill_row)

        skill_actions = QHBoxLayout()
        check_update = QPushButton("Skill更新確認")
        check_update.clicked.connect(self._check_update)
        skill_actions.addWidget(check_update)
        open_skill = QPushButton("Skillフォルダを開く")
        open_skill.clicked.connect(self._open_skill_folder)
        skill_actions.addWidget(open_skill)
        skill_actions.addStretch()
        form.addRow("", skill_actions)

        self.history_enabled = QCheckBox("ローカル履歴を保存する（デフォルトOFF）")
        self.history_enabled.setChecked(self.config.history_enabled)
        form.addRow("History", self.history_enabled)

        self.theme = QComboBox()
        self.theme.addItems(["System", "Light", "Dark"])
        self.theme.setCurrentText(self.config.theme)
        form.addRow("Theme", self.theme)

        reset_button = QPushButton("設定を初期値へ戻す")
        reset_button.clicked.connect(self._reset_fields)
        layout.addWidget(reset_button)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("キャンセル")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_backend_controls()
        self._update_model_info()

    def _choose_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "GGUFモデルを選択", "", "GGUF Model (*.gguf)")
        if path:
            self.model_path.setText(str(Path(path).resolve()))

    def _choose_skill_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "h3-prompt-writingフォルダを選択")
        if path:
            self.skill_location.setText(str(Path(path).resolve()))

    def _update_model_info(self) -> None:
        path = self.model_path.text().strip()
        if not path:
            self.model_info.setText("LLMモデルが設定されていません。")
            self._update_memory_warning()
            return
        info = inspect_model(path)
        if not info.exists:
            self.model_info.setText("ファイルが見つかりません。")
            self._update_memory_warning()
            return
        recommended = "推奨モデルです。" if info.is_recommended else "動作可能ですが、推奨モデルではありません。"
        self.model_info.setText(
            f"選択モデル: {info.display_name}\n"
            f"GGUFファイル: {info.filename}\n"
            f"ファイルサイズ: {info.size_bytes:,} bytes（{info.size_gib:.2f} GiB） / {recommended}"
        )
        self._update_memory_warning()

    def _update_memory_warning(self) -> None:
        memory = get_system_memory()
        context_size = int(self.context_size.currentData())
        model = inspect_model(self.model_path.text().strip())
        if not model.exists:
            self.memory_info.setText(
                format_memory_status(memory)
                + f"\nContext: {context_size}\nGGUFモデルを選択すると推定必要RAMを表示します。"
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
        )
        text = format_assessment_details(assessment)
        if assessment.warnings:
            text += "\n⚠ " + "\n⚠ ".join(assessment.warnings)
            self.memory_info.setText(text)
            self.memory_info.setStyleSheet("color: #d68a00;")
        else:
            self.memory_info.setText(text + "\n8192（Recommended）が標準です。")
            self.memory_info.setStyleSheet("")

    def _update_backend_controls(self) -> None:
        is_vulkan = self.backend.currentData() == BACKEND_VULKAN
        self.backend_device.setEnabled(is_vulkan and bool(self.vulkan_devices))
        self.gpu_layers.setEnabled(is_vulkan)
        if not is_vulkan:
            suffix = ""
            if not self.runtime_manager.runtime_available(BACKEND_VULKAN):
                suffix = " Vulkan runtimeは未導入です。"
            elif not self.vulkan_devices:
                suffix = " 利用可能なVulkan GPUは検出されていません。"
            self.backend_info.setText("CPUバックエンドを使用します。" + suffix)
        elif self.vulkan_devices:
            selected_id = self.backend_device.currentData()
            selected = next(
                (device for device in self.vulkan_devices if device.identifier == selected_id),
                self.vulkan_devices[0],
            )
            memory_text = "GPUメモリ情報なし"
            if selected.reported_memory_bytes is not None:
                memory_text = f"llama.cpp報告GPUメモリ: 約{selected.reported_memory_bytes / (1024**3):.1f} GiB"
            self.backend_info.setText(
                f"検出: {selected.display_name}\n{selected.uma_label}\n{memory_text}（情報表示のみ）\n"
                "GPUメモリはSystem RAMへ加算せず、生成前のRAM安全確認を継続します。"
            )
        elif not self.runtime_manager.runtime_available(BACKEND_VULKAN):
            self.backend_info.setText(
                "Vulkan用 llama.cpp runtime がありません。CPUは引き続き使用できます。"
            )
        elif not self.vulkan_devices:
            self.backend_info.setText(
                "llama.cppで利用可能なVulkan GPUを検出できませんでした。CPUを使用してください。"
            )
        else:
            self.backend_info.setText("Vulkan GPUを使用できません。CPUを選択してください。")

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
            text = "公式Skillの更新があります。" if update else "ローカルSkillは最新です。"
            QMessageBox.information(self, "Skill更新確認", text)
        except SkillError as exc:
            QMessageBox.warning(self, "Skill更新確認", str(exc))

    def _open_skill_folder(self) -> None:
        path = Path(self.skill_location.text().strip())
        if not path.exists():
            QMessageBox.warning(self, "Skillフォルダ", "Skillフォルダが見つかりません。")
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
        self.theme.setCurrentText(default.theme)

    def accept(self) -> None:
        config = self.config_manager.load()
        config.model_path = self.model_path.text().strip()
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
        config.theme = self.theme.currentText()
        try:
            self.config_manager.save(config)
        except OSError:
            QMessageBox.warning(self, "設定を保存できませんでした", PORTABLE_WRITE_ERROR)
            return
        super().accept()
