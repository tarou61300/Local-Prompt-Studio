from __future__ import annotations

from dataclasses import dataclass
import re


BACKEND_CPU = "cpu"
BACKEND_VULKAN = "vulkan"
GPU_LAYERS_AUTO = -1


@dataclass(frozen=True, slots=True)
class BackendSpec:
    backend_id: str
    display_name: str
    runtime_variant: str
    uses_gpu: bool


BACKENDS = {
    BACKEND_CPU: BackendSpec(BACKEND_CPU, "CPU", "cpu", False),
    BACKEND_VULKAN: BackendSpec(
        BACKEND_VULKAN,
        "Vulkan GPU (AMD / Intel / NVIDIA)",
        "vulkan",
        True,
    ),
}


def normalize_backend_id(value: str) -> str:
    normalized = str(value).strip().lower()
    aliases = {
        "cpu": BACKEND_CPU,
        "vulkan": BACKEND_VULKAN,
        "vulkan gpu": BACKEND_VULKAN,
    }
    return aliases.get(normalized, BACKEND_CPU)


def backend_spec(value: str) -> BackendSpec:
    return BACKENDS[normalize_backend_id(value)]


@dataclass(frozen=True, slots=True)
class BackendDevice:
    identifier: str
    name: str
    is_uma: bool | None = None
    reported_memory_bytes: int | None = None
    reported_free_bytes: int | None = None

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.identifier})"

    @property
    def memory_classification(self) -> str:
        if self.is_uma is True:
            return "uma"
        if self.is_uma is False:
            return "discrete"
        return "unknown"


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_VULKAN_DEVICE = re.compile(
    r"^\s*(?P<identifier>Vulkan(?P<index>\d+))\s*:\s*(?P<description>.+?)\s*$",
    re.IGNORECASE,
)
_VULKAN_DETAIL = re.compile(
    r"^\s*(?:ggml_vulkan:\s*)?(?P<index>\d+)\s*=\s*(?P<name>[^|]+?)"
    r"(?:\s*\|\s*(?P<features>.*))?$",
    re.IGNORECASE,
)
_MEMORY_SUFFIX = re.compile(
    r"\s*\((?P<total>\d+(?:\.\d+)?)\s*(?P<unit>[GM]i?B)"
    r"(?:\s*,\s*(?P<free>\d+(?:\.\d+)?)\s*[GM]i?B\s+free)?\)\s*$",
    re.IGNORECASE,
)


def _safe_device_name(value: str) -> str:
    cleaned = " ".join(value.replace("\x00", "").split())
    return cleaned[:200] or "名称不明のVulkanデバイス"


def _memory_bytes(value: str, unit: str) -> int:
    factor = 1024**3 if unit.lower().startswith("g") else 1024**2
    return int(float(value) * factor)


def parse_vulkan_devices(output: str) -> list[BackendDevice]:
    """Parse only llama.cpp Vulkan device output, never Windows adapter names."""
    text = _ANSI_ESCAPE.sub("", output)
    details: dict[int, tuple[str, bool | None]] = {}
    devices: dict[int, BackendDevice] = {}

    for line in text.splitlines():
        detail = _VULKAN_DETAIL.match(line)
        if detail is not None:
            index = int(detail.group("index"))
            features = detail.group("features") or ""
            uma_match = re.search(r"(?:^|\|)\s*uma\s*:\s*([01])(?:\s*\||$)", features, re.I)
            is_uma = None if uma_match is None else uma_match.group(1) == "1"
            details[index] = (_safe_device_name(detail.group("name")), is_uma)

        listed = _VULKAN_DEVICE.match(line)
        if listed is None:
            continue
        index = int(listed.group("index"))
        description = listed.group("description")
        total_bytes: int | None = None
        free_bytes: int | None = None
        memory_match = _MEMORY_SUFFIX.search(description)
        if memory_match is not None:
            total_bytes = _memory_bytes(memory_match.group("total"), memory_match.group("unit"))
            if memory_match.group("free") is not None:
                free_bytes = _memory_bytes(memory_match.group("free"), memory_match.group("unit"))
            description = description[: memory_match.start()]
        detail_name, is_uma = details.get(index, ("", None))
        name = _safe_device_name(description) if description.strip() else detail_name
        devices[index] = BackendDevice(
            identifier=f"Vulkan{index}",
            name=name,
            is_uma=is_uma,
            reported_memory_bytes=total_bytes,
            reported_free_bytes=free_bytes,
        )

    # Some builds emit the ggml_vulkan detail lines without a separate list section.
    for index, (name, is_uma) in details.items():
        devices.setdefault(index, BackendDevice(f"Vulkan{index}", name, is_uma))
    return [devices[index] for index in sorted(devices)]
