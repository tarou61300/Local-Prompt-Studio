from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.config_manager import AppConfig
from core.llama_manager import DEFAULT_GENERATION_TIMEOUT_SECONDS, LlamaServerManager
from core.model_manager import validate_model
from core.prompt_engine import PromptEngine, PromptSettings


class GenerationThread(QThread):
    status_changed = Signal(str)
    result_ready = Signal(str)
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
            self.status_changed.emit("MiniMax H3 Skillを準備しています…")
            payload = self.engine.request_payload(self.request_text, self.settings)
            if not self.mock_mode:
                self.status_changed.emit("選択したバックエンドでモデルを準備しています…")
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
                self.status_changed.emit("Context Sizeと出力予算を確認しています…")
                self.server.preflight_context(payload, self.config.context_size)
            self.status_changed.emit("プロンプトを生成しています…")
            output = self.server.generate(payload, timeout=DEFAULT_GENERATION_TIMEOUT_SECONDS)
            if not self.isInterruptionRequested():
                self.result_ready.emit(output)
        except Exception as exc:  # GUI boundary: never show a Python traceback to the user.
            if self.isInterruptionRequested():
                self.error_occurred.emit("生成はユーザーによってキャンセルされました。")
            else:
                self.error_occurred.emit(str(exc) or "生成中に不明なエラーが発生しました。")
