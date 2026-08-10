from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PerformanceRecord:
    recorded_at: str
    backend: str
    device: str | None
    model_filename: str
    model_size_bytes: int
    context_size: int
    gpu_layers: str | int
    model_load_seconds: float | None
    request_seconds: float
    prompt_processing_seconds: float | None
    generation_seconds: float | None
    input_tokens: int
    output_budget_tokens: int
    generated_tokens: int | None
    tokens_per_second: float | None
    ram_available_before_bytes: int | None
    ram_available_after_bytes: int | None
    ram_total_bytes: int | None

    @classmethod
    def now(cls, **values) -> "PerformanceRecord":
        return cls(recorded_at=datetime.now(timezone.utc).isoformat(), **values)


def write_performance_report(path: Path, records: list[PerformanceRecord]) -> None:
    """Write performance metadata only; prompt and generated text are not accepted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "privacy": "Prompt and generated text are not recorded.",
        "records": [asdict(record) for record in records],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
