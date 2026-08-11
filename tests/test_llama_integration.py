from __future__ import annotations

import io
import gc
import json
from pathlib import Path
import sys
import tracemalloc
from types import SimpleNamespace
import urllib.error

import pytest

from core.llama_manager import (
    CPU_TIMEOUT_MESSAGE,
    DEFAULT_GENERATION_TIMEOUT_SECONDS,
    LlamaContextError,
    LlamaConnectionError,
    LlamaError,
    LlamaServerManager,
    LlamaTimeoutError,
    LocalLlamaClient,
    _windows_safe_subprocess_path,
)
from core.inference_backends import (
    BACKEND_CPU,
    BACKEND_VULKAN,
    GPU_LAYERS_AUTO,
    BackendDevice,
    parse_vulkan_devices,
)
from core.prompt_engine import PromptEngine, PromptSettings
from core.skill_manager import SkillManager
from mock_server import start_mock_server


FIXTURE = Path(__file__).parent / "fixtures" / "skills" / "h3-prompt-writing"


def test_mock_server_generate_without_model(tmp_path):
    server, url = start_mock_server()
    try:
        payload = PromptEngine(SkillManager(FIXTURE)).request_payload(
            "女性が手を振る。台詞は「またね」", PromptSettings()
        )
        manager = LlamaServerManager(tmp_path, base_url=url)
        result = manager.generate(payload, timeout=2)
        assert result.startswith("A 10-second")
        assert "<think>" not in result
        assert "「またね」" in result
    finally:
        server.shutdown()
        server.server_close()


def test_timeout_is_reported(tmp_path):
    server, url = start_mock_server(delay=0.2)
    try:
        with pytest.raises(LlamaTimeoutError, match="タイムアウト"):
            LocalLlamaClient(url).chat_completion({"messages": []}, timeout=0.01)
    finally:
        server.shutdown()
        server.server_close()


def test_network_failure_and_external_url_rejection(monkeypatch):
    def refused(*args, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError("mock connection refused"))

    monkeypatch.setattr("urllib.request.urlopen", refused)
    with pytest.raises(LlamaConnectionError, match="接続できません"):
        LocalLlamaClient("http://127.0.0.1:1").chat_completion({"messages": []}, timeout=0.1)
    with pytest.raises(ValueError, match="ローカルHTTP"):
        LocalLlamaClient("https://api.openai.com")


def test_cpu_and_vulkan_runtime_discovery(tmp_path):
    runtime = tmp_path / "runtime"
    cpu_executable = runtime / "cpu" / "llama-server.exe"
    vulkan_executable = runtime / "vulkan" / "llama-server.exe"
    cpu_executable.parent.mkdir(parents=True)
    cpu_executable.write_bytes(b"test executable placeholder")
    manager = LlamaServerManager(runtime)
    assert manager.executable_for_backend(BACKEND_CPU) == cpu_executable
    assert manager.executable_for_backend(BACKEND_VULKAN) == vulkan_executable
    assert manager.runtime_available(BACKEND_CPU) is True
    assert manager.runtime_available(BACKEND_VULKAN) is False
    vulkan_executable.parent.mkdir(parents=True)
    vulkan_executable.write_bytes(b"vulkan placeholder")
    assert manager.runtime_available(BACKEND_VULKAN) is True


def test_default_generation_timeout_is_300_seconds(monkeypatch, tmp_path):
    captured = {}

    def complete(self, payload, timeout):
        captured["timeout"] = timeout
        return "finished prompt"

    monkeypatch.setattr(LocalLlamaClient, "chat_completion", complete)
    manager = LlamaServerManager(tmp_path, base_url="http://127.0.0.1:8080")
    assert manager.generate({"messages": []}) == "finished prompt"
    assert captured["timeout"] == DEFAULT_GENERATION_TIMEOUT_SECONDS == 300.0


def test_timeout_stops_only_owned_server(monkeypatch, tmp_path):
    class FakeOwnedProcess:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 0

        def kill(self):
            self.terminated = True

    def timeout(self, payload, timeout):
        raise LlamaTimeoutError(CPU_TIMEOUT_MESSAGE)

    monkeypatch.setattr(LocalLlamaClient, "chat_completion", timeout)
    manager = LlamaServerManager(tmp_path)
    owned = FakeOwnedProcess()
    manager.process = owned
    manager.base_url = "http://127.0.0.1:8080"
    with pytest.raises(LlamaTimeoutError, match="CPU/GPU生成は"):
        manager.generate({"messages": []})
    assert owned.terminated is True
    assert manager.process is None
    assert manager.base_url is None


def test_timeout_does_not_terminate_unowned_local_server(monkeypatch, tmp_path):
    def timeout(self, payload, timeout):
        raise LlamaTimeoutError(CPU_TIMEOUT_MESSAGE)

    monkeypatch.setattr(LocalLlamaClient, "chat_completion", timeout)
    manager = LlamaServerManager(tmp_path, base_url="http://127.0.0.1:8080")
    with pytest.raises(LlamaTimeoutError):
        manager.generate({"messages": []})
    assert manager.process is None
    assert manager.base_url == "http://127.0.0.1:8080"


def test_cancel_terminates_owned_server_and_clears_connection(tmp_path):
    class FakeOwnedProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout):
            return 0

        def kill(self):
            self.running = False

    manager = LlamaServerManager(tmp_path)
    owned = FakeOwnedProcess()
    manager.process = owned
    manager.base_url = "http://127.0.0.1:8080"
    manager.cancel()
    assert owned.running is False
    assert manager.process is None
    assert manager.base_url is None


def test_server_start_uses_8192_context_and_loopback(monkeypatch, tmp_path):
    executable = tmp_path / "runtime" / "cpu" / "llama-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")
    captured = {}

    class FakeProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout):
            return 0

        def kill(self):
            self.running = False

    class HealthyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return FakeProcess()

    monkeypatch.setattr(
        "core.llama_manager._windows_safe_subprocess_path",
        lambda path: "SAFE-EXE" if Path(path).name == "llama-server.exe" else "SAFE-DIR",
    )
    monkeypatch.setattr("subprocess.Popen", popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: HealthyResponse())
    manager = LlamaServerManager(tmp_path / "runtime")
    manager.start(tmp_path / "model.gguf")
    command = captured["command"]
    assert command[0] == "SAFE-EXE"
    assert captured["cwd"] == "SAFE-DIR"
    assert command[command.index("--ctx-size") + 1] == "8192"
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert "--offline" in command
    assert "--cache-prompt" in command
    assert "--no-webui" in command
    manager.stop()


def test_vulkan_device_parser_reads_device_memory_and_uma_flag():
    output = """
ggml_vulkan: 0 = AMD Radeon(TM) Graphics | uma: 1 | fp16: 1
Available devices:
  Vulkan0: AMD Radeon(TM) Graphics (9596 MiB, 9116 MiB free)
"""
    devices = parse_vulkan_devices(output)
    assert len(devices) == 1
    assert devices[0].identifier == "Vulkan0"
    assert devices[0].name == "AMD Radeon(TM) Graphics"
    assert devices[0].is_uma is True
    assert devices[0].reported_memory_bytes == 9596 * 1024**2
    assert devices[0].reported_free_bytes == 9116 * 1024**2


def test_vulkan_device_detection_no_device_falls_back_cleanly(monkeypatch, tmp_path):
    executable = tmp_path / "runtime" / "vulkan" / "llama-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="Available devices:\n", stderr="", returncode=0
        ),
    )
    manager = LlamaServerManager(tmp_path / "runtime")
    assert manager.detect_vulkan_devices() == []
    with pytest.raises(LlamaError, match="CPUバックエンド"):
        manager.start(tmp_path / "model.gguf", backend=BACKEND_VULKAN)


def test_vulkan_detection_uses_windows_safe_child_paths(monkeypatch, tmp_path):
    executable = tmp_path / "runtime" / "vulkan" / "llama-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")
    captured = {}

    def safe_path(path):
        return "SAFE-EXE" if Path(path).name == "llama-server.exe" else "SAFE-DIR"

    def run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return SimpleNamespace(stdout="Available devices:\n", stderr="", returncode=0)

    monkeypatch.setattr("core.llama_manager._windows_safe_subprocess_path", safe_path)
    monkeypatch.setattr("subprocess.run", run)

    assert LlamaServerManager(tmp_path / "runtime").detect_vulkan_devices() == []
    assert captured == {"command": ["SAFE-EXE", "--list-devices"], "cwd": "SAFE-DIR"}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows short-path test")
def test_windows_safe_subprocess_path_resolves_unicode_existing_file(tmp_path):
    executable = tmp_path / "日本語 パス" / "llama-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")

    safe_path = _windows_safe_subprocess_path(executable)
    if safe_path == str(executable):
        pytest.skip("8.3 short paths are unavailable on this volume")
    assert safe_path.isascii()
    assert Path(safe_path).samefile(executable)


def test_backend_command_construction_auto_explicit_and_selected_device(tmp_path):
    manager = LlamaServerManager(tmp_path / "runtime")
    cpu = manager.build_server_command(
        tmp_path / "model.gguf",
        backend=BACKEND_CPU,
        context_size=8192,
        port=1234,
    )
    assert cpu[cpu.index("--n-gpu-layers") + 1] == "0"
    assert "--device" not in cpu
    assert cpu[cpu.index("--host") + 1] == "127.0.0.1"
    assert "--skip-chat-parsing" in cpu
    assert "--no-jinja" not in cpu
    assert "--chat-template" not in cpu
    template_kwargs = json.loads(cpu[cpu.index("--chat-template-kwargs") + 1])
    assert template_kwargs == {"enable_thinking": False}

    automatic = manager.build_server_command(
        tmp_path / "model.gguf",
        backend=BACKEND_VULKAN,
        backend_device="Vulkan0",
        context_size=8192,
        gpu_layers=GPU_LAYERS_AUTO,
        port=1235,
    )
    assert automatic[automatic.index("--n-gpu-layers") + 1] == "auto"
    assert automatic[automatic.index("--device") + 1] == "Vulkan0"
    assert "runtime\\vulkan\\llama-server.exe" in automatic[0]

    explicit = manager.build_server_command(
        tmp_path / "model.gguf",
        backend=BACKEND_VULKAN,
        backend_device="Vulkan1",
        context_size=8192,
        gpu_layers=20,
        port=1236,
    )
    assert explicit[explicit.index("--n-gpu-layers") + 1] == "20"
    assert explicit[explicit.index("--device") + 1] == "Vulkan1"


def test_server_command_keeps_japanese_space_model_path_as_one_argument(tmp_path):
    model = tmp_path / "日本語 モデル" / "Qwen3-4B Q4_K_M.gguf"
    manager = LlamaServerManager(tmp_path / "runtime")
    command = manager.build_server_command(
        model,
        backend=BACKEND_CPU,
        context_size=8192,
        port=1234,
    )
    assert command[command.index("--model") + 1] == str(model.resolve())


def test_backend_switch_restarts_owned_server_with_correct_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    for variant in ("cpu", "vulkan"):
        executable = runtime / variant / "llama-server.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"placeholder")
    commands = []
    processes = []

    class FakeProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout):
            return 0

        def kill(self):
            self.running = False

    class HealthyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def popen(command, **kwargs):
        process = FakeProcess()
        commands.append(command)
        processes.append(process)
        return process

    monkeypatch.setattr("subprocess.Popen", popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: HealthyResponse())
    manager = LlamaServerManager(runtime)
    monkeypatch.setattr(
        manager,
        "detect_vulkan_devices",
        lambda: [BackendDevice("Vulkan0", "AMD Radeon Graphics")],
    )
    manager.start(tmp_path / "model.gguf", backend=BACKEND_CPU)
    manager.start(
        tmp_path / "model.gguf",
        backend=BACKEND_VULKAN,
        backend_device="Vulkan0",
    )
    assert len(commands) == 2
    assert "runtime\\cpu\\llama-server.exe" in commands[0][0]
    assert "runtime\\vulkan\\llama-server.exe" in commands[1][0]
    assert processes[0].running is False
    assert manager.active_backend == BACKEND_VULKAN
    assert manager.active_device == "Vulkan0"
    manager.stop()
    assert processes[1].running is False


def test_selected_vulkan_device_must_exist(monkeypatch, tmp_path):
    executable = tmp_path / "runtime" / "vulkan" / "llama-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")
    manager = LlamaServerManager(tmp_path / "runtime")
    monkeypatch.setattr(
        manager,
        "detect_vulkan_devices",
        lambda: [BackendDevice("Vulkan0", "AMD Radeon Graphics")],
    )
    with pytest.raises(LlamaError, match="Vulkan1"):
        manager.start(
            tmp_path / "model.gguf",
            backend=BACKEND_VULKAN,
            backend_device="Vulkan1",
        )


def test_twenty_identical_launches_reuse_one_prompt_cache_server(monkeypatch, tmp_path):
    executable = tmp_path / "runtime" / "cpu" / "llama-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")
    commands = []

    class FakeProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout):
            return 0

        def kill(self):
            self.running = False

    class HealthyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "subprocess.Popen",
        lambda command, **kwargs: commands.append(command) or FakeProcess(),
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: HealthyResponse())
    manager = LlamaServerManager(tmp_path / "runtime")
    for _ in range(20):
        manager.start(tmp_path / "model.gguf", backend=BACKEND_CPU, context_size=8192)
    assert len(commands) == 1
    assert "--cache-prompt" in commands[0]
    assert manager.process is not None
    manager.stop()
    assert manager.process is None


def test_twenty_mock_generations_have_bounded_python_memory_growth(tmp_path):
    server, url = start_mock_server()
    try:
        manager = LlamaServerManager(tmp_path, base_url=url)
        payload = {"messages": [{"role": "user", "content": "leak test"}]}
        manager.generate(payload, timeout=2)
        gc.collect()
        tracemalloc.start()
        before, _ = tracemalloc.get_traced_memory()
        for _ in range(20):
            assert manager.generate(payload, timeout=2)
        gc.collect()
        after, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert after - before < 2 * 1024 * 1024
        assert peak - before < 8 * 1024 * 1024
        assert manager.process is None
    finally:
        server.shutdown()
        server.server_close()


def test_context_http_error_body_becomes_useful_japanese(monkeypatch):
    body = json.dumps(
        {
            "error": {
                "code": 400,
                "message": "request (4293 tokens) exceeds the available context size (4096 tokens), try increasing it",
                "type": "exceed_context_size_error",
                "n_prompt_tokens": 4293,
                "n_ctx": 4096,
            }
        }
    ).encode()

    def context_error(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://127.0.0.1:8080/v1/chat/completions",
            400,
            "Bad Request",
            {},
            io.BytesIO(body),
        )

    monkeypatch.setattr("urllib.request.urlopen", context_error)
    with pytest.raises(LlamaConnectionError) as caught:
        LocalLlamaClient("http://127.0.0.1:8080").chat_completion({"messages": []})
    assert "Context Size不足" in str(caught.value)
    assert "4293" in str(caught.value)
    assert "4096" in str(caught.value)
    assert caught.value.error_type == "exceed_context_size_error"


def test_preflight_rejects_4096_and_accepts_8192(monkeypatch, tmp_path):
    monkeypatch.setattr(LocalLlamaClient, "count_input_tokens", lambda self, payload: 4293)
    payload = {"messages": [], "max_tokens": 1536}
    manager = LlamaServerManager(tmp_path, base_url="http://127.0.0.1:8080")
    with pytest.raises(LlamaContextError, match="8192"):
        manager.preflight_context(payload.copy(), 4096)
    accepted = payload.copy()
    input_tokens, output_tokens = manager.preflight_context(accepted, 8192)
    assert input_tokens == 4293
    assert output_tokens == accepted["max_tokens"] == 1536
    assert input_tokens + output_tokens + 64 <= 8192


def test_preflight_reduces_output_budget_to_fit(monkeypatch, tmp_path):
    monkeypatch.setattr(LocalLlamaClient, "count_input_tokens", lambda self, payload: 7000)
    payload = {"messages": [], "max_tokens": 1536}
    manager = LlamaServerManager(tmp_path, base_url="http://127.0.0.1:8080")
    _, output_tokens = manager.preflight_context(payload, 8192)
    assert output_tokens == 1128
    assert 7000 + output_tokens + 64 == 8192


def test_owned_server_restarts_when_context_changes(monkeypatch, tmp_path):
    executable = tmp_path / "runtime" / "cpu" / "llama-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"placeholder")
    commands = []

    class FakeProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.running = False

        def wait(self, timeout):
            return 0

        def kill(self):
            self.running = False

    class HealthyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        "subprocess.Popen",
        lambda command, **kwargs: commands.append(command) or FakeProcess(),
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: HealthyResponse())
    manager = LlamaServerManager(tmp_path / "runtime")
    manager.start(tmp_path / "model.gguf", context_size=4096)
    manager.start(tmp_path / "model.gguf", context_size=4096)
    assert len(commands) == 1
    manager.start(tmp_path / "model.gguf", context_size=8192)
    assert len(commands) == 2
    assert commands[-1][commands[-1].index("--ctx-size") + 1] == "8192"
    manager.stop()
