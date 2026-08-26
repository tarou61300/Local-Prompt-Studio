from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.prompt_library_manager import (
    PromptLibraryError,
    PromptLibraryManager,
    PromptLibraryValidationError,
    TagRecord,
    normalize_tag_name,
)

from .tag_selector import TagSelector


class PromptLibraryTagEditor(TagSelector):
    """Global existing-tag chooser with transaction-pending new tag names."""

    def __init__(
        self,
        tr: Callable[..., str],
        parent: QWidget | None = None,
    ) -> None:
        self._pending: dict[str, str] = {}
        super().__init__(tr, parent, allow_favorite_edit=True)

        self.search_label.setText(self.tr("library.new_tag"))
        self.search_edit.setObjectName("prompt_library_new_tag")
        self.search_edit.setPlaceholderText(self.tr("library.new_tag.placeholder"))
        root = self.layout()
        assert isinstance(root, QVBoxLayout)
        root.removeWidget(self.search_edit)
        add_row = QHBoxLayout()
        add_row.addWidget(self.search_edit, 1)
        self.add_button = QPushButton(self.tr("library.add_tag"))
        self.add_button.setObjectName("prompt_library_add_tag")
        self.add_button.clicked.connect(self.add_pending_tag)
        add_row.addWidget(self.add_button)
        root.insertLayout(1, add_row)
        self.search_edit.returnPressed.connect(self.add_pending_tag)

        self.guidance_label = QLabel()
        self.guidance_label.setObjectName("prompt_library_tag_guidance")
        self.guidance_label.setWordWrap(True)
        self.guidance_label.setStyleSheet("color: palette(placeholder-text);")
        root.insertWidget(2, self.guidance_label)
        self.other_label.setText(self.tr("library.existing_tag_suggestions"))

    def set_manager(self, manager: PromptLibraryManager) -> None:
        super().set_manager(manager)
        self._reload_candidates()

    def set_selected_tags(self, tags: tuple[TagRecord, ...]) -> None:
        self._pending.clear()
        super().set_selected_tags(tags)

    def pending_tag_names(self) -> tuple[str, ...]:
        return tuple(self._pending.values())

    def pending_button(self, normalized_name: str) -> QToolButton | None:
        return self.selected_widget.findChild(
            QToolButton,
            f"prompt_library_pending_tag_{normalized_name}",
        )

    def clear_selection(self) -> None:
        had_pending = bool(self._pending)
        self._pending.clear()
        had_selected = bool(self._selected)
        super().clear_selection()
        if had_pending and not had_selected:
            self._render_selected()
            self.selection_changed.emit()

    def _reload_candidates(self, _text: str | None = None) -> None:
        if self._manager is None:
            self._render_candidates(())
            return
        try:
            candidates = self._manager.search_existing_tags(
                self.search_edit.text()
            )
        except PromptLibraryError as exc:
            self._render_candidates(())
            self.error_occurred.emit(exc.code)
            return
        self._render_candidates(candidates)

    def add_pending_tag(self) -> None:
        if self._manager is None:
            return
        raw_name = self.search_edit.text()
        try:
            display_name, normalized_name = normalize_tag_name(raw_name)
            candidates = self._manager.search_existing_tags(raw_name)
        except PromptLibraryValidationError:
            self.guidance_label.setText(self.tr("library.tag_required"))
            return
        except PromptLibraryError as exc:
            self.error_occurred.emit(exc.code)
            return

        exact = next(
            (
                candidate.tag
                for candidate in candidates
                if candidate.tag.normalized_name == normalized_name
            ),
            None,
        )
        if exact is not None:
            self._pending.pop(normalized_name, None)
            self._selected[exact.id] = exact
            self.guidance_label.setText(
                self.tr("library.existing_tag_used", tag=exact.name)
            )
        else:
            self._pending[normalized_name] = display_name
            self.guidance_label.clear()
        self.search_edit.clear()
        self._render_selected()
        self._sync_candidate_checks()
        self.selection_changed.emit()

    def _candidate_toggled(self, tag: TagRecord, checked: bool) -> None:
        if checked:
            self._pending.pop(tag.normalized_name, None)
        super()._candidate_toggled(tag, checked)

    def _remove_pending(self, normalized_name: str) -> None:
        if self._pending.pop(normalized_name, None) is None:
            return
        self._render_selected()
        self.selection_changed.emit()

    def _render_selected(self) -> None:
        self._clear_grid(self.selected_layout)
        index = 0
        for tag in self._selected.values():
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
            index += 1
        for normalized_name, display_name in self._pending.items():
            button = QToolButton()
            button.setObjectName(f"prompt_library_pending_tag_{normalized_name}")
            button.setText(f"{display_name} ×")
            button.setToolTip(
                self.tr("library.remove_selected_tag", tag=display_name)
            )
            button.setSizePolicy(
                QSizePolicy.Policy.Maximum,
                QSizePolicy.Policy.Fixed,
            )
            button.clicked.connect(
                lambda _checked=False,
                normalized=normalized_name: self._remove_pending(normalized)
            )
            self.selected_layout.addWidget(
                button,
                index // 4,
                index % 4,
                Qt.AlignmentFlag.AlignLeft,
            )
            index += 1
        visible = bool(self._selected or self._pending)
        self.selected_widget.setVisible(visible)
        self.selected_label.setVisible(visible)
