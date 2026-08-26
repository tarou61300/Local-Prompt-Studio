from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.profile_models import LoadedProfile
from core.prompt_library_manager import (
    PromptLibraryError,
    PromptLibraryManager,
    PromptRecord,
    PromptSearchPage,
    PromptSummary,
)

from .tag_selector import TagSelector
from .prompt_library_dialog import PromptLibraryEntryDialog


@dataclass(frozen=True, slots=True)
class _SearchCriteria:
    model_id: str
    task_id: str
    tag_ids: tuple[int, ...]
    title: str


class PromptLibraryTableModel(QAbstractTableModel):
    checked_changed = Signal()

    CHECK_COLUMN = 0
    TITLE_COLUMN = 1
    MODEL_COLUMN = 2
    TASK_COLUMN = 3
    TAGS_COLUMN = 4

    def __init__(
        self,
        tr: Callable[..., str],
        model_names: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self._model_names = dict(model_names)
        self._items: tuple[PromptSummary, ...] = ()
        self._checked_ids: set[int] = set()

    @property
    def items(self) -> tuple[PromptSummary, ...]:
        return self._items

    def set_items(self, items: Iterable[PromptSummary]) -> None:
        self.beginResetModel()
        self._items = tuple(items)
        self._checked_ids.clear()
        self.endResetModel()
        self.checked_changed.emit()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else 5

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        headers = (
            self.tr("library.select"),
            self.tr("library.title"),
            self.tr("library.model"),
            self.tr("library.task"),
            self.tr("library.tags"),
        )
        return headers[section] if 0 <= section < len(headers) else None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if index.column() == self.CHECK_COLUMN:
            if role == Qt.ItemDataRole.CheckStateRole:
                return (
                    Qt.CheckState.Checked
                    if item.id in self._checked_ids
                    else Qt.CheckState.Unchecked
                )
            if role == Qt.ItemDataRole.DisplayRole:
                return ""
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == self.TITLE_COLUMN:
                return item.title
            if index.column() == self.MODEL_COLUMN:
                return self._model_names.get(item.model_id, item.model_id)
            if index.column() == self.TASK_COLUMN:
                return item.task_id
            if index.column() == self.TAGS_COLUMN:
                return ", ".join(tag.name for tag in item.tags)
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == self.TITLE_COLUMN:
                return item.title
            if index.column() == self.TAGS_COLUMN:
                return ", ".join(tag.name for tag in item.tags)
        return None

    def flags(self, index: QModelIndex):
        flags = super().flags(index)
        if index.isValid() and index.column() == self.CHECK_COLUMN:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if (
            not index.isValid()
            or index.column() != self.CHECK_COLUMN
            or role != Qt.ItemDataRole.CheckStateRole
        ):
            return False
        prompt_id = self._items[index.row()].id
        if value == Qt.CheckState.Checked.value or value == Qt.CheckState.Checked:
            self._checked_ids.add(prompt_id)
        else:
            self._checked_ids.discard(prompt_id)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.checked_changed.emit()
        return True

    def item_at(self, row: int) -> PromptSummary | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def checked_prompt_ids(self) -> tuple[int, ...]:
        return tuple(
            item.id for item in self._items if item.id in self._checked_ids
        )


class PromptLibraryPage(QWidget):
    """Search-and-copy UI for completed prompts stored in the library DB."""

    def __init__(
        self,
        tr: Callable[..., str],
        *,
        data_dir: Path,
        profiles: Iterable[LoadedProfile],
        manager_factory: Callable[[Path], PromptLibraryManager] = PromptLibraryManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self.data_dir = Path(data_dir)
        self._manager_factory = manager_factory
        self.manager: PromptLibraryManager | None = None
        self._profiles = tuple(profiles)
        self._profiles_by_id = {
            profile.manifest.id: profile for profile in self._profiles
        }
        self._model_names = {
            profile.manifest.id: profile.manifest.name for profile in self._profiles
        }
        self._criteria: _SearchCriteria | None = None
        self._current_page = 1
        self._page_count = 1
        self._database_created_on_activate = False
        self._detail_record: PromptRecord | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        library_actions = QHBoxLayout()
        self.new_button = QPushButton(self.tr("library.new_prompt"))
        self.new_button.setObjectName("prompt_library_new")
        self.new_button.clicked.connect(self.open_new_prompt)
        library_actions.addWidget(self.new_button)
        library_actions.addStretch()
        root.addLayout(library_actions)

        filters = QGroupBox(self.tr("library.filters"))
        filters.setObjectName("prompt_library_filters")
        filters_layout = QVBoxLayout(filters)
        target_form = QFormLayout()
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("prompt_library_model")
        self.model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.task_combo = QComboBox()
        self.task_combo.setObjectName("prompt_library_task")
        self.task_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        target_form.addRow(self.tr("library.model"), self.model_combo)
        target_form.addRow(self.tr("library.task"), self.task_combo)
        filters_layout.addLayout(target_form)

        self.tag_selector = TagSelector(self.tr, allow_favorite_edit=True)
        self.tag_selector.setObjectName("prompt_library_tag_selector")
        self.tag_selector.error_occurred.connect(self._show_database_error)
        filters_layout.addWidget(self.tag_selector)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(self.tr("library.title")))
        self.title_search = QLineEdit()
        self.title_search.setObjectName("prompt_library_title_search")
        self.title_search.setPlaceholderText(self.tr("library.title.placeholder"))
        self.title_search.returnPressed.connect(self.search)
        search_row.addWidget(self.title_search, 1)
        self.search_button = QPushButton(self.tr("library.search"))
        self.search_button.setObjectName("prompt_library_search")
        self.search_button.clicked.connect(self.search)
        search_row.addWidget(self.search_button)
        self.clear_button = QPushButton(self.tr("library.clear_conditions"))
        self.clear_button.setObjectName("prompt_library_clear")
        self.clear_button.clicked.connect(self.clear_conditions)
        search_row.addWidget(self.clear_button)
        filters_layout.addLayout(search_row)
        root.addWidget(filters)

        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setObjectName("prompt_library_workspace_splitter")
        self.workspace_splitter.setChildrenCollapsible(False)

        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(6)
        self.results_label = QLabel(self.tr("library.search_results", total=0))
        self.results_label.setObjectName("prompt_library_results_label")
        results_layout.addWidget(self.results_label)
        self.state_label = QLabel(self.tr("library.ready"))
        self.state_label.setObjectName("prompt_library_state")
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("color: palette(placeholder-text);")
        results_layout.addWidget(self.state_label)

        self.results_model = PromptLibraryTableModel(
            self.tr,
            self._model_names,
            self,
        )
        self.results_table = QTableView()
        self.results_table.setObjectName("prompt_library_results")
        self.results_table.setModel(self.results_model)
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.results_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setWordWrap(False)
        self.results_table.verticalHeader().setVisible(False)
        header = self.results_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        results_layout.addWidget(self.results_table, 1)

        actions = QHBoxLayout()
        self.show_button = QPushButton(self.tr("library.show"))
        self.show_button.setObjectName("prompt_library_show")
        self.show_button.clicked.connect(self.show_selected_prompt)
        actions.addWidget(self.show_button)
        self.copy_button = QPushButton(self.tr("library.copy"))
        self.copy_button.setObjectName("prompt_library_copy")
        self.copy_button.clicked.connect(self.copy_selected_prompt)
        actions.addWidget(self.copy_button)
        self.copy_checked_button = QPushButton(
            self.tr("library.copy_selected_prompts")
        )
        self.copy_checked_button.setObjectName("prompt_library_copy_checked")
        self.copy_checked_button.clicked.connect(self.copy_checked_prompts)
        actions.addWidget(self.copy_checked_button)
        self.edit_button = QPushButton(self.tr("library.edit"))
        self.edit_button.setObjectName("prompt_library_edit")
        self.edit_button.clicked.connect(self.edit_selected_prompt)
        actions.addWidget(self.edit_button)
        self.delete_button = QPushButton(self.tr("library.delete"))
        self.delete_button.setObjectName("prompt_library_delete")
        self.delete_button.clicked.connect(self.delete_selected_prompt)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        results_layout.addLayout(actions)

        pagination = QHBoxLayout()
        self.previous_button = QPushButton(self.tr("library.previous"))
        self.previous_button.setObjectName("prompt_library_previous")
        self.previous_button.clicked.connect(self.previous_page)
        pagination.addWidget(self.previous_button)
        pagination.addStretch()
        self.page_label = QLabel(self.tr("library.page", page=1, pages=1))
        self.page_label.setObjectName("prompt_library_page_indicator")
        pagination.addWidget(self.page_label)
        pagination.addStretch()
        self.next_button = QPushButton(self.tr("library.next"))
        self.next_button.setObjectName("prompt_library_next")
        self.next_button.clicked.connect(self.next_page)
        pagination.addWidget(self.next_button)
        results_layout.addLayout(pagination)
        self.workspace_splitter.addWidget(results_widget)

        detail_group = QGroupBox(self.tr("library.detail"))
        detail_group.setObjectName("prompt_library_detail")
        detail_layout = QVBoxLayout(detail_group)
        self.detail_title = QLabel()
        self.detail_title.setObjectName("prompt_library_detail_title")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-weight: 600;")
        detail_layout.addWidget(self.detail_title)
        self.detail_metadata = QLabel()
        self.detail_metadata.setObjectName("prompt_library_detail_metadata")
        self.detail_metadata.setWordWrap(True)
        detail_layout.addWidget(self.detail_metadata)
        self.detail_prompt = QPlainTextEdit()
        self.detail_prompt.setObjectName("prompt_library_detail_prompt")
        self.detail_prompt.setReadOnly(True)
        self.detail_prompt.setMinimumHeight(110)
        self.detail_prompt.setStyleSheet("border: 1px solid palette(mid);")
        detail_layout.addWidget(self.detail_prompt, 1)
        self.workspace_splitter.addWidget(detail_group)
        self.workspace_splitter.setStretchFactor(0, 3)
        self.workspace_splitter.setStretchFactor(1, 2)
        self.workspace_splitter.setSizes([360, 220])
        root.addWidget(self.workspace_splitter, 1)

        self.feedback_label = QLabel()
        self.feedback_label.setObjectName("prompt_library_feedback")
        self.feedback_label.setWordWrap(True)
        root.addWidget(self.feedback_label)

        self._populate_models()
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        self.task_combo.currentIndexChanged.connect(self._task_changed)
        self.results_table.selectionModel().selectionChanged.connect(
            self._update_action_state
        )
        self.results_model.checked_changed.connect(self._update_action_state)
        self._clear_results(self.tr("library.ready"))
        self._update_action_state()

    def activate(self) -> None:
        if self.manager is not None:
            return
        database_path = self.data_dir / "prompt_library.sqlite3"
        self._database_created_on_activate = not database_path.exists()
        try:
            self.manager = self._manager_factory(self.data_dir)
            self.tag_selector.set_manager(self.manager)
            self._set_tag_target()
        except PromptLibraryError as exc:
            self.manager = None
            self._show_database_error(exc.code)
            return
        self._clear_results(
            self.tr("library.no_prompts")
            if self._database_created_on_activate
            else self.tr("library.ready")
        )

    def create_new_prompt_dialog(self) -> PromptLibraryEntryDialog | None:
        if not self._ensure_manager():
            return None
        assert self.manager is not None
        return PromptLibraryEntryDialog(
            self.tr,
            manager=self.manager,
            profiles=self._profiles,
            initial_model_id=str(self.model_combo.currentData() or ""),
            initial_task_id=str(self.task_combo.currentData() or ""),
            parent=self,
        )

    def open_new_prompt(self) -> None:
        dialog = self.create_new_prompt_dialog()
        if dialog is None or not dialog.exec():
            return
        self._refresh_after_mutation(self.tr("library.saved_successfully"))

    def create_edit_prompt_dialog(
        self,
        record: PromptRecord,
    ) -> PromptLibraryEntryDialog:
        assert self.manager is not None
        return PromptLibraryEntryDialog(
            self.tr,
            manager=self.manager,
            profiles=self._profiles,
            record=record,
            parent=self,
        )

    def edit_selected_prompt(self) -> None:
        summary = self._current_summary()
        if summary is None:
            return
        record = self._load_prompt(summary.id)
        if record is None:
            return
        dialog = self.create_edit_prompt_dialog(record)
        if not dialog.exec():
            return
        self._refresh_after_mutation(self.tr("library.updated_successfully"))

    def delete_selected_prompt(self) -> None:
        summary = self._current_summary()
        if summary is None or not self._ensure_manager():
            return
        answer = QMessageBox.question(
            self,
            self.tr("library.confirm_delete_title"),
            self.tr("library.confirm_delete", title=summary.title),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        assert self.manager is not None
        try:
            self.manager.delete_prompt(summary.id)
        except PromptLibraryError as exc:
            self._show_database_error(exc.code)
            return
        self._refresh_after_mutation(self.tr("library.deleted_successfully"))

    def _refresh_after_mutation(self, feedback: str) -> None:
        self.tag_selector.refresh_candidates()
        if self._criteria is None:
            self._clear_results(self.tr("library.ready"))
        else:
            self._load_page(self._current_page)
        self.feedback_label.setText(feedback)

    def search(self) -> None:
        if not self._ensure_manager():
            return
        model_id = str(self.model_combo.currentData() or "")
        task_id = str(self.task_combo.currentData() or "")
        if not model_id or not task_id:
            self._clear_results(self.tr("library.target_required"))
            return
        self._criteria = _SearchCriteria(
            model_id=model_id,
            task_id=task_id,
            tag_ids=self.tag_selector.selected_tag_ids(),
            title=self.title_search.text(),
        )
        self._load_page(1)

    def clear_conditions(self) -> None:
        self.tag_selector.clear_selection()
        self.tag_selector.clear_search()
        self.title_search.clear()
        self._criteria = None
        self._clear_results(self.tr("library.ready"))

    def show_selected_prompt(self) -> None:
        summary = self._current_summary()
        if summary is None:
            return
        record = self._load_prompt(summary.id)
        if record is None:
            return
        self._detail_record = record
        self.detail_title.setText(record.title)
        self.detail_metadata.setText(
            self.tr(
                "library.detail_metadata",
                model=self._model_names.get(record.model_id, record.model_id),
                task=record.task_id,
                tags=", ".join(tag.name for tag in record.tags),
            )
        )
        self.detail_prompt.setPlainText(record.prompt_text)

    def copy_selected_prompt(self) -> None:
        summary = self._current_summary()
        if summary is None:
            return
        record = self._record_for_copy(summary.id)
        if record is None:
            return
        QApplication.clipboard().setText(record.prompt_text)
        self.feedback_label.setText(self.tr("library.copied"))

    def copy_checked_prompts(self) -> None:
        if not self._ensure_manager():
            return
        prompt_ids = self.results_model.checked_prompt_ids()
        if not prompt_ids:
            return
        bodies: list[str] = []
        try:
            for prompt_id in prompt_ids:
                bodies.append(self.manager.get_prompt(prompt_id).prompt_text)
        except PromptLibraryError as exc:
            self._show_database_error(exc.code)
            return
        QApplication.clipboard().setText("\n\n---\n\n".join(bodies))
        self.feedback_label.setText(
            self.tr("library.copied_multiple", count=len(bodies))
        )

    def next_page(self) -> None:
        if self._criteria is not None and self._current_page < self._page_count:
            self._load_page(self._current_page + 1)

    def previous_page(self) -> None:
        if self._criteria is not None and self._current_page > 1:
            self._load_page(self._current_page - 1)

    def _populate_models(self) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for profile in self._profiles:
            self.model_combo.addItem(profile.manifest.name, profile.manifest.id)
        self.model_combo.blockSignals(False)
        self._populate_tasks()

    def _populate_tasks(self) -> None:
        profile = self._profiles_by_id.get(str(self.model_combo.currentData() or ""))
        self.task_combo.blockSignals(True)
        self.task_combo.clear()
        if profile is not None:
            for task in profile.manifest.supported_tasks:
                self.task_combo.addItem(task, task)
        self.task_combo.blockSignals(False)

    def _model_changed(self) -> None:
        self._populate_tasks()
        self._target_changed()

    def _task_changed(self) -> None:
        self._target_changed()

    def _target_changed(self) -> None:
        self._criteria = None
        self._set_tag_target()
        self._clear_results(self.tr("library.ready"))

    def _set_tag_target(self) -> None:
        self.tag_selector.set_target(
            str(self.model_combo.currentData() or ""),
            str(self.task_combo.currentData() or ""),
        )

    def _ensure_manager(self) -> bool:
        if self.manager is None:
            self.activate()
        return self.manager is not None

    def _load_page(self, page: int) -> None:
        if self.manager is None or self._criteria is None:
            return
        try:
            result = self.manager.search_prompts(
                model_id=self._criteria.model_id,
                task_id=self._criteria.task_id,
                tag_ids=self._criteria.tag_ids,
                title=self._criteria.title,
                page=page,
            )
            last_page = max(
                1,
                math.ceil(result.total_count / result.page_size),
            )
            if page > last_page:
                result = self.manager.search_prompts(
                    model_id=self._criteria.model_id,
                    task_id=self._criteria.task_id,
                    tag_ids=self._criteria.tag_ids,
                    title=self._criteria.title,
                    page=last_page,
                )
        except PromptLibraryError as exc:
            self._show_database_error(exc.code)
            return
        self._apply_search_page(result)

    def _apply_search_page(self, result: PromptSearchPage) -> None:
        self._current_page = result.page
        self._page_count = max(1, math.ceil(result.total_count / result.page_size))
        self.results_model.set_items(result.items)
        self.results_table.clearSelection()
        self._clear_detail()
        self.feedback_label.clear()
        if result.total_count:
            start = (result.page - 1) * result.page_size + 1
            end = start + len(result.items) - 1
            self.results_label.setText(
                self.tr(
                    "library.search_results_range",
                    total=result.total_count,
                    start=start,
                    end=end,
                )
            )
            self.state_label.clear()
        else:
            self.results_label.setText(self.tr("library.search_results", total=0))
            self.state_label.setText(
                self.tr("library.no_prompts")
                if self._database_created_on_activate
                else self.tr("library.no_matches")
            )
        self.page_label.setText(
            self.tr(
                "library.page",
                page=self._current_page,
                pages=self._page_count,
            )
        )
        self.previous_button.setEnabled(self._current_page > 1)
        self.next_button.setEnabled(self._current_page < self._page_count)
        self._update_action_state()

    def _clear_results(self, state: str) -> None:
        self._current_page = 1
        self._page_count = 1
        self.results_model.set_items(())
        self.results_table.clearSelection()
        self.results_label.setText(self.tr("library.search_results", total=0))
        self.state_label.setText(state)
        self.page_label.setText(self.tr("library.page", page=1, pages=1))
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.feedback_label.clear()
        self._clear_detail()
        self._update_action_state()

    def _clear_detail(self) -> None:
        self._detail_record = None
        self.detail_title.clear()
        self.detail_metadata.clear()
        self.detail_prompt.clear()

    def _current_summary(self) -> PromptSummary | None:
        index = self.results_table.currentIndex()
        return self.results_model.item_at(index.row()) if index.isValid() else None

    def _load_prompt(self, prompt_id: int) -> PromptRecord | None:
        if not self._ensure_manager():
            return None
        try:
            return self.manager.get_prompt(prompt_id)
        except PromptLibraryError as exc:
            self._show_database_error(exc.code)
            return None

    def _record_for_copy(self, prompt_id: int) -> PromptRecord | None:
        if self._detail_record is not None and self._detail_record.id == prompt_id:
            return self._detail_record
        return self._load_prompt(prompt_id)

    def _update_action_state(self, *_args) -> None:
        has_current = self._current_summary() is not None
        self.show_button.setEnabled(has_current)
        self.copy_button.setEnabled(has_current)
        self.edit_button.setEnabled(has_current)
        self.delete_button.setEnabled(has_current)
        self.copy_checked_button.setEnabled(
            bool(self.results_model.checked_prompt_ids())
        )

    def _show_database_error(self, _code: str) -> None:
        self.results_model.set_items(())
        self._clear_detail()
        self.state_label.setText(self.tr("library.database_error"))
        self.feedback_label.setText(self.tr("library.database_error"))
        self._update_action_state()
