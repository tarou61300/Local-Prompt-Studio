from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from core.config_manager import ConfigManager, PORTABLE_WRITE_ERROR
from core.inference_backends import BACKEND_CPU, BACKEND_VULKAN, GPU_LAYERS_AUTO
from core.llama_manager import LlamaServerManager
from core.model_manager import validate_model
from core.skill_manager import SkillError, SkillManager


RECOMMENDED_MODEL_URL = "https://huggingface.co/Qwen/Qwen3-8B-GGUF"


class SetupDialog(QWizard):
    def __init__(
        self,
        config_manager: ConfigManager,
        project_root: Path,
        parent=None,
        *,
        enforce_portable_skill_storage: bool = False,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.project_root = project_root
        self.enforce_portable_skill_storage = enforce_portable_skill_storage
        self.config = config_manager.load()
        self.runtime_manager = LlamaServerManager(project_root / "runtime")
        self.vulkan_devices = self.runtime_manager.detect_vulkan_devices()
        self.setWindowTitle("MMH3 Prompt Builder 初回セットアップ")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(680, 470)
        self.setOption(QWizard.NoBackButtonOnStartPage)

        self._create_model_page()
        self._create_skill_page()
        self._create_inference_page()
        self._create_confirmation_page()
        self.currentIdChanged.connect(self._refresh_current_page)

    def _new_page(self, title: str, subtitle: str) -> tuple[QWizardPage, QVBoxLayout]:
        page = QWizardPage()
        page.setTitle(title)
        page.setSubTitle(subtitle)
        layout = QVBoxLayout(page)
        return page, layout

    def _create_model_page(self) -> None:
        page, layout = self._new_page(
            "Step 1 — GGUFモデル",
            "Recommended: Qwen3-8B Q4_K_M（モデルはアプリへコピーしません）",
        )
        self.model_path = QLineEdit(self.config.model_path)
        self.model_path.setReadOnly(True)
        layout.addWidget(self.model_path)
        choose = QPushButton("既存のGGUFを選択")
        choose.clicked.connect(self._choose_model)
        layout.addWidget(choose)
        open_page = QPushButton("推奨モデルの公式ページを開く")
        open_page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RECOMMENDED_MODEL_URL)))
        layout.addWidget(open_page)
        self.model_status = QLabel("LLMモデルが設定されていません。")
        self.model_status.setWordWrap(True)
        layout.addWidget(self.model_status)
        layout.addStretch()
        self.addPage(page)

    def _create_skill_page(self) -> None:
        page, layout = self._new_page(
            "Step 2 — MiniMax H3 Prompt Skill",
            "このボタンを押した場合だけ、MiniMax公式GitHubから3ファイルを取得します。",
        )
        portable_skill_path = self.config_manager.data_dir / "skills" / "h3-prompt-writing"
        self.skill_path = (
            portable_skill_path
            if self.enforce_portable_skill_storage
            else Path(self.config.skill_location) if self.config.skill_location else portable_skill_path
        )
        self.skill_status = QLabel()
        self.skill_status.setWordWrap(True)
        layout.addWidget(self.skill_status)
        fetch = QPushButton("MiniMax公式H3 Prompt Skillを取得")
        fetch.clicked.connect(self._fetch_skill)
        layout.addWidget(fetch)
        layout.addStretch()
        self.addPage(page)

    def _create_inference_page(self) -> None:
        page, layout = self._new_page("Step 3 — Inference", "最初はCPUを推奨します。")
        self.cpu_radio = QRadioButton("CPU（デフォルト）")
        self.gpu_radio = QRadioButton("Vulkan GPU (AMD / Intel / NVIDIA)")
        group = QButtonGroup(self)
        group.addButton(self.cpu_radio)
        group.addButton(self.gpu_radio)
        layout.addWidget(self.cpu_radio)
        layout.addWidget(self.gpu_radio)
        vulkan_available = self.runtime_manager.runtime_available(BACKEND_VULKAN)
        self.gpu_radio.setEnabled(vulkan_available and bool(self.vulkan_devices))
        if not vulkan_available:
            layout.addWidget(QLabel("Vulkan用 llama.cpp runtime がありません。CPUのまま完了できます。"))
        elif not self.vulkan_devices:
            layout.addWidget(QLabel("llama.cppでVulkan GPUを検出できませんでした。CPUを使用してください。"))
        else:
            detected = self.vulkan_devices[0]
            layout.addWidget(
                QLabel(
                    f"検出: {detected.display_name}\n{detected.uma_label}\n"
                    "GPUメモリはSystem RAMと合算しません。"
                )
            )
        if self.config.inference_backend == BACKEND_VULKAN and self.gpu_radio.isEnabled():
            self.gpu_radio.setChecked(True)
        else:
            self.cpu_radio.setChecked(True)
        layout.addStretch()
        self.addPage(page)

    def _create_confirmation_page(self) -> None:
        page, layout = self._new_page("Step 4 — 確認", "内容を確認して「完了」を押してください。")
        self.confirmation = QLabel()
        self.confirmation.setAlignment(Qt.AlignTop)
        self.confirmation.setWordWrap(True)
        layout.addWidget(self.confirmation)
        self.addPage(page)

    def _choose_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "GGUFモデルを選択", "", "GGUF Model (*.gguf)")
        if path:
            resolved = str(Path(path).resolve())
            self.model_path.setText(resolved)
            self.model_status.setText(f"選択済み: {resolved}")

    def _fetch_skill(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            status = SkillManager(self.skill_path).install_or_update()
            self.skill_status.setText(
                f"Installed\n最終取得日時: {status.fetched_at}\nSHA256: {status.sha256.get('SKILL.md', '')}"
            )
        except SkillError as exc:
            QMessageBox.warning(self, "Skill取得失敗", str(exc))
        except OSError:
            QMessageBox.warning(self, "Skill取得失敗", PORTABLE_WRITE_ERROR)
        finally:
            QApplication.restoreOverrideCursor()

    def _refresh_current_page(self, page_id: int) -> None:
        status = SkillManager(self.skill_path).status()
        self.skill_status.setText(
            f"Installed\n最終取得日時: {status.fetched_at or '不明'}" if status.valid else "Not installed"
        )
        if page_id == 3:
            self.confirmation.setText(
                "モデル:\n"
                + (self.model_path.text() or "未設定")
                + "\n\nSkill:\n"
                + ("Installed" if status.valid else "Not installed")
                + "\n\n推論方式:\n"
                + (
                    "Vulkan GPU (AMD / Intel / NVIDIA)"
                    if self.gpu_radio.isChecked()
                    else "CPU"
                )
            )

    def accept(self) -> None:
        try:
            validate_model(self.model_path.text())
        except ValueError as exc:
            QMessageBox.warning(self, "セットアップ未完了", str(exc))
            return
        status = SkillManager(self.skill_path).status()
        if not status.valid:
            QMessageBox.warning(self, "セットアップ未完了", "MiniMax H3 Prompt Skillを取得してください。")
            return
        self.config.model_path = self.model_path.text()
        self.config.skill_location = str(self.skill_path.resolve())
        self.config.inference_backend = (
            BACKEND_VULKAN if self.gpu_radio.isChecked() else BACKEND_CPU
        )
        self.config.backend_device = (
            self.vulkan_devices[0].identifier if self.gpu_radio.isChecked() else ""
        )
        self.config.gpu_layers = GPU_LAYERS_AUTO
        self.config.setup_completed = True
        try:
            self.config_manager.save(self.config)
        except OSError:
            QMessageBox.warning(self, "設定を保存できませんでした", PORTABLE_WRITE_ERROR)
            return
        super().accept()
