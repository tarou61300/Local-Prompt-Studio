from __future__ import annotations

import logging
from pathlib import Path
import threading
from typing import Any

from PySide6.QtCore import QThread, Signal

from core.chat_engine import ChatEngine
from core.comfyui_bridge import (
    BridgeStatus,
    ComfyUIBridgeError,
    ComfyUIBridgeService,
)
from core.config_manager import AppConfig
from core.llama_manager import (
    DEFAULT_GENERATION_TIMEOUT_SECONDS,
    LlamaConnectionError,
    LlamaContextError,
    LlamaError,
    LlamaServerManager,
)
from core.model_manager import validate_model
from core.prompt_engine import PromptEngine, PromptSettings
from core.prompt_translation import (
    PromptTranslationService,
    TRANSLATION_EMPTY_RESPONSE,
    TRANSLATION_STRUCTURE_NOT_PRESERVED,
)
from core.renderers import (
    TransformationError,
    serialize_transformation_error,
)

_LOGGER = logging.getLogger(__name__)
GENERATION_CANCELLED = "GENERATION_CANCELLED"
GENERATION_UNKNOWN_ERROR = "GENERATION_UNKNOWN_ERROR"


def generation_error_message(exc: Exception) -> str:
    if not isinstance(exc, TransformationError):
        return str(exc) or GENERATION_UNKNOWN_ERROR
    details = getattr(exc, "literal_diagnostics", None)
    if details is not None:
        items = "; ".join(
            (
                f"{item.source_role}/{item.detection_type}/"
                f"{item.character_count}/{item.short_hash}"
            )
            for item in details.missing
        )
        _LOGGER.warning(
            "Literal Content validation failed "
            "(detected=%d, missing=%d, items=%s)",
            details.detected_count,
            details.missing_count,
            items,
        )
    return serialize_transformation_error(exc)


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
                    status_callback=self.status_changed.emit,
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
                self.error_occurred.emit(GENERATION_CANCELLED)
            else:
                self.error_occurred.emit(generation_error_message(exc))


class TranslationThread(QThread):
    """Run one faithful prompt translation through the shared llama-server."""

    status_changed = Signal(str)
    result_ready = Signal(int, str, str)
    error_occurred = Signal(int, str)

    def __init__(
        self,
        *,
        service: PromptTranslationService,
        server: LlamaServerManager,
        config: AppConfig,
        source_text: str,
        direction: str,
        protected_terms: tuple[str, ...],
        structure_protection: bool,
        revision: int,
        mock_mode: bool,
        source_language_code: str = "en",
        ui_locale_id: str = "ja-JP",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.server = server
        self.config = config
        self.source_text = source_text
        self.direction = direction
        self.protected_terms = tuple(protected_terms)
        self.structure_protection = structure_protection
        self.revision = revision
        self.mock_mode = mock_mode
        self.source_language_code = source_language_code
        self.ui_locale_id = ui_locale_id

    def run(self) -> None:
        try:
            request = self.service.request_payload(
                self.source_text,
                self.direction,
                source_language_code=self.source_language_code,
                ui_locale_id=self.ui_locale_id,
                protected_terms=self.protected_terms,
                structure_protection=self.structure_protection,
            )
            if not self.mock_mode:
                self.status_changed.emit("translation.status.preparing_model")
                model = validate_model(self.config.model_path)
                self.server.start(
                    model.path,
                    backend=self.config.inference_backend,
                    backend_device=self.config.backend_device,
                    context_size=self.config.context_size,
                    cpu_threads=self.config.cpu_threads,
                    gpu_layers=self.config.gpu_layers,
                    status_callback=self.status_changed.emit,
                )
                self.status_changed.emit("translation.status.preflight")
                self.server.preflight_context(request.payload, self.config.context_size)
            self.status_changed.emit("translation.status.translating")
            generated = self.server.generate(
                request.payload,
                timeout=DEFAULT_GENERATION_TIMEOUT_SECONDS,
            )
            if not self.isInterruptionRequested():
                translated = self.service.finalize_response(
                    generated,
                    request,
                    protected_terms=self.protected_terms,
                )
                self.result_ready.emit(self.revision, self.direction, translated)
        except Exception as exc:
            if self.isInterruptionRequested():
                code = "TRANSLATION_CANCELLED"
            elif str(exc) in {
                TRANSLATION_EMPTY_RESPONSE,
                TRANSLATION_STRUCTURE_NOT_PRESERVED,
            }:
                code = str(exc)
            else:
                code = "TRANSLATION_FAILED"
            self.error_occurred.emit(self.revision, code)


class ChatThread(QThread):
    """Run one ordinary chat turn through the shared local llama-server."""

    status_changed = Signal(str)
    result_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        *,
        engine: ChatEngine,
        server: LlamaServerManager,
        config: AppConfig,
        conversation: list[dict[str, Any]],
        mock_mode: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.server = server
        self.config = config
        self.conversation = [dict(message) for message in conversation]
        self.mock_mode = mock_mode

    def run(self) -> None:
        requires_multimodal = any(
            message.get("image") is not None for message in self.conversation
        )
        chat_model_path = self.config.effective_chat_model_path()
        mmproj_path = (
            self.config.mmproj_for_model(chat_model_path)
            if requires_multimodal
            else ""
        )
        try:
            payload = self.engine.request_payload(self.conversation)
            if not self.mock_mode:
                self.status_changed.emit(
                    "chat.status.loading_multimodal"
                    if requires_multimodal
                    else "chat.status.preparing_model"
                )
                try:
                    model = validate_model(chat_model_path)
                    if requires_multimodal and not Path(mmproj_path).is_file():
                        raise LlamaError("CHAT_MMPROJ_LOAD_FAILED")
                    launch_options: dict[str, Any] = {
                        "backend": self.config.inference_backend,
                        "backend_device": self.config.backend_device,
                        "context_size": self.config.context_size,
                        "cpu_threads": self.config.cpu_threads,
                        "gpu_layers": self.config.gpu_layers,
                        "status_callback": self.status_changed.emit,
                    }
                    if requires_multimodal:
                        launch_options["mmproj_path"] = mmproj_path
                    self.server.start(model.path, **launch_options)
                except Exception as exc:
                    if requires_multimodal:
                        self._mark_multimodal_state(
                            chat_model_path, mmproj_path, "load_error"
                        )
                        raise LlamaError("CHAT_MMPROJ_LOAD_FAILED") from exc
                    if not self.config.use_prompt_model_for_chat:
                        raise LlamaError("CHAT_MODEL_LOAD_FAILED") from exc
                    raise
                self.status_changed.emit("chat.status.preflight")
                self.server.preflight_context(payload, self.config.context_size)
            self.status_changed.emit(
                "chat.status.analyzing_image"
                if requires_multimodal
                else "chat.status.generating"
            )
            generated = self.server.generate(
                payload,
                timeout=DEFAULT_GENERATION_TIMEOUT_SECONDS,
            )
            if not self.isInterruptionRequested():
                if requires_multimodal and mmproj_path:
                    self._mark_multimodal_state(
                        chat_model_path, mmproj_path, "available"
                    )
                self.result_ready.emit(self.engine.finalize_response(generated))
        except LlamaContextError:
            if not self.isInterruptionRequested():
                self.error_occurred.emit("CHAT_CONTEXT_OVERFLOW")
        except LlamaConnectionError as exc:
            if not self.isInterruptionRequested():
                if exc.error_type == "exceed_context_size_error":
                    self.error_occurred.emit("CHAT_CONTEXT_OVERFLOW")
                elif requires_multimodal and self._is_image_unsupported(exc):
                    self._mark_multimodal_state(
                        chat_model_path, mmproj_path, "unsupported"
                    )
                    self.error_occurred.emit("CHAT_IMAGE_UNSUPPORTED")
                elif requires_multimodal and self._is_image_decode_error(exc):
                    self.error_occurred.emit("CHAT_IMAGE_DECODE_FAILED")
                elif requires_multimodal:
                    self.error_occurred.emit("CHAT_IMAGE_REQUEST_FAILED")
                else:
                    self.error_occurred.emit(str(exc))
        except Exception as exc:
            if self.isInterruptionRequested():
                self.error_occurred.emit("CHAT_CANCELLED")
            elif str(exc) == "CHAT_MMPROJ_LOAD_FAILED":
                self.error_occurred.emit("CHAT_MMPROJ_LOAD_FAILED")
            elif requires_multimodal:
                self.error_occurred.emit("CHAT_IMAGE_REQUEST_FAILED")
            else:
                self.error_occurred.emit(str(exc) or "CHAT_UNKNOWN_ERROR")

    def _mark_multimodal_state(
        self,
        model_path: str,
        mmproj_path: str,
        state: str,
    ) -> None:
        if not mmproj_path:
            return
        marker = getattr(self.server, "mark_multimodal_state", None)
        if callable(marker):
            marker(model_path, mmproj_path, state)

    @staticmethod
    def _is_image_unsupported(exc: LlamaConnectionError) -> bool:
        detail = f"{exc.error_type or ''} {exc}".lower()
        return "image input is not supported" in detail or (
            "multimodal" in detail and "not supported" in detail
        )

    @staticmethod
    def _is_image_decode_error(exc: LlamaConnectionError) -> bool:
        detail = f"{exc.error_type or ''} {exc}".lower()
        return any(
            marker in detail
            for marker in (
                "failed to load image",
                "invalid image",
                "image decode",
                "decode image",
            )
        )
