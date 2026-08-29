from __future__ import annotations

from collections import deque
import logging
import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QObject, QThread
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from app.main_window import MainWindow
from app.settings_dialog import SettingsDialog, bridge_error_message
from core.comfyui_bridge import (
    BridgeStatus,
    ComfyUIBridgeError,
    ComfyUIBridgeService,
    JsonResponse,
    PairedClient,
    PairingSession,
)
from core.comfyui_credentials import ComfyUICredentialStore
from core.config_manager import AppConfig, ConfigManager, DEFAULT_COMFYUI_URL
from core.localization import Localization


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "skills" / "h3-prompt-writing"
REMOTE_URL = "https://comfy.example.com"


def bridge_status() -> BridgeStatus:
    return BridgeStatus(
        version="1.2",
        exact_socket_delivery_available=True,
        persistence_available=True,
        target_registered=False,
        target_session_connected=False,
        max_request_bytes=1_048_576,
        max_text_bytes=262_144,
        ack_timeout_seconds=3.0,
        pairing_expires_seconds=60,
    )


class UiTestProtector:
    def protect(self, data: bytes) -> bytes:
        return b"UI-TEST\0" + data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"UI-TEST\0"):
            raise RuntimeError("unavailable user scope")
        return data.removeprefix(b"UI-TEST\0")[::-1]


class UiFailingProtector:
    def protect(self, data: bytes) -> bytes:
        return b"unreadable"

    def unprotect(self, data: bytes) -> bytes:
        raise RuntimeError("private DPAPI failure")


class ControllableTestThread(QThread):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.release = threading.Event()

    def run(self) -> None:
        self.release.wait(3.0)


class ControllablePairThread(ControllableTestThread):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.requestInterruption()


class FakeTransport:
    def __init__(self, *responses) -> None:
        self.responses = deque(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.popleft()
        return response() if callable(response) else response


class RecordingCredentialStore:
    exists = False

    def __init__(self) -> None:
        self.saved = []

    def has_valid_credential(self, normalized_url: str) -> bool:
        return False

    def save(self, base_url, client_id, client_credential) -> None:
        self.saved.append((base_url, client_id, client_credential))


def bridge_status_payload():
    return {
        "ok": True,
        "status": "ready",
        "name": "MMH3 Prompt Bridge",
        "version": "1.2",
        "security": {},
        "deployment_modes": ["local", "remote_https"],
        "limits": {
            "max_request_bytes": 1_048_576,
            "max_text_bytes": 262_144,
            "ack_timeout_seconds": 3.0,
            "pairing_expires_seconds": 60,
        },
        "exact_socket_delivery_available": True,
        "persistence_available": True,
        "target_registered": False,
        "target_session_connected": False,
    }


class FakeBridgeService:
    def __init__(self, base_url: str, *, paired: bool = False) -> None:
        self.base_url = base_url
        self.paired = paired
        self.test_calls = 0
        self.start_calls = 0
        self.wait_calls = 0
        self.invalidate_calls = 0
        self.replacement_calls = 0
        self.validity_checks = 0
        self.test_error: str | None = None
        self.start_error: str | None = None
        self.pair_error: str | None = None
        self.invalidate_error: str | None = None
        self.test_started = threading.Event()
        self.test_release = threading.Event()
        self.test_release.set()
        self.start_started = threading.Event()
        self.start_release = threading.Event()
        self.start_release.set()
        self.wait_started = threading.Event()
        self.pair_release = threading.Event()

    @property
    def has_credential_file(self) -> bool:
        return self.paired

    def has_valid_credential(self) -> bool:
        self.validity_checks += 1
        return self.paired

    def test_connection(self) -> BridgeStatus:
        self.test_calls += 1
        self.test_started.set()
        self.test_release.wait(2.0)
        if self.test_error is not None:
            raise ComfyUIBridgeError(self.test_error)
        return bridge_status()

    def start_pairing(
        self,
        *,
        cancel_event: threading.Event | None = None,
    ) -> PairingSession:
        self.start_calls += 1
        self.start_started.set()
        self.start_release.wait(2.0)
        if cancel_event is not None and cancel_event.is_set():
            raise ComfyUIBridgeError("pairing_cancelled")
        if self.start_error is not None:
            raise ComfyUIBridgeError(self.start_error)
        return PairingSession(
            base_url=self.base_url,
            pair_id="synthetic-pair-id",
            verification_code="577559",
            verifier=b"synthetic-verifier",
            deadline=time.monotonic() + 60,
        )

    def wait_for_pairing(
        self,
        session: PairingSession,
        cancel_event: threading.Event,
    ) -> PairedClient:
        self.wait_calls += 1
        self.wait_started.set()
        while not self.pair_release.is_set():
            if cancel_event.wait(0.01):
                session.verifier = b""
                session.active = False
                raise ComfyUIBridgeError("pairing_cancelled")
        session.verifier = b""
        session.active = False
        if cancel_event.is_set():
            raise ComfyUIBridgeError("pairing_cancelled")
        if self.pair_error is not None:
            raise ComfyUIBridgeError(self.pair_error)
        self.paired = True
        self.replacement_calls += 1
        return PairedClient(
            client_id="synthetic-client-id",
            client_credential="synthetic-client-credential",
        )

    def invalidate_credentials(self) -> None:
        self.invalidate_calls += 1
        if self.invalidate_error is not None:
            raise ComfyUIBridgeError(self.invalidate_error)
        self.paired = False


class FakeServiceFactory:
    def __init__(self, *, initially_paired: bool = False) -> None:
        self.initially_paired = initially_paired
        self.services: dict[str, FakeBridgeService] = {}
        self.calls: list[str] = []

    def __call__(self, base_url: str) -> FakeBridgeService:
        self.calls.append(base_url)
        if base_url not in self.services:
            self.services[base_url] = FakeBridgeService(
                base_url,
                paired=self.initially_paired and base_url == DEFAULT_COMFYUI_URL,
            )
        return self.services[base_url]


@pytest.fixture(autouse=True)
def disable_runtime_detection(monkeypatch):
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.detect_vulkan_devices",
        lambda self: [],
    )
    monkeypatch.setattr(
        "core.llama_manager.LlamaServerManager.runtime_available",
        lambda self, backend: True,
    )


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


def make_dialog(
    tmp_path,
    factory: FakeServiceFactory,
) -> tuple[SettingsDialog, ConfigManager]:
    manager = ConfigManager(tmp_path)
    dialog = SettingsDialog(
        manager,
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=factory,
    )
    return dialog, manager


def test_settings_default_and_open_close_perform_zero_bridge_network(tmp_path, app):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    assert dialog.comfyui_url.text() == DEFAULT_COMFYUI_URL
    assert dialog.comfyui_pairing_status.text() == "Not paired"
    assert dialog._test_worker is None
    assert dialog._pair_worker is None
    service = factory.services[DEFAULT_COMFYUI_URL]
    assert service.test_calls == 0
    assert service.start_calls == 0
    assert service.wait_calls == 0
    assert service.validity_checks == 1
    dialog.reject()
    app.processEvents()
    assert service.test_calls == 0
    assert service.start_calls == 0


@pytest.mark.parametrize(
    ("locale_id", "test_label", "status_label", "pair_label"),
    [
        ("ja-JP", "接続テスト", "未Pairing", "ComfyUIとPairing"),
        ("en-US", "Test Connection", "Not paired", "Pair with ComfyUI"),
        ("zh-CN", "测试连接", "未配对", "与ComfyUI配对"),
    ],
)
def test_comfyui_settings_controls_are_localized_without_network(
    tmp_path,
    app,
    locale_id,
    test_label,
    status_label,
    pair_label,
):
    manager = ConfigManager(tmp_path / locale_id)
    manager.save(AppConfig(ui_locale=locale_id))
    factory = FakeServiceFactory()
    dialog = SettingsDialog(
        manager,
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", locale_id),
        bridge_service_factory=factory,
    )
    try:
        assert dialog.comfyui_test_button.text() == test_label
        assert dialog.comfyui_pairing_status.text() == status_label
        assert dialog.comfyui_pair_button.text() == pair_label
        service = factory.services[DEFAULT_COMFYUI_URL]
        assert service.test_calls == 0
        assert service.start_calls == 0
    finally:
        dialog.close()
        app.processEvents()


def test_application_window_startup_creates_no_comfyui_worker_or_network(
    tmp_path, app, monkeypatch
):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("ComfyUI work must not start during application startup")

    monkeypatch.setattr("core.comfyui_bridge.ComfyUIBridgeService.test_connection", forbidden)
    monkeypatch.setattr("core.comfyui_bridge.ComfyUIBridgeService.start_pairing", forbidden)
    monkeypatch.setattr("app.workers.ComfyUITestThread.start", forbidden)
    monkeypatch.setattr("app.workers.ComfyUIPairThread.start", forbidden)
    window = MainWindow(
        project_root=PROJECT_ROOT,
        config_manager=ConfigManager(tmp_path),
        server_url="http://127.0.0.1:1",
        dev_skill_path=SKILL_FIXTURE,
    )
    try:
        app.processEvents()
        assert calls == []
    finally:
        window.close()
        app.processEvents()


def test_local_paired_state_is_displayed_without_network(tmp_path, app):
    factory = FakeServiceFactory(initially_paired=True)
    dialog, _manager = make_dialog(tmp_path, factory)
    try:
        service = factory.services[DEFAULT_COMFYUI_URL]
        assert dialog.comfyui_pairing_status.text() == "Paired"
        assert dialog.comfyui_pair_button.text() == "Pair Again"
        assert service.validity_checks == 1
        assert service.test_calls == 0
        assert service.start_calls == 0
    finally:
        dialog.close()
        app.processEvents()


def test_actual_valid_current_url_credential_displays_paired(tmp_path, app):
    store = ComfyUICredentialStore(tmp_path, protector=UiTestProtector())
    store.save(DEFAULT_COMFYUI_URL, "synthetic-client", "synthetic-secret")
    dialog = SettingsDialog(
        ConfigManager(tmp_path),
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=lambda url: ComfyUIBridgeService(
            url,
            credential_store=store,
            transport=FakeTransport(),
        ),
    )
    try:
        assert dialog.comfyui_pairing_status.text() == "Paired"
        assert dialog.comfyui_pair_button.text() == "Pair Again"
    finally:
        dialog.close()
        app.processEvents()


def test_actual_corrupt_credential_displays_not_paired(tmp_path, app):
    store = ComfyUICredentialStore(tmp_path, protector=UiTestProtector())
    tmp_path.mkdir(exist_ok=True)
    store.path.write_bytes(b"corrupt-local-credential")
    dialog = SettingsDialog(
        ConfigManager(tmp_path),
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=lambda url: ComfyUIBridgeService(
            url,
            credential_store=store,
            transport=FakeTransport(),
        ),
    )
    try:
        assert dialog.comfyui_pairing_status.text() == "Not paired"
        assert dialog.comfyui_pair_button.text() == "Pair with ComfyUI"
    finally:
        dialog.close()
        app.processEvents()


def test_actual_credential_url_mismatch_displays_not_paired(tmp_path, app):
    manager = ConfigManager(tmp_path)
    manager.save(AppConfig(comfyui_url=REMOTE_URL))
    store = ComfyUICredentialStore(tmp_path, protector=UiTestProtector())
    store.save(DEFAULT_COMFYUI_URL, "synthetic-client", "synthetic-secret")
    dialog = SettingsDialog(
        manager,
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=lambda url: ComfyUIBridgeService(
            url,
            credential_store=store,
            transport=FakeTransport(),
        ),
    )
    try:
        assert dialog.comfyui_pairing_status.text() == "Not paired"
        assert dialog.comfyui_pair_button.text() == "Pair with ComfyUI"
    finally:
        dialog.close()
        app.processEvents()


def test_actual_dpapi_failure_displays_not_paired_without_raw_error(tmp_path, app):
    writer = ComfyUICredentialStore(tmp_path, protector=UiTestProtector())
    writer.save(DEFAULT_COMFYUI_URL, "synthetic-client", "synthetic-secret")
    unreadable = ComfyUICredentialStore(tmp_path, protector=UiFailingProtector())
    dialog = SettingsDialog(
        ConfigManager(tmp_path),
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=lambda url: ComfyUIBridgeService(
            url,
            credential_store=unreadable,
            transport=FakeTransport(),
        ),
    )
    try:
        assert dialog.comfyui_pairing_status.text() == "Not paired"
        assert "private DPAPI failure" not in dialog.comfyui_pairing_status.text()
    finally:
        dialog.close()
        app.processEvents()


def test_actual_paired_state_check_uses_no_http_dns_or_worker(
    tmp_path, app, monkeypatch
):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("paired-state rendering must stay local")

    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("app.workers.ComfyUITestThread.start", forbidden)
    monkeypatch.setattr("app.workers.ComfyUIPairThread.start", forbidden)
    store = ComfyUICredentialStore(tmp_path, protector=UiTestProtector())
    transport = FakeTransport()
    dialog = SettingsDialog(
        ConfigManager(tmp_path),
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=lambda url: ComfyUIBridgeService(
            url,
            credential_store=store,
            transport=transport,
        ),
    )
    try:
        assert dialog.comfyui_pairing_status.text() == "Not paired"
        assert calls == []
        assert transport.calls == []
        assert dialog._test_worker is None
        assert dialog._pair_worker is None
    finally:
        dialog.close()
        app.processEvents()


def test_valid_url_is_saved_normalized_and_changed_url_invalidates_old(tmp_path, app):
    factory = FakeServiceFactory(initially_paired=True)
    dialog, manager = make_dialog(tmp_path, factory)
    old_service = factory.services[DEFAULT_COMFYUI_URL]
    dialog.comfyui_url.setText("https://COMFY.example.com:443/")
    assert dialog.comfyui_pairing_status.text() == "Not paired"
    dialog.accept()
    app.processEvents()
    assert manager.load().comfyui_url == REMOTE_URL
    assert old_service.invalidate_calls == 1
    assert old_service.paired is False


def test_equivalent_normalized_url_does_not_invalidate(tmp_path, app):
    factory = FakeServiceFactory(initially_paired=True)
    dialog, manager = make_dialog(tmp_path, factory)
    old_service = factory.services[DEFAULT_COMFYUI_URL]
    dialog.comfyui_url.setText("HTTP://LOCALHOST:8188/")
    dialog.accept()
    app.processEvents()
    assert manager.load().comfyui_url == DEFAULT_COMFYUI_URL
    assert old_service.invalidate_calls == 0


def test_pairing_a_changed_url_invalidates_old_and_keeps_config_binding(
    tmp_path, app
):
    factory = FakeServiceFactory(initially_paired=True)
    dialog, manager = make_dialog(tmp_path, factory)
    old_service = factory.services[DEFAULT_COMFYUI_URL]
    dialog.comfyui_url.setText("https://COMFY.example.com:443/")
    dialog.comfyui_pair_button.click()
    wait_until(app, lambda: dialog._pairing_dialog is not None)
    remote_service = factory.services[REMOTE_URL]
    assert old_service.invalidate_calls == 1
    assert manager.load().comfyui_url == REMOTE_URL
    remote_service.pair_release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    dialog.reject()
    app.processEvents()
    assert manager.load().comfyui_url == REMOTE_URL
    assert remote_service.paired is True


def test_invalid_url_is_rejected_without_save_or_network(tmp_path, app, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: warnings.append((args[1], args[2])) or QMessageBox.Ok,
    )
    factory = FakeServiceFactory()
    dialog, manager = make_dialog(tmp_path, factory)
    dialog.comfyui_url.setText("http://remote.example.com:8188")
    dialog.accept()
    app.processEvents()
    assert manager.load().comfyui_url == DEFAULT_COMFYUI_URL
    assert warnings == [
        (
            "ComfyUI URL",
            bridge_error_message("remote_http_not_allowed"),
        )
    ]
    assert factory.services[DEFAULT_COMFYUI_URL].test_calls == 0
    dialog.close()


def test_invalidation_failure_is_safe_and_prevents_url_save(tmp_path, app, monkeypatch):
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: warnings.append(args[2]) or QMessageBox.Ok,
    )
    factory = FakeServiceFactory(initially_paired=True)
    dialog, manager = make_dialog(tmp_path, factory)
    old_service = factory.services[DEFAULT_COMFYUI_URL]
    old_service.invalidate_error = "credential_unavailable"
    dialog.comfyui_url.setText(REMOTE_URL)
    dialog.accept()
    assert manager.load().comfyui_url == DEFAULT_COMFYUI_URL
    assert old_service.invalidate_calls == 1
    assert dialog.comfyui_pairing_status.text() == "Not paired"
    assert warnings == [bridge_error_message("credential_unavailable")]
    dialog.close()
    app.processEvents()


def test_connection_runs_once_off_gui_thread_and_reports_version(tmp_path, app):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    service.test_release.clear()
    dialog.comfyui_test_button.click()
    wait_until(app, lambda: service.test_started.is_set())
    assert dialog._test_worker is not None and dialog._test_worker.isRunning()
    dialog.theme.setCurrentIndex(dialog.theme.findData("dark"))
    app.processEvents()
    assert dialog.theme.currentData() == "dark"
    service.test_release.set()
    wait_until(app, lambda: dialog._test_worker is None)
    assert service.test_calls == 1
    assert dialog.comfyui_feedback.text() == "MMH3 Prompt Bridge v1.2 detected."
    dialog.close()
    app.processEvents()


def test_connection_failure_uses_safe_code_mapping_without_retry(
    tmp_path, app, monkeypatch
):
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: warnings.append(args[2]) or QMessageBox.Ok,
    )
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    service.test_error = "bridge_unavailable"
    dialog.comfyui_test_button.click()
    wait_until(app, lambda: dialog._test_worker is None)
    assert service.test_calls == 1
    assert warnings == [bridge_error_message("bridge_unavailable")]
    assert DEFAULT_COMFYUI_URL not in warnings[0]
    dialog.close()
    app.processEvents()


def test_pairing_shows_code_promptly_then_updates_local_state(tmp_path, app):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    dialog.comfyui_pair_button.click()
    wait_until(app, lambda: dialog._pairing_dialog is not None)
    assert dialog._pairing_dialog.code_label.text() == "577 559"
    assert service.start_calls == 1
    assert service.wait_started.is_set()
    service.pair_release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    assert service.wait_calls == 1
    assert service.replacement_calls == 1
    assert dialog.comfyui_pairing_status.text() == "Paired"
    assert dialog.comfyui_pair_button.text() == "Pair Again"
    assert dialog.comfyui_feedback.text() == "Pairing completed."
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("code", "expected_message"),
    [
        ("pairing_rejected", "Pairing was rejected in ComfyUI."),
        ("pairing_expired", "Pairing timed out. Please try again."),
        (
            "no_browser_session",
            "Open or reload ComfyUI in your browser and try again.",
        ),
    ],
)
def test_pairing_failure_mapping_does_not_false_report_paired(
    tmp_path, app, monkeypatch, code, expected_message
):
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: warnings.append(args[2]) or QMessageBox.Ok,
    )
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    service.pair_error = code
    dialog.comfyui_pair_button.click()
    wait_until(app, lambda: dialog._pairing_dialog is not None)
    service.pair_release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    assert dialog.comfyui_pairing_status.text() == "Not paired"
    assert warnings == [expected_message]
    dialog.close()
    app.processEvents()


def test_pair_cancel_stops_wait_without_replacement_or_more_polling(tmp_path, app):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    dialog.comfyui_pair_button.click()
    wait_until(app, lambda: dialog._pairing_dialog is not None)
    dialog._pairing_dialog.cancel_button.click()
    wait_until(app, lambda: dialog._pair_worker is None)
    assert service.wait_calls == 1
    assert service.replacement_calls == 0
    assert dialog.comfyui_pairing_status.text() == "Not paired"
    assert dialog.comfyui_feedback.text() == "Pairing was cancelled."
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize("outcome", ["pairing_rejected", "pairing_cancelled"])
def test_pair_again_failure_or_cancel_preserves_old_pairing(
    tmp_path, app, monkeypatch, outcome
):
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    factory = FakeServiceFactory(initially_paired=True)
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    assert dialog.comfyui_pair_button.text() == "Pair Again"
    dialog.comfyui_pair_button.click()
    wait_until(app, lambda: dialog._pairing_dialog is not None)
    if outcome == "pairing_cancelled":
        dialog._pairing_dialog.cancel_button.click()
    else:
        service.pair_error = outcome
        service.pair_release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    assert service.paired is True
    assert service.replacement_calls == 0
    assert dialog.comfyui_pairing_status.text() == "Paired"
    dialog.close()
    app.processEvents()


def test_pair_again_success_atomically_replaces_old_pairing(tmp_path, app):
    factory = FakeServiceFactory(initially_paired=True)
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    dialog.comfyui_pair_button.click()
    wait_until(app, lambda: dialog._pairing_dialog is not None)
    assert service.paired is True
    service.pair_release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    assert service.paired is True
    assert service.replacement_calls == 1
    assert dialog.comfyui_pairing_status.text() == "Paired"
    dialog.close()
    app.processEvents()


def test_close_settings_during_pairing_cancels_and_waits_for_worker(tmp_path, app):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    dialog.show()
    service = factory.services[DEFAULT_COMFYUI_URL]
    dialog.comfyui_pair_button.click()
    wait_until(app, lambda: dialog._pairing_dialog is not None)
    worker = dialog._pair_worker
    dialog.reject()
    assert worker is not None
    wait_until(app, lambda: dialog._pair_worker is None)
    assert isValid(worker) is False
    assert service.replacement_calls == 0
    assert dialog.isVisible() is False


def test_close_settings_during_connection_waits_for_short_worker(tmp_path, app):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    dialog.show()
    service = factory.services[DEFAULT_COMFYUI_URL]
    service.test_release.clear()
    dialog.comfyui_test_button.click()
    wait_until(app, lambda: service.test_started.is_set())
    worker = dialog._test_worker
    dialog.reject()
    assert worker is not None
    assert dialog.isVisible() is True
    service.test_release.set()
    wait_until(app, lambda: dialog._test_worker is None)
    assert isValid(worker) is False
    assert dialog.isVisible() is False


def test_settings_close_retains_live_pair_worker_until_single_cleanup(tmp_path, app):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    dialog.show()
    worker = ControllablePairThread(parent=dialog)
    cleanup_calls = []
    dialog._pairing_url = DEFAULT_COMFYUI_URL
    dialog._pair_succeeded = False
    dialog._pair_error_code = None
    dialog._pair_worker = worker
    worker.finished.connect(dialog._pairing_finished)
    worker.finished.connect(lambda: cleanup_calls.append(True))
    worker.finished.connect(worker.deleteLater)
    worker.start()
    wait_until(app, worker.isRunning)

    dialog.reject()
    dialog.reject()

    assert dialog.isVisible() is True
    assert dialog._pair_worker is worker
    assert worker.parent() is dialog
    assert worker.isRunning() is True
    assert worker.cancel_calls == 1
    worker.release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    wait_until(app, lambda: not isValid(worker))
    assert cleanup_calls == [True]
    assert dialog.isVisible() is False


def test_application_quit_is_deferred_while_test_worker_is_alive(
    tmp_path, app, monkeypatch
):
    scheduled_quits = []

    class CapturingTimer:
        @staticmethod
        def singleShot(delay, callback):
            scheduled_quits.append((delay, callback))

    monkeypatch.setattr("app.settings_dialog.QTimer", CapturingTimer)
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    dialog.show()
    worker = ControllableTestThread(parent=dialog)
    cleanup_calls = []
    dialog._test_worker = worker
    worker.finished.connect(dialog._test_connection_finished)
    worker.finished.connect(lambda: cleanup_calls.append(True))
    worker.finished.connect(worker.deleteLater)
    worker.start()
    wait_until(app, worker.isRunning)

    QApplication.sendEvent(app, QEvent(QEvent.Quit))

    assert dialog._application_quit_pending is True
    assert dialog.isVisible() is True
    assert dialog._test_worker is worker
    assert worker.parent() is dialog
    assert worker.isRunning() is True
    assert worker.isInterruptionRequested() is True
    assert scheduled_quits == []
    worker.release.set()
    wait_until(app, lambda: dialog._test_worker is None)
    wait_until(app, lambda: not isValid(worker))
    assert cleanup_calls == [True]
    assert dialog.isVisible() is False
    assert len(scheduled_quits) == 1
    assert scheduled_quits[0][0] == 0


def test_deferred_application_quit_executes_once_without_recursion(
    tmp_path, app, monkeypatch
):
    queued_callbacks = []
    scheduled_count = 0

    class QueuedTimer:
        @staticmethod
        def singleShot(delay, callback):
            nonlocal scheduled_count
            assert delay == 0
            scheduled_count += 1
            queued_callbacks.append(callback)

    class QuitProxy(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.dialog = None
            self.quit_calls = 0
            self.intercepted = []
            self.completed_shutdowns = 0

        def quit(self) -> None:
            self.quit_calls += 1
            intercepted = self.dialog.eventFilter(self, QEvent(QEvent.Quit))
            self.intercepted.append(intercepted)
            if not intercepted:
                self.completed_shutdowns += 1

    monkeypatch.setattr("app.settings_dialog.QTimer", QueuedTimer)
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    dialog.show()
    application = QuitProxy()
    application.dialog = dialog
    dialog._application = application
    worker = ControllableTestThread(parent=dialog)
    cleanup_calls = []
    dialog._test_worker = worker
    worker.finished.connect(dialog._test_connection_finished)
    worker.finished.connect(lambda: cleanup_calls.append(True))
    worker.finished.connect(worker.deleteLater)
    worker.start()
    wait_until(app, worker.isRunning)

    application.quit()
    application.quit()
    application.quit()

    assert application.intercepted == [True, True, True]
    assert application.completed_shutdowns == 0
    assert dialog._application_quit_pending is True
    assert dialog._test_worker is worker
    assert worker.isRunning() is True
    assert worker.isInterruptionRequested() is True
    assert scheduled_count == 0
    assert queued_callbacks == []
    worker.release.set()
    wait_until(app, lambda: dialog._test_worker is None)
    wait_until(app, lambda: not isValid(worker))
    assert cleanup_calls == [True]
    assert dialog.isVisible() is False
    assert dialog._application_quit_pending is False
    assert scheduled_count == 1
    assert len(queued_callbacks) == 1

    queued_callbacks.pop(0)()

    assert application.quit_calls == 4
    assert application.intercepted == [True, True, True, False]
    assert application.completed_shutdowns == 1
    assert scheduled_count == 1
    assert queued_callbacks == []


def test_close_during_pair_start_never_shows_code_or_polls_after_cancel(
    tmp_path, app
):
    pair_start_in_flight = threading.Event()
    pair_start_release = threading.Event()

    class TrackingBridgeService(ComfyUIBridgeService):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.wait_for_pairing_calls = 0

        def wait_for_pairing(self, session, cancel_event, **kwargs):
            self.wait_for_pairing_calls += 1
            return super().wait_for_pairing(session, cancel_event, **kwargs)

    def pair_start_response():
        pair_start_in_flight.set()
        pair_start_release.wait(3.0)
        return JsonResponse(
            201,
            {
                "ok": True,
                "status": "pending",
                "pair_id": "synthetic-pair-id",
                "verification_code": "577559",
                "expires_in": 60,
            },
        )

    transport = FakeTransport(
        JsonResponse(200, bridge_status_payload()),
        pair_start_response,
    )
    store = RecordingCredentialStore()
    service = TrackingBridgeService(
        DEFAULT_COMFYUI_URL,
        credential_store=store,
        transport=transport,
    )
    dialog = SettingsDialog(
        ConfigManager(tmp_path),
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=lambda _url: service,
    )
    dialog.show()
    dialog.comfyui_pair_button.click()
    wait_until(app, pair_start_in_flight.is_set)
    worker = dialog._pair_worker
    cleanup_calls = []
    assert worker is not None
    worker.finished.connect(lambda: cleanup_calls.append(True))

    dialog.reject()

    assert dialog.isVisible() is True
    assert dialog._pair_worker is worker
    assert worker.parent() is dialog
    assert worker.isRunning() is True
    assert worker.cancel_event.is_set() is True
    assert worker.isInterruptionRequested() is True
    assert dialog._pairing_dialog is None
    pair_start_release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    wait_until(app, lambda: not isValid(worker))
    assert cleanup_calls == [True]
    assert dialog._pairing_dialog is None
    assert "577559" not in dialog.comfyui_feedback.text()
    assert "577 559" not in dialog.comfyui_feedback.text()
    assert dialog.comfyui_feedback.text() != "Pairing completed."
    assert len(transport.calls) == 2
    assert transport.calls[0][1].endswith("/mmh3-bridge/v1/status")
    assert transport.calls[1][1].endswith("/mmh3-bridge/v1/pair/start")
    assert all(not call[1].endswith("/mmh3-bridge/v1/pair/complete") for call in transport.calls)
    assert service.wait_for_pairing_calls == 0
    assert store.saved == []
    assert dialog.isVisible() is False


def test_in_flight_paired_response_shutdown_never_saves_credential(tmp_path, app):
    response_in_flight = threading.Event()
    response_release = threading.Event()

    def paired_response():
        response_in_flight.set()
        response_release.wait(3.0)
        return JsonResponse(
            200,
            {
                "ok": True,
                "status": "paired",
                "pair_id": "synthetic-pair-id",
                "client_id": "synthetic-client-id",
                "client_credential": "synthetic-client-credential",
            },
        )

    transport = FakeTransport(
        JsonResponse(200, bridge_status_payload()),
        JsonResponse(
            201,
            {
                "ok": True,
                "status": "pending",
                "pair_id": "synthetic-pair-id",
                "verification_code": "577559",
                "expires_in": 60,
            },
        ),
        paired_response,
    )
    store = RecordingCredentialStore()
    service = ComfyUIBridgeService(
        DEFAULT_COMFYUI_URL,
        credential_store=store,
        transport=transport,
    )
    dialog = SettingsDialog(
        ConfigManager(tmp_path),
        PROJECT_ROOT,
        localization=Localization(PROJECT_ROOT / "locales", "en-US"),
        bridge_service_factory=lambda _url: service,
    )
    dialog.show()
    dialog.comfyui_pair_button.click()
    wait_until(app, response_in_flight.is_set)
    worker = dialog._pair_worker
    assert worker is not None

    dialog.reject()

    assert dialog.isVisible() is True
    assert dialog._pair_worker is worker
    assert worker.cancel_event.is_set() is True
    assert store.saved == []
    response_release.set()
    wait_until(app, lambda: dialog._pair_worker is None)
    wait_until(app, lambda: not isValid(worker))
    assert store.saved == []
    assert len(transport.calls) == 3
    assert dialog.isVisible() is False


def test_pairing_ui_logs_and_repr_do_not_expose_non_ui_secrets(tmp_path, app, caplog):
    factory = FakeServiceFactory()
    dialog, _manager = make_dialog(tmp_path, factory)
    service = factory.services[DEFAULT_COMFYUI_URL]
    with caplog.at_level(logging.INFO):
        dialog.comfyui_pair_button.click()
        wait_until(app, lambda: dialog._pairing_dialog is not None)
        visible_text = " ".join(
            (
                dialog.comfyui_feedback.text(),
                dialog._pairing_dialog.code_label.text(),
                dialog._pairing_dialog.waiting_label.text(),
                dialog._pairing_dialog.windowTitle(),
            )
        )
        combined = visible_text + caplog.text + repr(dialog) + repr(dialog._pair_worker)
        assert "synthetic-pair-id" not in combined
        assert "synthetic-verifier" not in combined
        assert "synthetic-client-id" not in combined
        assert "synthetic-client-credential" not in combined
        assert "Authorization" not in combined
        service.pair_release.set()
        wait_until(app, lambda: dialog._pair_worker is None)
    dialog.close()
    app.processEvents()
