from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.prompt_library_manager import (
    PromptLibraryError,
    PromptLibraryManager,
    TagCandidate,
    TagRecord,
)


class TagSelector(QWidget):
    """Bounded, reusable tag chooser backed by PromptLibraryManager queries."""

    selection_changed = Signal()
    favorite_changed = Signal(int, bool)
    error_occurred = Signal(str)

    def __init__(
        self,
        tr: Callable[..., str],
        parent: QWidget | None = None,
        *,
        allow_favorite_edit: bool = False,
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self.allow_favorite_edit = allow_favorite_edit
        self.setStyleSheet(
            """
            QToolButton:checked {
                border: 1px solid palette(highlight);
                border-radius: 3px;
                background: palette(highlight);
                color: palette(highlighted-text);
            }
            """
        )
        self._manager: PromptLibraryManager | None = None
        self._model_id = ""
        self._task_id = ""
        self._selected: dict[int, TagRecord] = {}
        self._candidate_buttons: dict[int, QToolButton] = {}

        self._favorite_buttons: dict[int, QToolButton] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.search_label = QLabel(self.tr("library.find_tags"))
        root.addWidget(self.search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("prompt_library_tag_search")
        self.search_edit.setPlaceholderText(self.tr("library.find_tags.placeholder"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._reload_candidates)
        root.addWidget(self.search_edit)

        self.selected_label = QLabel(self.tr("library.selected"))
        self.selected_label.setObjectName("prompt_library_selected_label")
        root.addWidget(self.selected_label)
        self.selected_widget = QWidget()
        self.selected_widget.setObjectName("prompt_library_selected_tags")
        self.selected_layout = QGridLayout(self.selected_widget)
        self.selected_layout.setContentsMargins(0, 0, 0, 0)
        self.selected_layout.setHorizontalSpacing(6)
        self.selected_layout.setVerticalSpacing(4)
        root.addWidget(self.selected_widget)

        self.candidate_scroll = QScrollArea()
        self.candidate_scroll.setObjectName("prompt_library_tag_candidates_scroll")
        self.candidate_scroll.setWidgetResizable(True)
        self.candidate_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.candidate_scroll.setMinimumHeight(120)
        self.candidate_scroll.setMaximumHeight(220)
        self.candidate_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.candidate_widget = QWidget()
        self.candidate_root = QVBoxLayout(self.candidate_widget)
        self.candidate_root.setContentsMargins(4, 4, 4, 4)
        self.candidate_root.setSpacing(5)

        self.favorite_label = QLabel(self.tr("library.favorites"))
        self.favorite_label.setObjectName("prompt_library_favorites_label")
        self.candidate_root.addWidget(self.favorite_label)
        self.favorite_widget = QWidget()
        self.favorite_layout = QGridLayout(self.favorite_widget)
        self.favorite_layout.setContentsMargins(0, 0, 0, 0)
        self.favorite_layout.setHorizontalSpacing(6)
        self.favorite_layout.setVerticalSpacing(4)
        self.candidate_root.addWidget(self.favorite_widget)

        self.other_label = QLabel(self.tr("library.tags"))
        self.other_label.setObjectName("prompt_library_other_tags_label")
        self.candidate_root.addWidget(self.other_label)
        self.other_widget = QWidget()
        self.other_layout = QGridLayout(self.other_widget)
        self.other_layout.setContentsMargins(0, 0, 0, 0)
        self.other_layout.setHorizontalSpacing(6)
        self.other_layout.setVerticalSpacing(4)
        self.candidate_root.addWidget(self.other_widget)
        self.candidate_root.addStretch()
        self.candidate_scroll.setWidget(self.candidate_widget)
        root.addWidget(self.candidate_scroll)

        self._render_selected()
        self._render_candidates(())

    def set_manager(self, manager: PromptLibraryManager) -> None:
        self._manager = manager

    def set_selected_tags(self, tags: tuple[TagRecord, ...]) -> None:
        self._selected = {tag.id: tag for tag in tags}
        self._render_selected()
        self._sync_candidate_checks()
        self.selection_changed.emit()

    def refresh_candidates(self) -> None:
        self._reload_candidates()

    def set_target(self, model_id: str, task_id: str) -> None:
        self._model_id = model_id
        self._task_id = task_id
        self.clear_selection()
        self.clear_search()
        self._reload_candidates()

    def selected_tag_ids(self) -> tuple[int, ...]:
        return tuple(self._selected)

    def clear_selection(self) -> None:
        if not self._selected:
            self._sync_candidate_checks()
            return
        self._selected.clear()
        self._render_selected()
        self._sync_candidate_checks()
        self.selection_changed.emit()

    def clear_search(self) -> None:
        if self.search_edit.text():
            self.search_edit.clear()

    def candidate_count(self) -> int:
        return len(self._candidate_buttons)

    def candidate_button(self, tag_id: int) -> QToolButton | None:
        return self._candidate_buttons.get(tag_id)

    def favorite_button(self, tag_id: int) -> QToolButton | None:
        return self._favorite_buttons.get(tag_id)

    def selected_button(self, tag_id: int) -> QToolButton | None:
        return self.selected_widget.findChild(
            QToolButton,
            f"prompt_library_selected_tag_{tag_id}",
        )

    def _reload_candidates(self, _text: str | None = None) -> None:
        if self._manager is None or not self._model_id or not self._task_id:
            self._render_candidates(())
            return
        try:
            query = self.search_edit.text()
            if query.strip():
                candidates = self._manager.search_tag_candidates(
                    model_id=self._model_id,
                    task_id=self._task_id,
                    query=query,
                )
            else:
                candidates = self._manager.list_tag_candidates(
                    model_id=self._model_id,
                    task_id=self._task_id,
                )
        except PromptLibraryError as exc:
            self._render_candidates(())
            self.error_occurred.emit(exc.code)
            return
        self._render_candidates(candidates)

    @staticmethod
    def _clear_grid(layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_candidates(self, candidates: tuple[TagCandidate, ...]) -> None:
        self._clear_grid(self.favorite_layout)
        self._clear_grid(self.other_layout)
        self._candidate_buttons.clear()
        self._favorite_buttons.clear()
        favorite_count = 0
        other_count = 0
        for candidate in candidates:
            button = QToolButton()
            button.setObjectName(
                f"prompt_library_candidate_tag_{candidate.tag.id}"
            )
            button.setProperty("tag_id", candidate.tag.id)
            button.setCheckable(True)
            button.setChecked(candidate.tag.id in self._selected)
            button.setText(candidate.tag.name)
            button.setToolTip(candidate.tag.name)
            button.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed,
            )
            button.toggled.connect(
                lambda checked, tag=candidate.tag: self._candidate_toggled(
                    tag, checked
                )
            )
            self._candidate_buttons[candidate.tag.id] = button
            candidate_widget: QWidget = button
            if self.allow_favorite_edit:
                candidate_widget = QWidget()
                candidate_layout = QHBoxLayout(candidate_widget)
                candidate_layout.setContentsMargins(0, 0, 0, 0)
                candidate_layout.setSpacing(2)
                favorite_button = QToolButton()
                favorite_button.setObjectName(
                    f"prompt_library_favorite_tag_{candidate.tag.id}"
                )
                favorite_button.setText("★" if candidate.is_favorite else "☆")
                favorite_button.setToolTip(
                    self.tr(
                        "library.remove_favorite"
                        if candidate.is_favorite
                        else "library.add_favorite"
                    )
                )
                favorite_button.clicked.connect(
                    lambda _checked=False,
                    tag_id=candidate.tag.id,
                    favorite=candidate.is_favorite: self._toggle_favorite(
                        tag_id, not favorite
                    )
                )
                self._favorite_buttons[candidate.tag.id] = favorite_button
                candidate_layout.addWidget(favorite_button)
                candidate_layout.addWidget(button)
                candidate_layout.addStretch()
            if candidate.is_favorite:
                index = favorite_count
                favorite_count += 1
                self.favorite_layout.addWidget(
                    candidate_widget,
                    index // 4,
                    index % 4,
                    Qt.AlignmentFlag.AlignLeft,
                )
            else:
                index = other_count
                other_count += 1
                self.other_layout.addWidget(
                    candidate_widget,
                    index // 4,
                    index % 4,
                    Qt.AlignmentFlag.AlignLeft,
                )
        has_favorites = favorite_count > 0
        self.favorite_label.setVisible(has_favorites)
        self.favorite_widget.setVisible(has_favorites)
        self.other_label.setVisible(other_count > 0)
        self.other_widget.setVisible(other_count > 0)


    def _toggle_favorite(self, tag_id: int, favorite: bool) -> None:
        if self._manager is None or not self.allow_favorite_edit:
            return
        try:
            self._manager.set_tag_favorite(tag_id, favorite)
        except PromptLibraryError as exc:
            self.error_occurred.emit(exc.code)
            return
        self._reload_candidates()
        self.favorite_changed.emit(tag_id, favorite)
    def _candidate_toggled(self, tag: TagRecord, checked: bool) -> None:
        if checked:
            self._selected[tag.id] = tag
        else:
            self._selected.pop(tag.id, None)
        self._render_selected()
        self._sync_candidate_checks()
        self.selection_changed.emit()

    def _remove_selected(self, tag_id: int) -> None:
        if self._selected.pop(tag_id, None) is None:
            return
        self._render_selected()
        self._sync_candidate_checks()
        self.selection_changed.emit()

    def _render_selected(self) -> None:
        self._clear_grid(self.selected_layout)
        for index, tag in enumerate(self._selected.values()):
            button = QToolButton()
            button.setObjectName(f"prompt_library_selected_tag_{tag.id}")
            button.setProperty("tag_id", tag.id)
            button.setText(f"{tag.name} ×")
            button.setToolTip(self.tr("library.remove_selected_tag", tag=tag.name))
            button.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed,
            )
            button.clicked.connect(
                lambda _checked=False, tag_id=tag.id: self._remove_selected(tag_id)
            )
            self.selected_layout.addWidget(
                button,
                index // 4,
                index % 4,
                Qt.AlignmentFlag.AlignLeft,
            )
        self.selected_widget.setVisible(bool(self._selected))
        self.selected_label.setVisible(bool(self._selected))

    def _sync_candidate_checks(self) -> None:
        for tag_id, button in self._candidate_buttons.items():
            checked = tag_id in self._selected
            if button.isChecked() == checked:
                continue
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)
