from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .inference_backends import (
    BACKEND_CPU,
    BACKEND_VULKAN,
    GPU_LAYERS_AUTO,
    BackendDevice,
    backend_spec,
    normalize_backend_id,
    parse_vulkan_devices,
)
from .prompt_engine import clean_model_output
from .prompt_engine import DEFAULT_MAX_OUTPUT_TOKENS
from .config_manager import DEFAULT_CONTEXT_SIZE


DEFAULT_GENERATION_TIMEOUT_SECONDS = 300.0
DEFAULT_STARTUP_TIMEOUT_SECONDS = 300.0
CONTEXT_SAFETY_TOKENS = 64
MIN_OUTPUT_TOKENS = 512
LOGGER = logging.getLogger(__name__)
GENERATION_TIMEOUT_MESSAGE = (
    "生成がタイムアウトしました（300秒）。CPU/GPU生成は環境によって数分かかることがあります。"
    "Context Sizeを8192または4096に下げ、他のアプリを閉じてから再試行してください。"
)
# Kept as an import alias for existing integrations; the text is backend-neutral.
CPU_TIMEOUT_MESSAGE = GENERATION_TIMEOUT_MESSAGE


@dataclass(frozen=True, slots=True)
class LlamaModelSpec:
    """Complete identity of the single runtime model owned by the application."""

    model_path: str
    backend: str
    backend_device: str
    context_size: int
    cpu_threads: int
    gpu_layers: int
    mmproj_path: str | None = None

    @property
    def launch_signature(self) -> tuple[Any, ...]:
        return (
            os.path.normcase(os.path.normpath(self.model_path)),
            self.backend,
            self.backend_device,
            self.context_size,
            self.cpu_threads,
            self.gpu_layers,
            os.path.normcase(os.path.normpath(self.mmproj_path))
            if self.mmproj_path
            else None,
        )

    def can_serve_text_spec(self, requested: "LlamaModelSpec") -> bool:
        """A loaded multimodal runtime can safely satisfy its text-only base spec."""
        if requested.mmproj_path is not None:
            return False
        return self.launch_signature[:-1] == requested.launch_signature[:-1]


def _windows_safe_subprocess_path(path: Path) -> str:
    """Use an ASCII 8.3 alias when Windows child I/O fails from a Unicode path."""
    value = str(path)
    if sys.platform != "win32" or value.isascii():
        return value
    try:
        import ctypes

        get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        get_short_path.restype = ctypes.c_uint
        required = get_short_path(value, None, 0)
        if required <= 0:
            return value
        buffer = ctypes.create_unicode_buffer(required)
        written = get_short_path(value, buffer, len(buffer))
        short_path = buffer.value
        if written <= 0 or written >= len(buffer) or not short_path.isascii():
            return value
        return short_path
    except (AttributeError, OSError, ValueError):
        return value


class LlamaError(RuntimeError):
    pass


class LlamaTimeoutError(LlamaError):
    pass


class LlamaConnectionError(LlamaError):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


class LlamaContextError(LlamaError):
    pass


def _server_http_error(status: int, body: bytes) -> LlamaConnectionError:
    error_type: str | None = None
    server_message = ""
    n_prompt_tokens: int | None = None
    n_ctx: int | None = None
    try:
        decoded = json.loads(body.decode("utf-8"))
        error = decoded.get("error", decoded) if isinstance(decoded, dict) else {}
        if isinstance(error, dict):
            error_type = str(error.get("type") or "") or None
            server_message = str(error.get("message") or "")
            n_prompt_tokens = int(error["n_prompt_tokens"]) if "n_prompt_tokens" in error else None
            n_ctx = int(error["n_ctx"]) if "n_ctx" in error else None
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        pass

    LOGGER.warning(
        "llama-server HTTP error status=%s type=%s n_prompt_tokens=%s n_ctx=%s",
        status,
        error_type or "unknown",
        n_prompt_tokens,
        n_ctx,
    )
    is_context_error = error_type == "exceed_context_size_error" or (
        "exceeds the available context size" in server_message.lower()
    )
    if is_context_error:
        if n_prompt_tokens is not None and n_ctx is not None:
            message = (
                f"Context Size不足です。入力は{n_prompt_tokens}トークンですが、選択中のContextは"
                f"{n_ctx}です。設定で8192以上を選択してください。"
            )
        else:
            message = "Context Size不足のためserverがリクエストを拒否しました。設定で8192以上を選択してください。"
        return LlamaConnectionError(message, status=status, error_type=error_type)

    safe_message = re.sub(r"\s+", " ", server_message).strip()
    if len(safe_message) > 500:
        safe_message = "serverの詳細メッセージが長いため、Prompt保護のため表示を省略しました。"
    if safe_message:
        message = f"ローカルserverがHTTP {status}を返しました。server: {safe_message}"
    else:
        message = f"ローカルserverがHTTP {status}を返しました。レスポンス形式を確認してください。"
    return LlamaConnectionError(message, status=status, error_type=error_type)


def _assert_loopback_url(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("llama-serverは127.0.0.1のローカルHTTP接続のみ利用できます。")


class LocalLlamaClient:
    def __init__(self, base_url: str) -> None:
        _assert_loopback_url(base_url)
        self.base_url = base_url.rstrip("/")
        self.last_response_metrics: dict[str, int | float] = {}

    def chat_completion(
        self, payload: dict[str, Any], timeout: float = DEFAULT_GENERATION_TIMEOUT_SECONDS
    ) -> str:
        url = f"{self.base_url}/v1/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                raw = response.read()
        except (TimeoutError, socket.timeout) as exc:
            raise LlamaTimeoutError(GENERATION_TIMEOUT_MESSAGE) from exc
        except urllib.error.HTTPError as exc:
            raise _server_http_error(exc.code, exc.read()) from exc
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise LlamaTimeoutError(GENERATION_TIMEOUT_MESSAGE) from exc
            raise LlamaConnectionError("ローカルllama-serverへ接続できません。") from exc
        if status != 200:
            raise LlamaConnectionError(f"ローカルserverがHTTP {status}を返しました。")
        try:
            data = json.loads(raw.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LlamaError("llama-serverのレスポンス形式が不正です。") from exc
        if not isinstance(content, str) or not content.strip():
            raise LlamaError("llama-serverから空のレスポンスが返されました。")
        self.last_response_metrics = self._extract_response_metrics(data)
        return clean_model_output(content)

    @staticmethod
    def _extract_response_metrics(data: dict[str, Any]) -> dict[str, int | float]:
        metrics: dict[str, int | float] = {}
        usage = data.get("usage") if isinstance(data, dict) else None
        timings = data.get("timings") if isinstance(data, dict) else None
        candidates = {
            "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            "generated_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
            "prompt_ms": timings.get("prompt_ms") if isinstance(timings, dict) else None,
            "generation_ms": timings.get("predicted_ms") if isinstance(timings, dict) else None,
            "tokens_per_second": (
                timings.get("predicted_per_second") if isinstance(timings, dict) else None
            ),
        }
        for key, value in candidates.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                metrics[key] = value
        return metrics

    def count_input_tokens(self, payload: dict[str, Any], timeout: float = 30.0) -> int | None:
        """Use llama.cpp's active model/chat template for exact input token counting."""
        url = f"{self.base_url}/v1/chat/completions/input_tokens"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code == 404:
                LOGGER.info("llama-server input token endpoint unavailable; using local estimate")
                return None
            raise _server_http_error(exc.code, body) from exc
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
            LOGGER.info("llama-server input token count unavailable; using local estimate")
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
            value = int(data["input_tokens"])
            return value if value >= 0 else None
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            LOGGER.info("llama-server input token response invalid; using local estimate")
            return None


class LlamaServerManager:
    """Owns exactly one llama-server process and talks to it over loopback HTTP."""

    def __init__(
        self,
        runtime_root: Path | str,
        base_url: str | None = None,
        log_dir: Path | str | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self.base_url = base_url
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.process: subprocess.Popen[str] | None = None
        self._stdout_handle: Any | None = None
        self._stderr_handle: Any | None = None
        self._launch_signature: tuple[Any, ...] | None = None
        self.active_model_spec: LlamaModelSpec | None = None
        self._cancelled = threading.Event()
        self.active_backend = BACKEND_CPU
        self.active_device = ""
        self.last_model_load_seconds: float | None = None
        self.last_generation_metrics: dict[str, int | float | str | None] = {}
        self._multimodal_states: dict[tuple[str, str], str] = {}

    @property
    def is_mock_or_external_local(self) -> bool:
        return self.base_url is not None and self.process is None

    @property
    def is_owned_server_running(self) -> bool:
        """Return whether this manager currently owns a live llama-server."""
        process = self.process
        return process is not None and process.poll() is None

    @staticmethod
    def _multimodal_key(model_path: Path | str, mmproj_path: Path | str) -> tuple[str, str]:
        return (
            os.path.normcase(os.path.normpath(str(Path(model_path).resolve(strict=False)))),
            os.path.normcase(os.path.normpath(str(Path(mmproj_path).resolve(strict=False)))),
        )

    def mark_multimodal_state(
        self,
        model_path: Path | str,
        mmproj_path: Path | str,
        state: str,
    ) -> None:
        if state not in {"available", "unsupported", "load_error"}:
            raise ValueError(f"Invalid multimodal state: {state}")
        self._multimodal_states[
            self._multimodal_key(model_path, mmproj_path)
        ] = state

    def multimodal_state_for(
        self,
        model_path: Path | str,
        mmproj_path: Path | str,
    ) -> str | None:
        return self._multimodal_states.get(
            self._multimodal_key(model_path, mmproj_path)
        )

    @staticmethod
    def _validated_backend_id(value: str) -> str:
        lowered = str(value).strip().lower()
        if lowered not in {"cpu", "vulkan", "vulkan gpu"}:
            raise ValueError(f"未対応の推論バックエンドです: {value}")
        return normalize_backend_id(value)

    def executable_for_backend(self, backend: str) -> Path:
        spec = backend_spec(self._validated_backend_id(backend))
        return self.runtime_root / spec.runtime_variant / "llama-server.exe"

    def executable_for_device(self, device: str) -> Path:
        """Compatibility alias for callers upgraded from the old device terminology."""
        return self.executable_for_backend(device)

    def runtime_available(self, backend: str) -> bool:
        return self.executable_for_backend(backend).is_file()

    def detect_vulkan_devices(self, timeout: float = 15.0) -> list[BackendDevice]:
        executable = self.executable_for_backend(BACKEND_VULKAN)
        if not executable.is_file():
            return []
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        launch_executable = _windows_safe_subprocess_path(executable)
        launch_directory = _windows_safe_subprocess_path(executable.parent)
        try:
            completed = subprocess.run(
                [launch_executable, "--list-devices"],
                cwd=launch_directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOGGER.warning("Vulkan device detection failed: %s", type(exc).__name__)
            return []
        output = f"{completed.stdout}\n{completed.stderr}"
        devices = parse_vulkan_devices(output)
        LOGGER.info(
            "Vulkan device detection returncode=%s devices=%s",
            completed.returncode,
            [device.identifier for device in devices],
        )
        return devices

    def build_server_command(
        self,
        model_path: Path | str,
        *,
        backend: str,
        backend_device: str = "",
        context_size: int = DEFAULT_CONTEXT_SIZE,
        cpu_threads: int = 0,
        gpu_layers: int = GPU_LAYERS_AUTO,
        mmproj_path: Path | str | None = None,
        port: int,
    ) -> list[str]:
        backend_id = self._validated_backend_id(backend)
        executable = self.executable_for_backend(backend_id)
        if backend_id == BACKEND_CPU:
            layers_value = "0"
        else:
            normalized_layers = max(GPU_LAYERS_AUTO, int(gpu_layers))
            layers_value = "auto" if normalized_layers == GPU_LAYERS_AUTO else str(normalized_layers)
        command = [
            str(executable),
            "--model",
            str(Path(model_path).resolve()),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ctx-size",
            str(context_size),
            "--parallel",
            "1",
            "--offline",
            "--cache-prompt",
            "--skip-chat-parsing",
            "--chat-template-kwargs",
            json.dumps({"enable_thinking": False}, separators=(",", ":")),
            "--no-webui",
            "--n-gpu-layers",
            layers_value,
        ]
        if backend_id == BACKEND_VULKAN and backend_device:
            command.extend(["--device", backend_device])
        if cpu_threads > 0:
            command.extend(["--threads", str(cpu_threads)])
        if mmproj_path:
            command.extend(["--mmproj", str(Path(mmproj_path).resolve())])
        return command

    def start(
        self,
        model_path: Path | str,
        *,
        backend: str = BACKEND_CPU,
        backend_device: str = "",
        context_size: int = DEFAULT_CONTEXT_SIZE,
        cpu_threads: int = 0,
        gpu_layers: int = GPU_LAYERS_AUTO,
        mmproj_path: Path | str | None = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        status_callback: Callable[[str], None] | None = None,
    ) -> str:
        backend_id = self._validated_backend_id(backend)
        if self.base_url and self.process is None:
            return self.base_url
        selected_device = backend_device.strip()
        if backend_id == BACKEND_VULKAN:
            devices = self.detect_vulkan_devices()
            if not devices:
                raise LlamaError(
                    "Vulkan対応GPUをllama.cppで検出できませんでした。CPUバックエンドを使用してください。"
                )
            if not selected_device:
                selected_device = devices[0].identifier
            if selected_device not in {device.identifier for device in devices}:
                raise LlamaError(
                    f"選択されたVulkanデバイス「{selected_device}」を検出できません。設定を開き直してください。"
                )
        requested_spec = LlamaModelSpec(
            model_path=str(Path(model_path).resolve()),
            backend=backend_id,
            backend_device=selected_device,
            context_size=int(context_size),
            cpu_threads=int(cpu_threads),
            gpu_layers=int(gpu_layers),
            mmproj_path=(
                str(Path(mmproj_path).resolve()) if mmproj_path else None
            ),
        )
        signature = requested_spec.launch_signature
        if self.base_url:
            if self._launch_signature == signature or (
                self.active_model_spec is not None
                and self.active_model_spec.can_serve_text_spec(requested_spec)
            ):
                return self.base_url
            if status_callback is not None:
                status_callback("status.switching_model")
                status_callback("status.unloading_model")
            self.stop()
        if status_callback is not None:
            status_callback("status.loading_model")
        executable = self.executable_for_backend(backend_id)
        runtime_dir = executable.parent
        if not executable.is_file():
            message = (
                "Vulkan用 llama.cpp runtime が設定されていません。CPUバックエンドを使用してください。"
                if backend_id == BACKEND_VULKAN
                else "CPU用 llama-serverが見つかりません。"
            )
            raise LlamaError(message)
        port = self._free_port()
        command = self.build_server_command(
            model_path,
            backend=backend_id,
            backend_device=selected_device,
            context_size=context_size,
            cpu_threads=cpu_threads,
            gpu_layers=gpu_layers,
            mmproj_path=mmproj_path,
            port=port,
        )
        command[0] = _windows_safe_subprocess_path(executable)
        launch_directory = _windows_safe_subprocess_path(runtime_dir)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        stdout_target: Any = subprocess.DEVNULL
        stderr_target: Any = subprocess.DEVNULL
        if self.log_dir is not None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._stdout_handle = (self.log_dir / "llama-server.stdout.log").open("ab")
            self._stderr_handle = (self.log_dir / "llama-server.stderr.log").open("ab")
            stdout_target = self._stdout_handle
            stderr_target = self._stderr_handle
        try:
            self.process = subprocess.Popen(
                command,
                cwd=launch_directory,
                stdout=stdout_target,
                stderr=stderr_target,
                creationflags=creation_flags,
            )
        except Exception:
            self._close_log_handles()
            raise
        self.base_url = f"http://127.0.0.1:{port}"
        started_at = time.monotonic()
        deadline = started_at + startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.process = None
                self.base_url = None
                self._close_log_handles()
                raise LlamaError("llama-serverの起動に失敗しました。モデルまたはログを確認してください。")
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=1.0) as response:
                    if response.status == 200:
                        self._launch_signature = signature
                        self.active_model_spec = requested_spec
                        self.active_backend = backend_id
                        self.active_device = selected_device
                        self.last_model_load_seconds = time.monotonic() - started_at
                        return self.base_url
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.25)
        self.stop()
        raise LlamaTimeoutError(
            "モデル読み込みがタイムアウトしました（300秒）。"
            "CPU/GPU処理は環境によって数分かかることがあります。Context Sizeまたは空きRAMを確認してください。"
        )

    def generate(
        self, payload: dict[str, Any], timeout: float = DEFAULT_GENERATION_TIMEOUT_SECONDS
    ) -> str:
        self._cancelled.clear()
        if not self.base_url:
            raise LlamaError("llama-serverが起動していません。")
        try:
            client = LocalLlamaClient(self.base_url)
            started_at = time.monotonic()
            result = client.chat_completion(payload, timeout=timeout)
            elapsed = time.monotonic() - started_at
            self.last_generation_metrics = {
                "backend": self.active_backend,
                "device": self.active_device or None,
                "model_load_seconds": self.last_model_load_seconds,
                "request_seconds": elapsed,
                **client.last_response_metrics,
            }
        except LlamaTimeoutError as exc:
            # Only terminate the process created and tracked by this manager.
            if self.process is not None:
                self.stop()
            raise LlamaTimeoutError(GENERATION_TIMEOUT_MESSAGE) from exc
        if self._cancelled.is_set():
            raise LlamaError("生成はユーザーによってキャンセルされました。")
        return result

    def preflight_context(self, payload: dict[str, Any], context_size: int) -> tuple[int, int]:
        if not self.base_url:
            raise LlamaError("llama-serverが起動していません。")
        client = LocalLlamaClient(self.base_url)
        input_tokens = client.count_input_tokens(payload)
        if input_tokens is None:
            input_tokens = self._estimate_input_tokens(payload.get("messages", []))
        requested_output = int(payload.get("max_tokens", DEFAULT_MAX_OUTPUT_TOKENS))
        available_output = context_size - input_tokens - CONTEXT_SAFETY_TOKENS
        if input_tokens >= context_size or available_output < MIN_OUTPUT_TOKENS:
            suggested = 8192 if input_tokens + DEFAULT_MAX_OUTPUT_TOKENS <= 8192 else 16384
            if input_tokens + DEFAULT_MAX_OUTPUT_TOKENS > 16384:
                suggested = 32768
            raise LlamaContextError(
                f"Context Size不足です。入力は約{input_tokens}トークン、最低出力予算は"
                f"{MIN_OUTPUT_TOKENS}トークンですが、選択中のContextは{context_size}です。"
                f"設定で{suggested}以上を選択してください。"
            )
        payload["max_tokens"] = min(requested_output, available_output)
        LOGGER.info(
            "Context preflight input_tokens=%s output_budget=%s context_size=%s",
            input_tokens,
            payload["max_tokens"],
            context_size,
        )
        return input_tokens, int(payload["max_tokens"])

    def cancel(self) -> None:
        self._cancelled.set()
        if self.process is not None:
            self.stop()

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.base_url = None
        self._launch_signature = None
        self.active_model_spec = None
        self._close_log_handles()

    def _close_log_handles(self) -> None:
        for handle_name in ("_stdout_handle", "_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                try:
                    handle.close()
                finally:
                    setattr(self, handle_name, None)

    @staticmethod
    def _estimate_input_tokens(messages: Any) -> int:
        ascii_characters = 0
        non_ascii_characters = 0
        message_count = 0
        media_tokens = 0
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_count += 1
                raw_content = message.get("content", "")
                if isinstance(raw_content, list):
                    text_parts = []
                    for part in raw_content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "text":
                            text_parts.append(str(part.get("text", "")))
                        elif part.get("type") == "image_url":
                            # The active multimodal processor normally supplies an
                            # exact count. Never treat base64 bytes as text tokens if
                            # that endpoint is unavailable.
                            media_tokens += 2048
                    content = " ".join(text_parts)
                else:
                    content = str(raw_content)
                ascii_characters += sum(ord(character) < 128 for character in content)
                non_ascii_characters += sum(ord(character) >= 128 for character in content)
        return (
            math.ceil(ascii_characters / 4)
            + math.ceil(non_ascii_characters * 1.5)
            + message_count * 8
            + media_tokens
        )

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])
