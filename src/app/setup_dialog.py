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

from core.config_manager import ConfigManager
from core.inference_backends import BACKEND_CPU, BACKEND_VULKAN, GPU_LAYERS_AUTO
from core.llama_manager import LlamaServerManager
from core.localization import Localization
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
        localization: Localization | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.project_root = project_root
        self.enforce_portable_skill_storage = enforce_portable_skill_storage
        self.config = config_manager.load()
        self.localization = localization or Localization(
            project_root / "locales", self.config.ui_locale
        )
        self.tr = self.localization.tr
        self.runtime_manager = LlamaServerManager(project_root / "runtime")
        self.vulkan_devices = self.runtime_manager.detect_vulkan_devices()
        self.setWindowTitle(
            f"{self.localization.tr('app.title')} — {self.localization.tr('settings.title')}"
        )
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(680, 470)
        self.setOption(QWizard.NoBackButtonOnStartPage)
        self.setButtonText(QWizard.BackButton, self.tr("setup.back"))
        self.setButtonText(QWizard.NextButton, self.tr("setup.next"))
        self.setButtonText(QWizard.FinishButton, self.tr("setup.finish"))
        self.setButtonText(QWizard.CancelButton, self.tr("common.cancel"))

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
            self.tr("setup.model.title"),
            self.tr("setup.model.subtitle"),
        )
        self.model_path = QLineEdit(self.config.model_path)
        self.model_path.setReadOnly(True)
        layout.addWidget(self.model_path)
        choose = QPushButton(self.tr("setup.model.choose"))
        choose.clicked.connect(self._choose_model)
        layout.addWidget(choose)
        open_page = QPushButton(self.tr("setup.model.open_recommended"))
        open_page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RECOMMENDED_MODEL_URL)))
        layout.addWidget(open_page)
        self.model_status = QLabel(self.tr("settings.model.not_set"))
        self.model_status.setWordWrap(True)
        layout.addWidget(self.model_status)
        layout.addStretch()
        self.addPage(page)

    def _create_skill_page(self) -> None:
        page, layout = self._new_page(
            self.tr("setup.skill.title"),
            self.tr("setup.h3_skill_optional"),
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
        fetch = QPushButton(self.tr("setup.skill.fetch"))
        fetch.clicked.connect(self._fetch_skill)
        layout.addWidget(fetch)
        layout.addStretch()
        self.addPage(page)

    def _create_inference_page(self) -> None:
        page, layout = self._new_page(
            self.tr("setup.inference.title"), self.tr("setup.inference.subtitle")
        )
        self.cpu_radio = QRadioButton(self.tr("setup.inference.cpu"))
        self.gpu_radio = QRadioButton("Vulkan GPU (AMD / Intel / NVIDIA)")
        group = QButtonGroup(self)
        group.addButton(self.cpu_radio)
        group.addButton(self.gpu_radio)
        layout.addWidget(self.cpu_radio)
        layout.addWidget(self.gpu_radio)
        vulkan_available = self.runtime_manager.runtime_available(BACKEND_VULKAN)
        self.gpu_radio.setEnabled(vulkan_available and bool(self.vulkan_devices))
        if not vulkan_available:
            layout.addWidget(QLabel(self.tr("setup.inference.vulkan_runtime_missing")))
        elif not self.vulkan_devices:
            layout.addWidget(QLabel(self.tr("setup.inference.vulkan_not_detected")))
        else:
            detected = self.vulkan_devices[0]
            layout.addWidget(
                QLabel(
                    self.tr(
                        "setup.inference.vulkan_detected",
                        device=detected.display_name,
                        uma=self.tr(
                            "backend.memory_classification."
                            f"{detected.memory_classification}"
                        ),
                    )
                )
            )
        if self.config.inference_backend == BACKEND_VULKAN and self.gpu_radio.isEnabled():
            self.gpu_radio.setChecked(True)
        else:
            self.cpu_radio.setChecked(True)
        layout.addStretch()
        self.addPage(page)

    def _create_confirmation_page(self) -> None:
        page, layout = self._new_page(
            self.tr("setup.confirm.title"), self.tr("setup.confirm.subtitle")
        )
        self.confirmation = QLabel()
        self.confirmation.setAlignment(Qt.AlignTop)
        self.confirmation.setWordWrap(True)
        layout.addWidget(self.confirmation)
        self.addPage(page)

    def _choose_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("settings.choose_model_title"), "", "GGUF Model (*.gguf)"
        )
        if path:
            resolved = str(Path(path).resolve())
            self.model_path.setText(resolved)
            self.model_status.setText(self.tr("setup.model.selected", path=resolved))

    def _fetch_skill(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            status = SkillManager(self.skill_path).install_or_update()
            self.skill_status.setText(
                self.tr(
                    "setup.skill.installed_details",
                    fetched_at=status.fetched_at,
                    sha256=status.sha256.get("SKILL.md", ""),
                )
            )
        except SkillError as exc:
            QMessageBox.warning(self, self.tr("setup.skill.fetch_failed"), str(exc))
        except OSError:
            QMessageBox.warning(
                self,
                self.tr("setup.skill.fetch_failed"),
                self.tr("error.portable_write"),
            )
        finally:
            QApplication.restoreOverrideCursor()

    def _refresh_current_page(self, page_id: int) -> None:
        status = SkillManager(self.skill_path).status()
        self.skill_status.setText(
            self.tr(
                "setup.skill.status_installed",
                fetched_at=status.fetched_at or self.tr("common.unknown"),
            )
            if status.valid
            else self.tr("readiness.not_installed")
        )
        if page_id == 3:
            self.confirmation.setText(
                self.tr(
                    "setup.confirm.summary",
                    model=self.model_path.text() or self.tr("readiness.not_set"),
                    skill=(
                        self.tr("readiness.installed")
                        if status.valid
                        else self.tr("readiness.not_installed")
                    ),
                    inference=(
                        "Vulkan GPU (AMD / Intel / NVIDIA)"
                        if self.gpu_radio.isChecked()
                        else "CPU"
                    ),
                )
            )

    def accept(self) -> None:
        try:
            validate_model(self.model_path.text())
        except ValueError as exc:
            QMessageBox.warning(self, self.tr("setup.incomplete"), str(exc))
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
            QMessageBox.warning(
                self,
                self.tr("error.settings_save_title"),
                self.tr("error.portable_write"),
            )
            return
        super().accept()
