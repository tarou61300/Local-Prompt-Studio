from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .ime_aware_text_edit import ImeAwarePlaceholderPlainTextEdit


class ChatMessageWidget(QFrame):
    transfer_requested = Signal(str)

    def __init__(self, role: str, text: str, tr, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(f"chat_{role}_message")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        role_label = QLabel(tr("chat.role.user") if role == "user" else tr("chat.role.assistant"))
        role_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(role_label)
        body = QLabel(text)
        body.setObjectName("chat_message_body")
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(body)
        if role == "assistant":
            actions = QHBoxLayout()
            copy_button = QPushButton(tr("chat.copy"))
            copy_button.setObjectName("chat_copy_button")
            copy_button.clicked.connect(
                lambda: QApplication.clipboard().setText(text)
            )
            transfer_button = QPushButton(tr("chat.transfer"))
            transfer_button.setObjectName("chat_transfer_button")
            transfer_button.clicked.connect(lambda: self.transfer_requested.emit(text))
            actions.addStretch()
            actions.addWidget(copy_button)
            actions.addWidget(transfer_button)
            layout.addLayout(actions)


class ChatPage(QWidget):
    send_requested = Signal(str)
    cancel_requested = Signal()
    new_chat_requested = Signal()
    target_profile_requested = Signal(str)
    target_task_requested = Signal(str)
    transfer_requested = Signal(str, str)
    open_prompt_requested = Signal(str)

    def __init__(self, tr, parent=None) -> None:
        super().__init__(parent)
        self.tr = tr
        self._transfer_text = ""
        self._notification_destination = "common"
        self._profiles: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
        self._syncing_target = False
        self._any_llm_busy = False

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel(self.tr("chat.title"))
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        self.new_chat_button = QPushButton(self.tr("chat.new"))
        self.new_chat_button.setObjectName("chat_new_button")
        self.new_chat_button.clicked.connect(self.new_chat_requested)
        root.addLayout(header)

        self.conversation_scroll = QScrollArea()
        self.conversation_scroll.setObjectName("chat_conversation_scroll")
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.conversation_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.conversation_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.conversation_widget = QWidget()
        self.messages_layout = QVBoxLayout(self.conversation_widget)
        self.messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.empty_label = QLabel(self.tr("chat.empty"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: palette(mid);")
        self.messages_layout.addWidget(self.empty_label)
        self.conversation_scroll.setWidget(self.conversation_widget)

        self.transfer_panel = QGroupBox(self.tr("chat.transfer_title"))
        self.transfer_panel.setObjectName("chat_transfer_panel")
        transfer_layout = QVBoxLayout(self.transfer_panel)
        target_row = QHBoxLayout()
        self.target_label = QLabel()
        self.target_label.setObjectName("chat_target_label")
        self.target_label.setWordWrap(True)
        target_row.addWidget(self.target_label, 1)
        self.change_target_button = QPushButton(self.tr("chat.change_target"))
        self.change_target_button.setObjectName("chat_change_target_button")
        self.change_target_button.setCheckable(True)
        self.change_target_button.toggled.connect(self._toggle_target_chooser)
        target_row.addWidget(self.change_target_button)
        transfer_layout.addLayout(target_row)
        self.target_chooser = QWidget()
        chooser_layout = QHBoxLayout(self.target_chooser)
        chooser_layout.setContentsMargins(0, 0, 0, 0)
        self.target_profile = QComboBox()
        self.target_profile.setObjectName("chat_target_profile")
        self.target_task = QComboBox()
        self.target_task.setObjectName("chat_target_task")
        chooser_layout.addWidget(self.target_profile, 1)
        chooser_layout.addWidget(self.target_task, 1)
        self.target_chooser.setVisible(False)
        transfer_layout.addWidget(self.target_chooser)
        destination_row = QHBoxLayout()
        destination_row.addWidget(QLabel(self.tr("chat.destination")))
        self.destination = QComboBox()
        self.destination.setObjectName("chat_transfer_destination")
        destination_row.addWidget(self.destination, 1)
        self.transfer_button = QPushButton(self.tr("chat.transfer_action"))
        self.transfer_button.setObjectName("chat_transfer_action")
        self.transfer_button.clicked.connect(self._emit_transfer)
        destination_row.addWidget(self.transfer_button)
        transfer_layout.addLayout(destination_row)
        self.transfer_panel.setVisible(False)

        self.notification = QFrame()
        self.notification.setObjectName("chat_transfer_notification")
        notification_layout = QHBoxLayout(self.notification)
        notification_layout.setContentsMargins(8, 4, 8, 4)
        self.notification_label = QLabel()
        self.notification_label.setWordWrap(True)
        notification_layout.addWidget(self.notification_label, 1)
        open_button = QPushButton(self.tr("chat.open_prompt"))
        open_button.clicked.connect(
            lambda: self.open_prompt_requested.emit(self._notification_destination)
        )
        notification_layout.addWidget(open_button)
        self.notification.setVisible(False)

        self.input_group = QGroupBox(self.tr("chat.input"))
        self.input_group.setObjectName("chat_input_group")
        self.input_group.setMinimumHeight(120)
        self.input_group.setMaximumHeight(160)
        self.input_group.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        input_layout = QVBoxLayout(self.input_group)
        self.input_text = ImeAwarePlaceholderPlainTextEdit()
        self.input_text.setObjectName("chat_input")
        self.input_text.setPlaceholderText(self.tr("chat.placeholder"))
        self.input_text.setMinimumHeight(72)
        self.input_text.setMaximumHeight(98)
        self.input_text.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        input_layout.addWidget(self.input_text)
        input_actions = QHBoxLayout()
        input_actions.addWidget(self.new_chat_button)
        input_actions.addStretch()
        self.cancel_button = QPushButton(self.tr("common.cancel"))
        self.cancel_button.setObjectName("chat_cancel_button")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.send_button = QPushButton(self.tr("chat.send"))
        self.send_button.setObjectName("chat_send_button")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._emit_send)
        input_actions.addWidget(self.cancel_button)
        input_actions.addWidget(self.send_button)
        input_layout.addLayout(input_actions)
        self.status_label = QLabel()
        self.status_label.setObjectName("chat_status")
        self.status_label.setWordWrap(True)

        # The conversation owns the remaining height above a compact input area.
        # The lower action row can accept attachment buttons in a later phase
        # without restructuring the page.
        root.addWidget(self.conversation_scroll, 1)
        root.addWidget(self.transfer_panel)
        root.addWidget(self.notification)
        root.addWidget(self.input_group)
        root.addWidget(self.status_label)
        self.input_text.textChanged.connect(self._update_send_state)
        self.target_profile.currentIndexChanged.connect(self._profile_changed)
        self.target_task.currentIndexChanged.connect(self._task_changed)

    def add_message(self, role: str, text: str) -> None:
        self.empty_label.setVisible(False)
        message = ChatMessageWidget(role, text, self.tr, self.conversation_widget)
        message.transfer_requested.connect(self.open_transfer_panel)
        self.messages_layout.addWidget(message)
        QTimer.singleShot(0, lambda: self._scroll_to_latest(message))

    def _scroll_to_latest(self, message: QWidget) -> None:
        self.messages_layout.activate()
        self.conversation_scroll.ensureWidgetVisible(message, 0, 0)
        scroll_bar = self.conversation_scroll.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
        # Word-wrapped labels can finalize their height one event turn later.
        QTimer.singleShot(0, lambda: scroll_bar.setValue(scroll_bar.maximum()))

    def clear_messages(self) -> None:
        for index in range(self.messages_layout.count() - 1, -1, -1):
            item = self.messages_layout.itemAt(index)
            widget = item.widget()
            if widget is not None and widget is not self.empty_label:
                self.messages_layout.takeAt(index)
                widget.deleteLater()
        self.empty_label.setVisible(True)
        self.transfer_panel.setVisible(False)
        self.notification.setVisible(False)
        self._transfer_text = ""

    def set_busy(self, *, any_llm_busy: bool, chat_busy: bool) -> None:
        self._any_llm_busy = any_llm_busy
        self.input_text.setEnabled(not chat_busy)
        self.cancel_button.setEnabled(chat_busy)
        self.new_chat_button.setEnabled(not chat_busy)
        self._update_send_state()

    def _update_send_state(self) -> None:
        self.send_button.setEnabled(
            bool(self.input_text.toPlainText().strip()) and not self._any_llm_busy
        )

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet("color: #b00020;" if error else "")

    def _emit_send(self) -> None:
        text = self.input_text.toPlainText().strip()
        if text:
            self.send_requested.emit(text)

    def open_transfer_panel(self, text: str) -> None:
        self._transfer_text = text
        self.notification.setVisible(False)
        self.transfer_panel.setVisible(True)

    def set_target_catalog(
        self,
        profiles: Sequence[tuple[str, str, tuple[str, ...]]],
    ) -> None:
        self._profiles = tuple(profiles)
        self._syncing_target = True
        self.target_profile.clear()
        for profile_id, name, _tasks in self._profiles:
            self.target_profile.addItem(name, profile_id)
        self._syncing_target = False

    def sync_target(
        self,
        *,
        profile_id: str,
        profile_name: str,
        task: str,
        destinations: Sequence[tuple[str, str]],
    ) -> None:
        self._syncing_target = True
        profile_index = self.target_profile.findData(profile_id)
        if profile_index >= 0:
            self.target_profile.setCurrentIndex(profile_index)
        self.target_task.clear()
        tasks = next(
            (values for key, _name, values in self._profiles if key == profile_id),
            (),
        )
        for value in tasks:
            self.target_task.addItem(value, value)
        task_index = self.target_task.findData(task)
        self.target_task.setCurrentIndex(task_index if task_index >= 0 else 0)
        current_destination = self.destination.currentData()
        self.destination.clear()
        for key, label in destinations:
            self.destination.addItem(label, key)
        destination_index = self.destination.findData(current_destination)
        self.destination.setCurrentIndex(destination_index if destination_index >= 0 else 0)
        self.target_label.setText(
            self.tr("chat.current_target", profile=profile_name, task=task)
        )
        self.target_label.setToolTip(f"{profile_name} / {task}")
        self._syncing_target = False

    def _toggle_target_chooser(self, visible: bool) -> None:
        self.target_chooser.setVisible(visible)

    def _profile_changed(self) -> None:
        if not self._syncing_target:
            profile_id = self.target_profile.currentData()
            if profile_id:
                self.target_profile_requested.emit(str(profile_id))

    def _task_changed(self) -> None:
        if not self._syncing_target:
            task = self.target_task.currentData()
            if task:
                self.target_task_requested.emit(str(task))

    def _emit_transfer(self) -> None:
        destination = str(self.destination.currentData() or "")
        if self._transfer_text and destination:
            self.transfer_requested.emit(self._transfer_text, destination)

    def show_transfer_complete(
        self,
        destination: str,
        destination_label: str,
        target: str,
    ) -> None:
        self._notification_destination = destination
        self.notification_label.setText(
            self.tr(
                "chat.transfer_complete",
                target=target,
                destination=destination_label,
            )
        )
        self.notification.setVisible(True)
        self.transfer_panel.setVisible(False)
