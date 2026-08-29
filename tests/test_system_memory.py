from __future__ import annotations

from pathlib import Path

import core.system_memory as system_memory
from core.localization import Localization
from core.system_memory import (
    GIB,
    MemoryInfo,
    assess_memory,
    estimate_required_memory_bytes,
    format_assessment_details,
    format_memory_status,
    memory_warnings,
)


ROOT = Path(__file__).resolve().parents[1]


def test_low_total_and_available_memory_warnings():
    memory = MemoryInfo(total_bytes=int(8.0 * GIB), available_bytes=int(3.0 * GIB))
    warnings = memory_warnings(
        8192,
        model_name="Example 5B Q4",
        model_filename="example-5b-q4.gguf",
        model_size_bytes=int(5.03 * GIB),
        memory=memory,
    )
    joined = " ".join(warnings)
    assert "搭載RAM" in joined
    assert "現在利用可能なRAM" in joined
    assert "Example 5B Q4" in joined


def test_large_context_always_explains_high_memory_mode():
    memory = MemoryInfo(total_bytes=64 * GIB, available_bytes=48 * GIB)
    warnings = memory_warnings(32768, model_size_bytes=int(5.03 * GIB), memory=memory)
    assert any("高メモリ向け" in warning for warning in warnings)


def test_recommended_context_on_roomy_system_has_no_warning():
    memory = MemoryInfo(total_bytes=32 * GIB, available_bytes=24 * GIB)
    assert memory_warnings(8192, model_size_bytes=int(5.03 * GIB), memory=memory) == []


def test_low_memory_context_warns_skill_may_not_fit():
    memory = MemoryInfo(total_bytes=32 * GIB, available_bytes=24 * GIB)
    warnings = memory_warnings(4096, model_size_bytes=int(2.5 * GIB), memory=memory)
    assert any("Skill全文" in warning and "8192" in warning for warning in warnings)


def test_warning_changes_with_selected_gguf_instead_of_retaining_8b_name():
    memory = MemoryInfo(total_bytes=int(6 * GIB), available_bytes=int(1 * GIB))
    first = assess_memory(
        8192,
        model_name="Qwen3-8B Q4_K_M",
        model_filename="Qwen3-8B-Q4_K_M.gguf",
        model_size_bytes=int(5.0 * GIB),
        memory=memory,
    )
    second = assess_memory(
        8192,
        model_name="Qwen3-4B Q4_K_M",
        model_filename="Qwen3-4B-Q4_K_M.gguf",
        model_size_bytes=int(2.5 * GIB),
        memory=memory,
    )
    assert "Qwen3-8B Q4_K_M" in " ".join(first.warnings)
    assert "Qwen3-4B Q4_K_M" in " ".join(second.warnings)
    assert "Qwen3-8B" not in " ".join(second.warnings)


def test_runtime_warning_source_has_no_hard_coded_qwen3_8b():
    source = Path(system_memory.__file__).read_text(encoding="utf-8")
    assert "Qwen3-8B" not in source


def test_total_and_available_ram_are_formatted_as_separate_values():
    memory = MemoryInfo(total_bytes=int(12.7 * GIB), available_bytes=int(5.3 * GIB))
    text = format_memory_status(memory)
    assert text == "RAM: Available 5.3 GB / Total 12.7 GB"


def test_memory_status_details_and_warnings_use_selected_ui_locale():
    tr = Localization(ROOT / "locales", "zh-CN").tr
    memory = MemoryInfo(total_bytes=int(8.0 * GIB), available_bytes=int(3.0 * GIB))
    assessment = assess_memory(
        8192,
        model_name="Synthetic Model",
        model_filename="synthetic.gguf",
        model_size_bytes=int(5.0 * GIB),
        memory=memory,
        tr=tr,
    )

    assert format_memory_status(memory, tr) == "RAM：可用3.0 GB / 总计8.0 GB"
    details = format_assessment_details(assessment, tr)
    assert "当前可用RAM" in details
    assert "所选模型：Synthetic Model" in details
    assert assessment.warnings
    assert all("搭載RAM" not in warning for warning in assessment.warnings)


def test_estimate_is_derived_from_model_size_and_context():
    small_model = estimate_required_memory_bytes(int(2.5 * GIB), 8192)
    large_model = estimate_required_memory_bytes(int(5.0 * GIB), 8192)
    large_context = estimate_required_memory_bytes(int(2.5 * GIB), 16384)
    assert small_model < large_model
    assert small_model < large_context


def test_reported_gpu_memory_is_informational_and_not_double_counted():
    memory = MemoryInfo(total_bytes=int(12.7 * GIB), available_bytes=int(5.3 * GIB))
    without_gpu = assess_memory(
        8192,
        model_name="Qwen3-4B Q4_K_M",
        model_filename="qwen3-4b-q4_k_m.gguf",
        model_size_bytes=int(2.33 * GIB),
        memory=memory,
    )
    with_reported_gpu = assess_memory(
        8192,
        model_name="Qwen3-4B Q4_K_M",
        model_filename="qwen3-4b-q4_k_m.gguf",
        model_size_bytes=int(2.33 * GIB),
        memory=memory,
        reported_gpu_memory_bytes=int(9.4 * GIB),
    )
    assert with_reported_gpu.estimated_required_bytes == without_gpu.estimated_required_bytes
    assert with_reported_gpu.warnings == without_gpu.warnings
    assert "情報表示のみ・System RAMへ加算しません" in format_assessment_details(with_reported_gpu)
