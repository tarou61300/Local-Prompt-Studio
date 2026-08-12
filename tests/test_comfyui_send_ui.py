from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton
from shiboken6 import isValid

from app.main_window import MainWindow
from core.comfyui_bridge import ComfyUIBridgeError, SendResult
from core.config_manager import AppConfig, ConfigManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"
REMOTE_URL = "https://comfy.example.com"


class FakeSendService:
    def __init__(self, *, error_code: str | None = None, blocked: bool = False) -> None:
        self.error_code = error_code
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()

    def send(self, text: str) -> SendResult:
        self.calls.append(text)
        self.started.set()
        self.release.wait(3.0)
        if self.error_code is not None:
            raise ComfyUIBridgeError(
                self.error_code,
                "private remote response that must not reach the UI",
            )
        return SendResult(status="success", request_id="synthetic-request-id")


class FakeServiceFactory:
    def __init__(self, service: FakeSendService) -> None:
        self.service = service
        self.calls = []

    def __call__(self, base_url: str) -> FakeSendService:
        self.calls.append(base_url)
        return self.service


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app: QApplication, predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def make_window(
    tmp_path,
    factory: FakeServiceFactory,
    *,
    comfyui_url: str = REMOTE_URL,
) -> MainWindow:
    manager = ConfigManager(tmp_path)
    manager.save(
        AppConfig(
            skill_location=str(SKILL_FIXTURE),
            setup_completed=True,
            comfyui_url=comfyui_url,
        )
    )
    return MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=manager,
        server_url="http://127.0.0.1:1",
        dev_skill_path=SKILL_FIXTURE,
        bridge_service_factory=factory,
    )


def close_window(window: MainWindow, app: QApplication) -> None:
    if window._send_worker is not None:
        window._send_worker.requestInterruption()
    window.close()
    app.processEvents()


def test_send_button_starts_blank_and_startup_creates_no_comfyui_work(
    tmp_path, app, monkeypatch
):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("MainWindow startup must not create ComfyUI work")

    class ForbiddenSendWorker:
        def __init__(self, *args, **kwargs):
            forbidden(*args, **kwargs)

    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("app.main_window.ComfyUISendThread", ForbiddenSendWorker)
    service = FakeSendService()
    factory = FakeServiceFactory(service)
    window = make_window(tmp_path, factory)
    try:
        window.show()
        app.processEvents()
        assert window.send_comfyui_button.text() == "Send to ComfyUI"
        assert window.send_comfyui_button.isVisible() is True
        assert window.send_comfyui_button.toolTip() == window.tr("comfyui.send_current")
        assert window.send_comfyui_button.isEnabled() is False
        assert window._send_worker is None
        assert factory.calls == []
        assert service.calls == []
        assert forbidden_calls == []
        window.output_text.setPlainText("synthetic output")
        assert window.send_comfyui_button.isEnabled() is True
        window.output_text.clear()
        assert window.send_comfyui_button.isEnabled() is False
    finally:
        close_window(window, app)


def test_send_uses_current_edited_snapshot_and_prevents_duplicate(
    tmp_path, app
):
    service = FakeSendService(blocked=True)
    factory = FakeServiceFactory(service)
    window = make_window(tmp_path, factory)
    try:
        window.output_text.setPlainText("synthetic generated output")
        window.output_text.setPlainText("synthetic manually edited output")
        window.send_comfyui_button.click()
        wait_until(app, service.started.is_set)
        worker = window._send_worker
        assert worker is not None and worker.isRunning()
        assert service.calls == ["synthetic manually edited output"]
        assert factory.calls == [REMOTE_URL]
        assert window.send_comfyui_button.isEnabled() is False
        assert window.generate_button.isEnabled() is False
        assert window.regenerate_button.isEnabled() is False
        assert window.settings_action.isEnabled() is False
        assert window.output_text.isEnabled() is True

        window.output_text.setPlainText("synthetic edit made while send is active")
        window.send_comfyui_button.click()
        window._send_to_comfyui()
        assert service.calls == ["synthetic manually edited output"]
        assert window.worker is None
        window.generate()
        assert window.worker is None
        service.release.set()
        wait_until(app, lambda: window._send_worker is None)
        wait_until(app, lambda: not isValid(worker))
        assert service.calls == ["synthetic manually edited output"]
        assert window.output_text.toPlainText() == "synthetic edit made while send is active"
        assert window.status_label.text() == "Sent to ComfyUI."
        assert window.send_comfyui_button.isEnabled() is True
        assert window.generate_button.isEnabled() is True
        assert window.regenerate_button.isEnabled() is True
        assert window.settings_action.isEnabled() is True
    finally:
        close_window(window, app)


@pytest.mark.parametrize(
    ("code", "expected_text"),
    [
        ("credential_unavailable", "not paired"),
        ("credential_url_mismatch", "not paired"),
        ("unauthorized_client", "no longer valid"),
        ("no_target", "No MMH3 target"),
        ("target_not_found", "No MMH3 target"),
        ("stale_target", "no longer active"),
        ("stale_session", "no longer active"),
        ("widget_not_found", "no longer available"),
        ("invalid_widget", "no longer available"),
        ("bridge_busy", "Bridge is busy"),
        ("timeout", "may already have been applied"),
        ("ack_timeout", "may already have been applied"),
        ("bridge_unavailable", "may already have been applied"),
        ("text_too_large", "too large"),
        ("unsupported_bridge_version", "not compatible"),
        ("malformed_response", "invalid response"),
        ("rate_limited", "rate-limiting"),
        ("compatibility_unavailable", "cannot receive text"),
    ],
)
def test_send_errors_use_safe_stable_messages_without_retry(
    tmp_path, app, code, expected_text
):
    service = FakeSendService(error_code=code)
    factory = FakeServiceFactory(service)
    window = make_window(tmp_path, factory)
    try:
        output = f"synthetic output for {code}"
        window.output_text.setPlainText(output)
        window.send_comfyui_button.click()
        wait_until(app, lambda: window._send_worker is None)
        assert service.calls == [output]
        assert expected_text in window.status_label.text()
        assert "private remote response" not in window.status_label.text()
        assert REMOTE_URL not in window.status_label.text()
        assert output not in window.status_label.text()
        assert window.output_text.toPlainText() == output
        assert window.send_comfyui_button.isEnabled() is True
        assert window.generate_button.isEnabled() is True
        assert window.regenerate_button.isEnabled() is True
        assert window.settings_action.isEnabled() is True
    finally:
        close_window(window, app)


def test_close_during_send_retains_worker_and_suppresses_result_ui(
    tmp_path, app
):
    service = FakeSendService(blocked=True)
    factory = FakeServiceFactory(service)
    window = make_window(tmp_path, factory)
    window.show()
    window.output_text.setPlainText("synthetic shutdown output")
    window.send_comfyui_button.click()
    wait_until(app, service.started.is_set)
    worker = window._send_worker
    cleanup_calls = []
    assert worker is not None
    worker.finished.connect(lambda: cleanup_calls.append(True))
    status_before_close = window.status_label.text()

    window.close()

    assert window.isVisible() is True
    assert window._send_worker is worker
    assert worker.parent() is window
    assert worker.isRunning() is True
    assert worker.isInterruptionRequested() is True
    assert service.calls == ["synthetic shutdown output"]
    service.release.set()
    wait_until(app, lambda: window._send_worker is None)
    wait_until(app, lambda: not isValid(worker))
    wait_until(app, lambda: not window.isVisible())
    assert cleanup_calls == [True]
    assert service.calls == ["synthetic shutdown output"]
    assert window.status_label.text() == status_before_close


def test_application_quit_during_send_executes_once_without_recursion(
    tmp_path, app, monkeypatch
):
    queued_callbacks = []

    class QueuedTimer:
        @staticmethod
        def singleShot(delay, callback):
            assert delay == 0
            queued_callbacks.append(callback)

    class QuitProxy(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.window = None
            self.quit_calls = 0
            self.intercepted = []
            self.completed_shutdowns = 0

        def quit(self) -> None:
            self.quit_calls += 1
            intercepted = self.window.eventFilter(self, QEvent(QEvent.Quit))
            self.intercepted.append(intercepted)
            if not intercepted:
                self.completed_shutdowns += 1

    service = FakeSendService(blocked=True)
    factory = FakeServiceFactory(service)
    window = make_window(tmp_path, factory)
    window.show()
    application = QuitProxy()
    application.window = window
    window._application = application
    monkeypatch.setattr("app.main_window.QTimer", QueuedTimer)
    window.output_text.setPlainText("synthetic application quit output")
    window.send_comfyui_button.click()
    wait_until(app, service.started.is_set)
    worker = window._send_worker
    assert worker is not None

    application.quit()
    application.quit()
    application.quit()

    assert application.intercepted == [True, True, True]
    assert worker.isRunning() is True
    assert worker.isInterruptionRequested() is True
    assert queued_callbacks == []
    service.release.set()
    wait_until(app, lambda: window._send_worker is None)
    wait_until(app, lambda: not isValid(worker))
    assert window._application_quit_pending is False
    assert len(queued_callbacks) == 2

    queued_callbacks.pop(0)()
    assert window.isVisible() is False
    queued_callbacks.pop(0)()

    assert application.quit_calls == 4
    assert application.intercepted == [True, True, True, False]
    assert application.completed_shutdowns == 1
    assert queued_callbacks == []
    assert service.calls == ["synthetic application quit output"]


def test_send_output_is_absent_from_logs_errors_and_worker_repr(
    tmp_path, app, caplog
):
    private_output = "synthetic-sensitive-output-8472"
    service = FakeSendService(blocked=True)
    factory = FakeServiceFactory(service)
    window = make_window(tmp_path, factory)
    try:
        with caplog.at_level(logging.INFO):
            window.output_text.setPlainText(private_output)
            window.send_comfyui_button.click()
            wait_until(app, service.started.is_set)
            worker = window._send_worker
            assert worker is not None
            visible_and_repr = window.status_label.text() + repr(worker) + caplog.text
            assert private_output not in visible_and_repr
            assert "Authorization" not in visible_and_repr
            assert "Bearer" not in visible_and_repr
            service.release.set()
            wait_until(app, lambda: window._send_worker is None)
            assert private_output not in caplog.text
            assert window.output_text.toPlainText() == private_output
    finally:
        close_window(window, app)


def test_copy_save_and_settings_actions_remain_available(
    tmp_path, app, monkeypatch
):
    service = FakeSendService()
    factory = FakeServiceFactory(service)
    window = make_window(tmp_path, factory)
    output = "synthetic copy and save output"
    saved_path = tmp_path / "saved-output.txt"
    settings_calls = []

    class FakeSettingsDialog:
        def __init__(self, *args, **kwargs):
            settings_calls.append((args, kwargs))

        def exec(self):
            return False

    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(saved_path), "Text File (*.txt)"),
    )
    monkeypatch.setattr("app.main_window.SettingsDialog", FakeSettingsDialog)
    try:
        window.output_text.setPlainText(output)
        copy_button = window.findChild(QPushButton, "copy_button")
        save_button = window.findChild(QPushButton, "save_button")
        assert copy_button is not None
        assert save_button is not None
        copy_button.click()
        assert QApplication.clipboard().text() == output
        save_button.click()
        assert saved_path.read_text(encoding="utf-8") == output
        window._open_settings()
        assert len(settings_calls) == 1
        assert service.calls == []
    finally:
        close_window(window, app)
