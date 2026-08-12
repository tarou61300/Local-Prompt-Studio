from __future__ import annotations

from collections import deque

from app.workers import (
    ComfyUIPairThread,
    ComfyUISendThread,
    ComfyUITestThread,
    GenerationThread,
)
from core.comfyui_bridge import (
    ComfyUIBridgeError,
    ComfyUIBridgeService,
    JsonResponse,
    SendResult,
)
from core.config_manager import AppConfig
from core.inference_backends import BACKEND_VULKAN, GPU_LAYERS_AUTO
from core.prompt_engine import PromptSettings


BASE_URL = "http://127.0.0.1:8188"


class FakeTransport:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.popleft()
        if callable(response):
            return response()
        return response


class RecordingCredentialStore:
    exists = False

    def __init__(self):
        self.saved = []

    def save(self, base_url, client_id, client_credential):
        self.saved.append((base_url, client_id, client_credential))


def status_payload():
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


def test_comfyui_pair_worker_cancel_during_paired_response_never_saves():
    store = RecordingCredentialStore()
    worker_reference = {}

    def paired_after_cancel():
        worker_reference["worker"].cancel()
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
        JsonResponse(200, status_payload()),
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
        paired_after_cancel,
    )
    service = ComfyUIBridgeService(
        BASE_URL,
        credential_store=store,
        transport=transport,
    )
    worker = ComfyUIPairThread(service)
    worker_reference["worker"] = worker
    codes = []
    errors = []
    successes = []
    worker.verification_code_ready.connect(codes.append)
    worker.error_occurred.connect(errors.append)
    worker.pairing_succeeded.connect(lambda: successes.append(True))
    worker.run()
    assert codes == ["577559"]
    assert errors == ["pairing_cancelled"]
    assert successes == []
    assert store.saved == []
    assert len(transport.calls) == 3


def test_comfyui_test_worker_returns_only_stable_error_code():
    class FailingService:
        def test_connection(self):
            raise RuntimeError("private remote response")

    errors = []
    worker = ComfyUITestThread(FailingService())
    worker.error_occurred.connect(errors.append)
    worker.run()
    assert errors == ["bridge_unavailable"]


def test_comfyui_send_worker_sends_one_immutable_snapshot_with_safe_signal():
    class RecordingService:
        def __init__(self):
            self.calls = []

        def send(self, text):
            self.calls.append(text)
            return SendResult(status="success", request_id="synthetic-request-id")

    service = RecordingService()
    snapshot = "synthetic edited output"
    worker = ComfyUISendThread(service, snapshot)
    successes = []
    errors = []
    worker.send_succeeded.connect(lambda: successes.append(True))
    worker.error_occurred.connect(errors.append)
    worker.run()
    assert service.calls == [snapshot]
    assert successes == [True]
    assert errors == []
    assert snapshot not in repr(worker)


def test_comfyui_send_worker_emits_only_stable_error_code_without_retry():
    class FailingService:
        def __init__(self):
            self.calls = 0

        def send(self, text):
            self.calls += 1
            raise ComfyUIBridgeError("timeout", "private remote response")

    service = FailingService()
    worker = ComfyUISendThread(service, "synthetic private output")
    errors = []
    successes = []
    worker.error_occurred.connect(errors.append)
    worker.send_succeeded.connect(lambda: successes.append(True))
    worker.run()
    assert service.calls == 1
    assert errors == ["timeout"]
    assert successes == []
    assert "private remote response" not in repr(worker)
    assert "synthetic private output" not in repr(worker)


def test_real_generation_resubmits_backend_signature_when_server_already_exists(tmp_path):
    model = tmp_path / "qwen3-4b-q4_k_m.gguf"
    model.write_bytes(b"GGUF")

    class FakeEngine:
        def request_payload(self, request_text, settings):
            return {"messages": [{"role": "user", "content": request_text}], "max_tokens": 10}

        def finalize_output(self, request_text, settings, output):
            from core.renderers import RenderResult

            return RenderResult(output)

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


def test_generation_length_warning_is_emitted_after_rendered_result():
    class FakeEngine:
        def request_payload(self, request_text, settings):
            return {"messages": [], "max_tokens": 10}

        def finalize_output(self, request_text, settings, output):
            from core.renderers import RenderResult

            return RenderResult(
                output,
                warnings=("PROMPT_LONGER_THAN_RECOMMENDED",),
            )

    class FakeServer:
        def generate(self, payload, timeout):
            return "generated"

    worker = GenerationThread(
        engine=FakeEngine(),
        server=FakeServer(),
        config=AppConfig(),
        request_text="test",
        settings=PromptSettings(),
        mock_mode=True,
    )
    events = []
    worker.result_ready.connect(lambda result: events.append(("result", result)))
    worker.status_changed.connect(lambda status: events.append(("status", status)))
    worker.run()

    from core.renderers import RenderResult

    assert events[-2:] == [
        (
            "result",
            RenderResult(
                "generated",
                warnings=("PROMPT_LONGER_THAN_RECOMMENDED",),
            ),
        ),
        ("status", "PROMPT_LONGER_THAN_RECOMMENDED"),
    ]
