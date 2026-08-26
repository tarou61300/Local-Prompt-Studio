from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.profile_models import LoadedProfile
from core.prompt_library_manager import (
    PromptLibraryError,
    PromptLibraryManager,
    PromptRecord,
)

from .prompt_library_tag_editor import PromptLibraryTagEditor


class PromptLibraryEntryDialog(QDialog):
    """Create a prompt or edit only its mutable Title/Tags metadata."""

    def __init__(
        self,
        tr: Callable[..., str],
        *,
        manager: PromptLibraryManager,
        profiles: Iterable[LoadedProfile],
        initial_model_id: str = "",
        initial_task_id: str = "",
        record: PromptRecord | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self.manager = manager
        self._profiles = tuple(profiles)
        self._profiles_by_id = {
            profile.manifest.id: profile for profile in self._profiles
        }
        self.record = record
        self.result_record: PromptRecord | None = None
        self.setModal(True)
        self.setWindowTitle(
            self.tr("library.edit_prompt")
            if record is not None
            else self.tr("library.new_prompt")
        )
        self.resize(700, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        form = QFormLayout()
        self.title_edit = QLineEdit(record.title if record is not None else "")
        self.title_edit.setObjectName("prompt_library_entry_title")
        form.addRow(self.tr("library.title"), self.title_edit)

        self.model_combo: QComboBox | None = None
        self.task_combo: QComboBox | None = None
        self.model_value: QLabel | None = None
        self.task_value: QLabel | None = None
        if record is None:
            self.model_combo = QComboBox()
            self.model_combo.setObjectName("prompt_library_entry_model")
            for profile in self._profiles:
                self.model_combo.addItem(
                    profile.manifest.name,
                    profile.manifest.id,
                )
            self.model_combo.currentIndexChanged.connect(self._populate_tasks)
            form.addRow(self.tr("library.model"), self.model_combo)

            self.task_combo = QComboBox()
            self.task_combo.setObjectName("prompt_library_entry_task")
            form.addRow(self.tr("library.task"), self.task_combo)
            model_index = self.model_combo.findData(initial_model_id)
            if model_index >= 0:
                self.model_combo.setCurrentIndex(model_index)
            self._populate_tasks()
            task_index = self.task_combo.findData(initial_task_id)
            if task_index >= 0:
                self.task_combo.setCurrentIndex(task_index)
        else:
            model_name = next(
                (
                    profile.manifest.name
                    for profile in self._profiles
                    if profile.manifest.id == record.model_id
                ),
                record.model_id,
            )
            self.model_value = QLabel(model_name)
            self.model_value.setObjectName("prompt_library_entry_model_readonly")
            self.task_value = QLabel(record.task_id)
            self.task_value.setObjectName("prompt_library_entry_task_readonly")
            form.addRow(self.tr("library.model"), self.model_value)
            form.addRow(self.tr("library.task"), self.task_value)
        root.addLayout(form)

        self.tag_editor = PromptLibraryTagEditor(self.tr)
        self.tag_editor.setObjectName("prompt_library_entry_tags")
        self.tag_editor.error_occurred.connect(self._show_error)
        self.tag_editor.set_manager(manager)
        if record is not None:
            self.tag_editor.set_selected_tags(record.tags)
        root.addWidget(self.tag_editor)

        prompt_label = QLabel(self.tr("library.prompt"))
        root.addWidget(prompt_label)
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setObjectName("prompt_library_entry_prompt")
        self.prompt_edit.setMinimumHeight(150)
        self.prompt_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.prompt_edit.setStyleSheet("border: 1px solid palette(mid);")
        if record is not None:
            self.prompt_edit.setPlainText(record.prompt_text)
            self.prompt_edit.setReadOnly(True)
        root.addWidget(self.prompt_edit, 1)

        self.error_label = QLabel()
        self.error_label.setObjectName("prompt_library_entry_error")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.setObjectName("prompt_library_entry_buttons")
        save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        save_button.setText(self.tr("library.save"))
        cancel_button.setText(self.tr("library.cancel"))
        self.button_box.accepted.connect(self.save_entry)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

    @property
    def is_edit(self) -> bool:
        return self.record is not None

    def current_model_id(self) -> str:
        if self.record is not None:
            return self.record.model_id
        assert self.model_combo is not None
        return str(self.model_combo.currentData() or "")

    def current_task_id(self) -> str:
        if self.record is not None:
            return self.record.task_id
        assert self.task_combo is not None
        return str(self.task_combo.currentData() or "")

    def _populate_tasks(self) -> None:
        if self.model_combo is None or self.task_combo is None:
            return
        profile = self._profiles_by_id.get(
            str(self.model_combo.currentData() or "")
        )
        previous = self.task_combo.currentData()
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        if profile is not None:
            for task in profile.manifest.supported_tasks:
                self.task_combo.addItem(task, task)
        previous_index = self.task_combo.findData(previous)
        if previous_index >= 0:
            self.task_combo.setCurrentIndex(previous_index)
        self.task_combo.blockSignals(False)

    def save_entry(self) -> None:
        title = self.title_edit.text()
        if not title.strip():
            self.error_label.setText(self.tr("library.title_required"))
            return
        if self.record is None and not self.prompt_edit.toPlainText().strip():
            self.error_label.setText(self.tr("library.prompt_required"))
            return
        try:
            if self.record is None:
                self.result_record = self.manager.create_prompt(
                    title=title,
                    model_id=self.current_model_id(),
                    task_id=self.current_task_id(),
                    prompt_text=self.prompt_edit.toPlainText(),
                    tag_ids=self.tag_editor.selected_tag_ids(),
                    tag_names=self.tag_editor.pending_tag_names(),
                )
            else:
                self.result_record = self.manager.update_metadata(
                    self.record.id,
                    title=title,
                    tag_ids=self.tag_editor.selected_tag_ids(),
                    tag_names=self.tag_editor.pending_tag_names(),
                )
        except PromptLibraryError as exc:
            self._show_error(exc.code)
            return
        self.accept()

    def _show_error(self, code: str) -> None:
        if code == "PROMPT_LIBRARY_TITLE_EMPTY":
            message = self.tr("library.title_required")
        elif code == "PROMPT_LIBRARY_PROMPT_EMPTY":
            message = self.tr("library.prompt_required")
        else:
            message = self.tr("library.save_error")
        self.error_label.setText(message)
