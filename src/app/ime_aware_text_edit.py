from __future__ import annotations

from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QPlainTextEdit


class ImeAwarePlaceholderPlainTextEdit(QPlainTextEdit):
    """Hide the placeholder while an IME preedit string is active."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._configured_placeholder = ""
        self._preedit_text = ""
        self.textChanged.connect(self._sync_placeholder)

    def setPlaceholderText(self, placeholder_text: str) -> None:
        self._configured_placeholder = str(placeholder_text)
        self._sync_placeholder()

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self._preedit_text = event.preeditString()
        self._sync_placeholder()
        super().inputMethodEvent(event)
        self._sync_placeholder()

    def _sync_placeholder(self) -> None:
        show_placeholder = not self.toPlainText() and not self._preedit_text
        visible_text = self._configured_placeholder if show_placeholder else ""
        if QPlainTextEdit.placeholderText(self) != visible_text:
            QPlainTextEdit.setPlaceholderText(self, visible_text)
