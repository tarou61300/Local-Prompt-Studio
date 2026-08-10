from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass


GIB = 1024**3


@dataclass(frozen=True, slots=True)
class MemoryInfo:
    total_bytes: int
    available_bytes: int

    @property
    def total_gib(self) -> float:
        return self.total_bytes / GIB

    @property
    def available_gib(self) -> float:
        return self.available_bytes / GIB


@dataclass(frozen=True, slots=True)
class MemoryAssessment:
    context_size: int
    model_name: str
    model_filename: str
    model_size_bytes: int
    estimated_required_bytes: int
    memory: MemoryInfo | None
    warnings: tuple[str, ...]
    reported_gpu_memory_bytes: int | None = None

    @property
    def model_size_gib(self) -> float:
        return self.model_size_bytes / GIB

    @property
    def estimated_required_gib(self) -> float:
        return self.estimated_required_bytes / GIB


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def get_system_memory() -> MemoryInfo | None:
    """Return physical memory without adding a third-party dependency."""
    if os.name == "nt":
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return MemoryInfo(int(status.ullTotalPhys), int(status.ullAvailPhys))
        except (AttributeError, OSError):
            return None
        return None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
        return MemoryInfo(page_size * total_pages, page_size * available_pages)
    except (AttributeError, OSError, ValueError):
        return None


def estimate_required_memory_bytes(model_size_bytes: int, context_size: int) -> int:
    """Estimate CPU runtime RAM from the selected GGUF and context.

    llama.cpp memory usage varies by model architecture and runtime version, so this
    is deliberately a conservative heuristic rather than a model-specific promise.
    """
    model_gib = max(0.0, model_size_bytes / GIB)
    runtime_overhead_gib = max(1.0, model_gib * 0.15)
    context_8192_gib = max(0.75, model_gib * 0.18)
    context_gib = context_8192_gib * max(0.25, context_size / 8192)
    safety_margin_gib = 0.5
    return int((model_gib + runtime_overhead_gib + context_gib + safety_margin_gib) * GIB)


def assess_memory(
    context_size: int,
    *,
    model_name: str,
    model_filename: str,
    model_size_bytes: int,
    memory: MemoryInfo | None = None,
    reported_gpu_memory_bytes: int | None = None,
) -> MemoryAssessment:
    """Build a dynamic assessment for the currently selected GGUF."""
    measured = memory if memory is not None else get_system_memory()
    estimated_bytes = estimate_required_memory_bytes(model_size_bytes, context_size)
    estimated_gib = estimated_bytes / GIB
    warnings: list[str] = []

    if context_size == 4096:
        warnings.append(
            "4096（Low Memory）は、MiniMax H3 Skill全文・ユーザー入力・生成結果が"
            "同時に収まらない場合があります。可能なら8192（Recommended）を使用してください。"
        )

    if measured is not None:
        # Leave a practical allowance for Windows, Qt, and other resident software.
        recommended_total_gib = estimated_gib + max(4.0, estimated_gib * 0.4)
        if measured.total_gib < recommended_total_gib:
            warnings.append(
                f"搭載RAMは約{measured.total_gib:.1f} GBです。選択モデル「{model_name}」と"
                f"Context {context_size}の推定必要RAMは約{estimated_gib:.1f} GBで、"
                f"Windows等の使用分を含めると約{recommended_total_gib:.1f} GB以上が目安です。"
            )
        if measured.available_gib < estimated_gib:
            warnings.append(
                f"現在利用可能なRAMは約{measured.available_gib:.1f} GBです。選択モデル"
                f"「{model_name}」とContext {context_size}には約{estimated_gib:.1f} GBを"
                "目安に、他のアプリを閉じてから生成してください（概算）。"
            )

    if context_size >= 16384:
        warnings.append(
            f"Context Size {context_size}は高メモリ向けです。通常は8192（Recommended）を使用してください。"
        )

    return MemoryAssessment(
        context_size=context_size,
        model_name=model_name,
        model_filename=model_filename,
        model_size_bytes=model_size_bytes,
        estimated_required_bytes=estimated_bytes,
        memory=measured,
        warnings=tuple(warnings),
        reported_gpu_memory_bytes=reported_gpu_memory_bytes,
    )


def format_memory_status(memory: MemoryInfo | None) -> str:
    if memory is None:
        return "RAM: Available 不明 / Total 不明"
    return f"RAM: Available {memory.available_gib:.1f} GB / Total {memory.total_gib:.1f} GB"


def format_assessment_details(assessment: MemoryAssessment) -> str:
    memory = assessment.memory
    if memory is None:
        memory_lines = ["現在利用可能なRAM: 不明", "搭載RAM: 不明"]
    else:
        memory_lines = [
            f"現在利用可能なRAM: 約{memory.available_gib:.1f} GB",
            f"搭載RAM: 約{memory.total_gib:.1f} GB",
        ]
    detail_lines = memory_lines + [
            f"選択モデル: {assessment.model_name}",
            f"GGUFファイル: {assessment.model_filename}",
            f"GGUFサイズ: 約{assessment.model_size_gib:.2f} GB",
            f"Context: {assessment.context_size}",
            f"推定必要RAM: 約{assessment.estimated_required_gib:.1f} GB（概算）",
        ]
    if assessment.reported_gpu_memory_bytes is not None:
        detail_lines.append(
            "llama.cpp報告GPUメモリ: "
            f"約{assessment.reported_gpu_memory_bytes / GIB:.1f} GiB（情報表示のみ・System RAMへ加算しません）"
        )
    return "\n".join(detail_lines)


def memory_warnings(
    context_size: int,
    *,
    model_size_bytes: int,
    model_name: str = "選択中のモデル",
    model_filename: str = "",
    memory: MemoryInfo | None = None,
    reported_gpu_memory_bytes: int | None = None,
) -> list[str]:
    """Compatibility wrapper returning warning text for a selected GGUF."""
    return list(
        assess_memory(
            context_size,
            model_name=model_name,
            model_filename=model_filename,
            model_size_bytes=model_size_bytes,
            memory=memory,
            reported_gpu_memory_bytes=reported_gpu_memory_bytes,
        ).warnings
    )
