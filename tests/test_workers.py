from __future__ import annotations

from app.workers import GenerationThread
from core.config_manager import AppConfig
from core.inference_backends import BACKEND_VULKAN, GPU_LAYERS_AUTO
from core.prompt_engine import PromptSettings


def test_real_generation_resubmits_backend_signature_when_server_already_exists(tmp_path):
    model = tmp_path / "qwen3-4b-q4_k_m.gguf"
    model.write_bytes(b"GGUF")

    class FakeEngine:
        def request_payload(self, request_text, settings):
            return {"messages": [{"role": "user", "content": request_text}], "max_tokens": 10}

    class FakeServer:
        base_url = "http://127.0.0.1:1234"

        def __init__(self):
            self.starts = []

        def start(self, model_path, **settings):
            self.starts.append((model_path, settings))
            return self.base_url

        def preflight_context(self, payload, context_size):
            return 1, 10

        def generate(self, payload, timeout):
            return "generated"

    server = FakeServer()
    config = AppConfig(
        model_path=str(model),
        inference_backend=BACKEND_VULKAN,
        backend_device="Vulkan0",
        gpu_layers=GPU_LAYERS_AUTO,
        context_size=8192,
    )
    worker = GenerationThread(
        engine=FakeEngine(),
        server=server,
        config=config,
        request_text="test",
        settings=PromptSettings(),
        mock_mode=False,
    )
    worker.run()
    assert len(server.starts) == 1
    _, launch = server.starts[0]
    assert launch["backend"] == BACKEND_VULKAN
    assert launch["backend_device"] == "Vulkan0"
    assert launch["gpu_layers"] == GPU_LAYERS_AUTO
