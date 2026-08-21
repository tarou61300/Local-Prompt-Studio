from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette, QShowEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
)

from core.prompt_translation import (
    JAPANESE_TO_ORIGINAL,
    ORIGINAL_TO_JAPANESE,
    ProtectedSpan,
    protected_spans,
    structure_tokens,
)


class StructureAwarePlainTextEdit(QPlainTextEdit):
    """Reject edits that alter protected structural regions while protection is on."""

    def __init__(self, protected_terms: tuple[str, ...], parent=None) -> None:
        super().__init__(parent)
        self._protected_terms = protected_terms
        self._structure_protection = True
        self._baseline_text = ""
        self._baseline_tokens: tuple[str, ...] = ()
        self._restoring = False
        self._highlighted_spans: tuple[ProtectedSpan, ...] = ()
        self.textChanged.connect(self._refresh_protected_highlights)

    @property
    def is_restoring(self) -> bool:
        return self._restoring

    @property
    def highlighted_spans(self) -> tuple[ProtectedSpan, ...]:
        return self._highlighted_spans

    def set_programmatic_text(self, text: str) -> None:
        self._restoring = True
        try:
            self.setPlainText(text)
        finally:
            self._restoring = False
        self._accept_as_baseline()
        self._refresh_protected_highlights()

    def set_structure_protection(self, enabled: bool) -> None:
        self._structure_protection = enabled
        self._accept_as_baseline()
        self._refresh_protected_highlights()

    def accept_user_change(self) -> bool:
        if self._restoring:
            return True
        text = self.toPlainText()
        tokens = structure_tokens(text, self._protected_terms)
        if self._structure_protection and tokens != self._baseline_tokens:
            self._restoring = True
            try:
                self.setPlainText(self._baseline_text)
            finally:
                self._restoring = False
            self._refresh_protected_highlights()
            return False
        self._baseline_text = text
        self._baseline_tokens = tokens
        self._refresh_protected_highlights()
        return True

    def _accept_as_baseline(self) -> None:
        self._baseline_text = self.toPlainText()
        self._baseline_tokens = structure_tokens(
            self._baseline_text,
            self._protected_terms,
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.PaletteChange
            and hasattr(self, "_protected_terms")
        ):
            self._refresh_protected_highlights()

    @staticmethod
    def _blend(base: QColor, accent: QColor, strength: float) -> QColor:
        inverse = 1.0 - strength
        return QColor(
            round(base.red() * inverse + accent.red() * strength),
            round(base.green() * inverse + accent.green() * strength),
            round(base.blue() * inverse + accent.blue() * strength),
        )

    def _refresh_protected_highlights(self) -> None:
        text = self.toPlainText()
        self._highlighted_spans = protected_spans(text, self._protected_terms)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        accent = palette.color(QPalette.ColorRole.Highlight)
        enabled = self._structure_protection
        char_format = QTextCharFormat()
        char_format.setBackground(
            self._blend(base, accent, 0.30 if enabled else 0.10)
        )
        char_format.setUnderlineColor(
            self._blend(base, accent, 0.75 if enabled else 0.45)
        )
        char_format.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.SingleUnderline
            if enabled
            else QTextCharFormat.UnderlineStyle.DotLine
        )
        selections: list[QTextEdit.ExtraSelection] = []
        for span in self._highlighted_spans:
            selection = QTextEdit.ExtraSelection()
            cursor = QTextCursor(self.document())
            cursor.setPosition(span.start)
            cursor.setPosition(span.end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selection.format = char_format
            selections.append(selection)
        self.setExtraSelections(selections)


class PromptTranslationDialog(QDialog):
    """Bidirectional, debounced prompt translation editor."""

    translation_requested = Signal(int, str, str, bool)

    def __init__(
        self,
        tr: Callable[..., str],
        original_text: str,
        *,
        protected_terms: tuple[str, ...] = (),
        parent=None,
        debounce_ms: int = 1000,
    ) -> None:
        super().__init__(parent)
        self.tr = tr
        self.initial_original_text = original_text
        self.protected_terms = tuple(protected_terms)
        self._revision = 0
        self._last_direction = ORIGINAL_TO_JAPANESE
        self._initial_translation_scheduled = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(debounce_ms)
        self._debounce.timeout.connect(self._request_translation)

        self.setObjectName("prompt_translation_dialog")
        self.setWindowTitle(self.tr("translation.title"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(980, 620)
        self.setMinimumSize(720, 440)

        root = QVBoxLayout(self)
        protection_row = QHBoxLayout()
        self.structure_protection = QCheckBox(
            self.tr("translation.structure_protection")
        )
        self.structure_protection.setObjectName("translation_structure_protection")
        self.structure_protection.setChecked(True)
        self.structure_protection.setToolTip(
            self.tr("translation.structure_protection_tooltip")
        )
        protection_row.addWidget(self.structure_protection)
        self.auto_translate = QCheckBox(self.tr("translation.auto_translate"))
        self.auto_translate.setObjectName("translation_auto_translate")
        self.auto_translate.setChecked(False)
        self.auto_translate.setToolTip(self.tr("translation.auto_translate_tooltip"))
        protection_row.addWidget(self.auto_translate)
        self.update_translation_button = QPushButton(
            self.tr("translation.update_translation")
        )
        self.update_translation_button.setObjectName("translation_update_button")
        self.update_translation_button.setToolTip(
            self.tr("translation.update_translation_tooltip")
        )
        protection_row.addWidget(self.update_translation_button)
        protection_row.addStretch()
        root.addLayout(protection_row)

        information_row = QHBoxLayout()
        self.protection_legend = QLabel(self.tr("translation.protected_legend"))
        self.protection_legend.setObjectName("translation_protection_legend")
        self.protection_legend.setWordWrap(True)
        self.protection_legend.setToolTip(
            self.tr("translation.structure_protection_tooltip")
        )
        information_row.addWidget(self.protection_legend, 1)
        self.source_label = QLabel("")
        self.source_label.setObjectName("translation_source")
        information_row.addWidget(self.source_label)
        root.addLayout(information_row)

        self.protection_warning = QLabel(
            self.tr("translation.structure_protection_disabled")
        )
        self.protection_warning.setObjectName("translation_protection_warning")
        self.protection_warning.setWordWrap(True)
        self.protection_warning.setVisible(False)
        root.addWidget(self.protection_warning)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("translation_splitter")
        splitter.setChildrenCollapsible(False)

        original_group = QGroupBox(self.tr("translation.original"))
        original_layout = QVBoxLayout(original_group)
        self.original_edit = StructureAwarePlainTextEdit(
            self.protected_terms,
            original_group,
        )
        self.original_edit.setObjectName("translation_original")
        self.original_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.original_edit.set_programmatic_text(original_text)
        original_layout.addWidget(self.original_edit)
        splitter.addWidget(original_group)

        japanese_group = QGroupBox(self.tr("translation.japanese"))
        japanese_layout = QVBoxLayout(japanese_group)
        self.japanese_edit = StructureAwarePlainTextEdit(
            self.protected_terms,
            japanese_group,
        )
        self.japanese_edit.setObjectName("translation_japanese")
        self.japanese_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.japanese_edit.set_programmatic_text("")
        japanese_layout.addWidget(self.japanese_edit)
        splitter.addWidget(japanese_group)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 480])
        root.addWidget(splitter, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("translation_status")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Apply
        )
        self.apply_button.setText(self.tr("translation.apply"))
        self.apply_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            self.tr("common.cancel")
        )
        self.apply_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.original_edit.textChanged.connect(
            lambda: self._on_user_edit(
                self.original_edit,
                ORIGINAL_TO_JAPANESE,
            )
        )
        self.japanese_edit.textChanged.connect(
            lambda: self._on_user_edit(
                self.japanese_edit,
                JAPANESE_TO_ORIGINAL,
            )
        )
        self.structure_protection.toggled.connect(
            self._on_structure_protection_toggled
        )
        self.auto_translate.toggled.connect(self._on_auto_translate_toggled)
        self.update_translation_button.clicked.connect(self._manual_update_translation)
        self._update_source_label()

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def last_direction(self) -> str:
        return self._last_direction

    def original_text(self) -> str:
        return self.original_edit.toPlainText()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._initial_translation_scheduled:
            self._initial_translation_scheduled = True
            QTimer.singleShot(0, self._request_initial_translation)

    def _request_initial_translation(self) -> None:
        source = self.original_edit.toPlainText()
        if not source.strip():
            return
        self._debounce.stop()
        self._revision += 1
        self._last_direction = ORIGINAL_TO_JAPANESE
        self._update_source_label()
        self.update_translation_button.setEnabled(True)
        self._request_translation()

    def schedule_translation(self, direction: str) -> None:
        source = self._source_editor(direction).toPlainText()
        if not source.strip():
            self.update_translation_button.setEnabled(False)
            return
        self._revision += 1
        self._last_direction = direction
        self._update_source_label()
        self.update_translation_button.setEnabled(True)
        if self.auto_translate.isChecked():
            self.apply_button.setEnabled(False)
            self.status_label.setText(self.tr("translation.status.waiting"))
            self._debounce.start()
        else:
            self._debounce.stop()
            self.status_label.setText(self.tr("translation.status.manual"))
            self.apply_button.setEnabled(bool(self.original_text().strip()))

    def mark_translating(self, revision: int) -> None:
        if revision == self._revision:
            self.status_label.setText(self.tr("translation.status.translating"))
            self.apply_button.setEnabled(False)

    def apply_translation_result(
        self,
        revision: int,
        direction: str,
        translated: str,
    ) -> bool:
        if revision != self._revision or direction != self._last_direction:
            return False
        target = (
            self.japanese_edit
            if direction == ORIGINAL_TO_JAPANESE
            else self.original_edit
        )
        target.set_programmatic_text(translated)
        self.status_label.setText(self.tr("translation.status.ready"))
        self.apply_button.setEnabled(bool(self.original_text().strip()))
        return True

    def apply_translation_error(self, revision: int, code: str) -> bool:
        if revision != self._revision:
            return False
        key = {
            "TRANSLATION_STRUCTURE_NOT_PRESERVED": "translation.error.structure",
            "TRANSLATION_EMPTY_RESPONSE": "translation.error.empty",
            "TRANSLATION_CANCELLED": "translation.error.cancelled",
        }.get(code, "translation.error.failed")
        self.status_label.setText(self.tr(key))
        self.apply_button.setEnabled(bool(self.original_text().strip()))
        return True

    def _source_editor(self, direction: str) -> StructureAwarePlainTextEdit:
        return (
            self.original_edit
            if direction == ORIGINAL_TO_JAPANESE
            else self.japanese_edit
        )

    def _on_user_edit(
        self,
        editor: StructureAwarePlainTextEdit,
        direction: str,
    ) -> None:
        if editor.is_restoring:
            return
        if not editor.accept_user_change():
            self.status_label.setText(self.tr("translation.structure_edit_blocked"))
            return
        self.schedule_translation(direction)

    def _request_translation(self) -> None:
        source = self._source_editor(self._last_direction).toPlainText()
        if not source.strip():
            return
        self.mark_translating(self._revision)
        self.translation_requested.emit(
            self._revision,
            self._last_direction,
            source,
            self.structure_protection.isChecked(),
        )

    def _on_structure_protection_toggled(self, enabled: bool) -> None:
        self.original_edit.set_structure_protection(enabled)
        self.japanese_edit.set_structure_protection(enabled)
        self.protection_warning.setVisible(not enabled)
        self.schedule_translation(self._last_direction)

    def _on_auto_translate_toggled(self, enabled: bool) -> None:
        self._debounce.stop()
        if enabled:
            self.schedule_translation(self._last_direction)
            return
        self._revision += 1
        self._update_source_label()
        self.status_label.setText(self.tr("translation.status.manual"))
        self.apply_button.setEnabled(bool(self.original_text().strip()))

    def _manual_update_translation(self) -> None:
        source = self._source_editor(self._last_direction).toPlainText()
        if not source.strip():
            return
        self._debounce.stop()
        self._revision += 1
        self._update_source_label()
        self._request_translation()

    def _update_source_label(self) -> None:
        key = (
            "translation.source.original"
            if self._last_direction == ORIGINAL_TO_JAPANESE
            else "translation.source.japanese"
        )
        self.source_label.setText(self.tr(key))
