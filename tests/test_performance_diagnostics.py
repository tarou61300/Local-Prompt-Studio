from __future__ import annotations

import json

from core.performance_diagnostics import PerformanceRecord, write_performance_report


def test_performance_report_contains_metrics_but_no_prompt_text(tmp_path):
    secret_prompt = "この文字列は診断ファイルへ保存してはいけない"
    record = PerformanceRecord.now(
        backend="vulkan",
        device="AMD Radeon Graphics (Vulkan0)",
        model_filename="qwen3-4b-q4_k_m.gguf",
        model_size_bytes=123,
        context_size=8192,
        gpu_layers="auto",
        model_load_seconds=12.3,
        request_seconds=42.0,
        prompt_processing_seconds=2.0,
        generation_seconds=40.0,
        input_tokens=4293,
        output_budget_tokens=1536,
        generated_tokens=256,
        tokens_per_second=6.4,
        ram_available_before_bytes=5_000_000_000,
        ram_available_after_bytes=4_000_000_000,
        ram_total_bytes=13_000_000_000,
    )
    report = tmp_path / "report.json"
    write_performance_report(report, [record])
    text = report.read_text(encoding="utf-8")
    data = json.loads(text)
    assert secret_prompt not in text
    assert "prompt_text" not in text
    assert "generated_text" not in text
    assert data["records"][0]["backend"] == "vulkan"
    assert data["records"][0]["tokens_per_second"] == 6.4
