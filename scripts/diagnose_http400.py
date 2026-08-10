from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from core.prompt_engine import PromptEngine, PromptSettings  # noqa: E402
from core.llama_manager import LlamaServerManager  # noqa: E402
from core.skill_manager import SkillManager  # noqa: E402


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def workspace_child(path: Path) -> Path:
    resolved_root = PROJECT_ROOT.resolve()
    resolved = path.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise RuntimeError(f"Diagnostic path is outside the workspace: {resolved}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--mode", default="T2VA")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    runtime = workspace_child(PROJECT_ROOT / "runtime" / "cpu")
    executable = runtime / "llama-server.exe"
    skill = workspace_child(PROJECT_ROOT / "skills" / "h3-prompt-writing")
    run_dir = workspace_child(
        PROJECT_ROOT / ".tmp" / "http400-diagnosis" / uuid.uuid4().hex
    )
    run_dir.mkdir(parents=True)
    stdout_path = run_dir / "llama.stdout.log"
    stderr_path = run_dir / "llama.stderr.log"
    port = free_port()
    command = [
        str(executable),
        "--model",
        str(args.model.resolve()),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        str(args.context),
        "--parallel",
        "1",
        "--n-gpu-layers",
        "0",
        "--no-webui",
    ]
    settings = PromptSettings(mode=args.mode, duration=10, processing="Faithful")
    # Deliberately minimal diagnostic text; never reuse or persist the user's real prompt.
    payload = PromptEngine(SkillManager(skill)).request_payload(
        "A person slowly waves once.", settings
    )
    summary = {
        "context_size": args.context,
        "mode": args.mode,
        "message_count": len(payload["messages"]),
        "message_characters": sum(len(item["content"]) for item in payload["messages"]),
        "payload_keys": sorted(payload.keys()),
        "max_tokens_present": "max_tokens" in payload,
        "prompt_logged": False,
    }
    print("PAYLOAD_SUMMARY=" + json.dumps(summary, ensure_ascii=False))
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=runtime,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creation_flags,
        )
        try:
            deadline = time.monotonic() + 300
            base_url = f"http://127.0.0.1:{port}"
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"llama-server exited during startup: {process.returncode}")
                try:
                    with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError, OSError):
                    time.sleep(0.25)
            else:
                raise RuntimeError("llama-server startup timed out")

            if args.preflight_only:
                manager = LlamaServerManager(runtime, base_url=base_url)
                input_tokens, output_tokens = manager.preflight_context(payload, args.context)
                print(f"PREFLIGHT_INPUT_TOKENS={input_tokens}")
                print(f"PREFLIGHT_OUTPUT_BUDGET={output_tokens}")
                print(f"PREFLIGHT_TOTAL_WITH_MARGIN={input_tokens + output_tokens + 64}")
                print("PREFLIGHT_RESULT=accepted")
            else:
                request = urllib.request.Request(
                    f"{base_url}/v1/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=60) as response:
                        response.read()
                        print(f"HTTP_STATUS={response.status}")
                        print("HTTP_BODY_NOT_PRINTED=successful response")
                except urllib.error.HTTPError as exc:
                    body = exc.read().decode("utf-8", errors="replace")
                    print(f"HTTP_STATUS={exc.code}")
                    print("HTTP_ERROR_BODY=" + body)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    print(f"STDOUT_LOG={stdout_path}")
    print(f"STDERR_LOG={stderr_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
