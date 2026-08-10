from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core.inference_backends import (  # noqa: E402
    BACKEND_CPU,
    BACKEND_VULKAN,
    GPU_LAYERS_AUTO,
    backend_spec,
)
from core.llama_manager import LlamaServerManager  # noqa: E402
from core.model_manager import validate_model  # noqa: E402
from core.performance_diagnostics import PerformanceRecord, write_performance_report  # noqa: E402
from core.prompt_engine import PromptEngine, PromptSettings  # noqa: E402
from core.skill_manager import SkillManager  # noqa: E402
from core.system_memory import get_system_memory  # noqa: E402


def _workspace_report_path(value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve(strict=False)
    prefix = str(PROJECT_ROOT.resolve()).rstrip("\\/") + "\\"
    if not str(resolved).lower().startswith(prefix.lower()):
        raise argparse.ArgumentTypeError("診断レポートはプロジェクト内へ保存してください。")
    return resolved


def _gpu_layers(value: str) -> int:
    if value.strip().lower() == "auto":
        return GPU_LAYERS_AUTO
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("GPU layersはAutoまたは0以上を指定してください。")
    return parsed


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CPU/Vulkan performance diagnostic (prompt text is never recorded)."
    )
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument(
        "--skill",
        type=Path,
        default=PROJECT_ROOT / "skills" / "h3-prompt-writing",
    )
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--backend", choices=("cpu", "vulkan", "both"), default="both")
    parser.add_argument("--vulkan-device", default="")
    parser.add_argument("--gpu-layers", type=_gpu_layers, default=GPU_LAYERS_AUTO)
    parser.add_argument(
        "--report",
        type=_workspace_report_path,
        default=PROJECT_ROOT / ".dev-data" / "benchmarks" / "last-cpu-vulkan.json",
    )
    return parser.parse_args()


def _run_backend(
    *,
    backend: str,
    device_identifier: str,
    model,
    engine: PromptEngine,
    request_text: str,
    context_size: int,
    gpu_layers: int,
) -> PerformanceRecord:
    manager = LlamaServerManager(
        PROJECT_ROOT / "runtime",
        log_dir=PROJECT_ROOT / ".dev-data" / "benchmarks" / "llama-server" / backend,
    )
    device_name: str | None = None
    selected_device = device_identifier
    if backend == BACKEND_VULKAN:
        devices = manager.detect_vulkan_devices()
        if not devices:
            raise RuntimeError("Vulkan対応GPUをllama.cppで検出できませんでした。")
        selected = next(
            (device for device in devices if device.identifier == selected_device),
            devices[0] if not selected_device else None,
        )
        if selected is None:
            raise RuntimeError(f"Vulkanデバイスを検出できません: {selected_device}")
        selected_device = selected.identifier
        device_name = selected.display_name

    payload = engine.request_payload(request_text, PromptSettings())
    before = get_system_memory()
    print(f"Starting {backend_spec(backend).display_name} diagnostic...")
    try:
        manager.start(
            model.path,
            backend=backend,
            backend_device=selected_device,
            context_size=context_size,
            gpu_layers=gpu_layers,
        )
        input_tokens, output_budget = manager.preflight_context(payload, context_size)
        started_at = time.monotonic()
        manager.generate(payload)
        request_seconds = time.monotonic() - started_at
        after = get_system_memory()
        metrics = manager.last_generation_metrics
        prompt_ms = metrics.get("prompt_ms")
        generation_ms = metrics.get("generation_ms")
        return PerformanceRecord.now(
            backend=backend,
            device=device_name,
            model_filename=model.filename,
            model_size_bytes=model.size_bytes,
            context_size=context_size,
            gpu_layers="auto" if gpu_layers == GPU_LAYERS_AUTO else gpu_layers,
            model_load_seconds=manager.last_model_load_seconds,
            request_seconds=request_seconds,
            prompt_processing_seconds=(float(prompt_ms) / 1000 if prompt_ms is not None else None),
            generation_seconds=(
                float(generation_ms) / 1000 if generation_ms is not None else None
            ),
            input_tokens=input_tokens,
            output_budget_tokens=output_budget,
            generated_tokens=(
                int(metrics["generated_tokens"]) if "generated_tokens" in metrics else None
            ),
            tokens_per_second=(
                float(metrics["tokens_per_second"])
                if "tokens_per_second" in metrics
                else None
            ),
            ram_available_before_bytes=before.available_bytes if before else None,
            ram_available_after_bytes=after.available_bytes if after else None,
            ram_total_bytes=(after or before).total_bytes if (after or before) else None,
        )
    finally:
        manager.stop()


def main() -> int:
    args = _arguments()
    model = validate_model(args.model)
    request_text = args.request_file.read_text(encoding="utf-8").strip()
    if not request_text:
        raise ValueError("request fileが空です。")
    engine = PromptEngine(SkillManager(args.skill))
    backends = (
        [BACKEND_CPU, BACKEND_VULKAN]
        if args.backend == "both"
        else [args.backend]
    )
    records = [
        _run_backend(
            backend=backend,
            device_identifier=args.vulkan_device,
            model=model,
            engine=engine,
            request_text=request_text,
            context_size=args.context,
            gpu_layers=args.gpu_layers,
        )
        for backend in backends
    ]
    write_performance_report(args.report, records)
    print(json.dumps({"report": str(args.report), "records": len(records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
