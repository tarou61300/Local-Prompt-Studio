from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

from core.comfyui_bridge import (
    BridgeStatus,
    ComfyUIBridgeError,
    ComfyUIBridgeService,
)
from core.config_manager import AppConfig
from core.llama_manager import DEFAULT_GENERATION_TIMEOUT_SECONDS, LlamaServerManager
from core.model_manager import validate_model
from core.prompt_engine import PromptEngine, PromptSettings


class ComfyUITestThread(QThread):
    """Run one explicit Bridge status request without blocking the GUI thread."""

    result_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, service: ComfyUIBridgeService, parent=None) -> None:
        super().__init__(parent)
        self.service = service

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        try:
            status: BridgeStatus = self.service.test_connection()
        except ComfyUIBridgeError as exc:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(exc.code)
        except Exception:
            if not self.isInterruptionRequested():
                self.error_occurred.emit("bridge_unavailable")
        else:
            if not self.isInterruptionRequested():
                self.result_ready.emit(status)


class ComfyUIPairThread(QThread):
    """Run one verifier/challenge pairing flow and persist only through the service."""

    verification_code_ready = Signal(str)
    pairing_succeeded = Signal()
    error_occurred = Signal(str)

    def __init__(self, service: ComfyUIBridgeService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.requestInterruption()

    def run(self) -> None:
        try:
            session = self.service.start_pairing(cancel_event=self.cancel_event)
            if self.cancel_event.is_set() or self.isInterruptionRequested():
                raise ComfyUIBridgeError("pairing_cancelled")
            self.verification_code_ready.emit(session.verification_code)
            self.service.wait_for_pairing(session, self.cancel_event)
            if self.cancel_event.is_set() or self.isInterruptionRequested():
                raise ComfyUIBridgeError("pairing_cancelled")
        except ComfyUIBridgeError as exc:
            self.error_occurred.emit(exc.code)
        except Exception:
            self.error_occurred.emit("bridge_unavailable")
        else:
            self.pairing_succeeded.emit()


class ComfyUISendThread(QThread):
    """Send one immutable output snapshot without exposing its contents in signals."""

    send_succeeded = Signal()
    error_occurred = Signal(str)

    def __init__(
        self,
        service: ComfyUIBridgeService,
        text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._text = text

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        try:
            self.service.send(self._text)
        except ComfyUIBridgeError as exc:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(exc.code)
        except Exception:
            if not self.isInterruptionRequested():
                self.error_occurred.emit("bridge_unavailable")
        else:
            if not self.isInterruptionRequested():
                self.send_succeeded.emit()


class GenerationThread(QThread):
    status_changed = Signal(str)
    result_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(
        self,
        *,
        engine: PromptEngine,
        server: LlamaServerManager,
        config: AppConfig,
        request_text: str,
        settings: PromptSettings,
        mock_mode: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.server = server
        self.config = config
        self.request_text = request_text
        self.settings = settings
        self.mock_mode = mock_mode

    def run(self) -> None:
        try:
            self.status_changed.emit("status.preparing_profile")
            payload = self.engine.request_payload(self.request_text, self.settings)
            if not self.mock_mode:
                self.status_changed.emit("status.preparing_model")
                model = validate_model(self.config.model_path)
                # Always submit the desired launch signature. The manager reuses an
                # identical owned server and restarts only when backend/model/context
                # settings changed.
                self.server.start(
                    model.path,
                    backend=self.config.inference_backend,
                    backend_device=self.config.backend_device,
                    context_size=self.config.context_size,
                    cpu_threads=self.config.cpu_threads,
                    gpu_layers=self.config.gpu_layers,
                )
            if not self.mock_mode:
                self.status_changed.emit("status.preflight")
                self.server.preflight_context(payload, self.config.context_size)
            self.status_changed.emit("status.generating")
            output = self.server.generate(payload, timeout=DEFAULT_GENERATION_TIMEOUT_SECONDS)
            if not self.isInterruptionRequested():
                rendered = self.engine.finalize_output(
                    self.request_text,
                    self.settings,
                    output,
                )
                self.result_ready.emit(rendered)
                if rendered.warnings:
                    self.status_changed.emit(rendered.warnings[0])
        except Exception as exc:  # GUI boundary: never show a Python traceback to the user.
            if self.isInterruptionRequested():
                self.error_occurred.emit("生成はユーザーによってキャンセルされました。")
            else:
                self.error_occurred.emit(str(exc) or "生成中に不明なエラーが発生しました。")
