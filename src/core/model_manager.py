from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


RECOMMENDED_MODEL_MARKERS = ("qwen3-8b", "q4_k_m")


@dataclass(frozen=True, slots=True)
class ModelInfo:
    path: Path
    filename: str
    display_name: str
    size_bytes: int
    exists: bool
    is_recommended: bool

    @property
    def size_gib(self) -> float:
        return self.size_bytes / (1024**3)


class ModelValidationError(ValueError):
    pass


_QWEN_GGUF_NAME = re.compile(
    r"^qwen(?P<generation>\d+(?:\.\d+)?)-"
    r"(?P<parameters>\d+(?:\.\d+)?b)[-_]"
    r"(?P<quant>q\d+(?:_[a-z0-9]+)+)$",
    re.IGNORECASE,
)


def friendly_model_name(filename: str) -> str:
    """Normalize only model names whose filename structure is unambiguous."""
    name = Path(filename).name
    stem = Path(name).stem
    match = _QWEN_GGUF_NAME.fullmatch(stem)
    if match is None:
        return name
    generation = match.group("generation")
    parameters = match.group("parameters").upper()
    quant = match.group("quant").upper()
    return f"Qwen{generation}-{parameters} {quant}"


def inspect_model(path: str | Path) -> ModelInfo:
    model_path = Path(path).expanduser()
    exists = model_path.is_file()
    filename_lower = model_path.name.lower()
    return ModelInfo(
        path=model_path.resolve(strict=False),
        filename=model_path.name,
        display_name=friendly_model_name(model_path.name),
        size_bytes=model_path.stat().st_size if exists else 0,
        exists=exists,
        is_recommended=all(marker in filename_lower for marker in RECOMMENDED_MODEL_MARKERS),
    )


def validate_model(path: str | Path) -> ModelInfo:
    if not str(path).strip():
        raise ModelValidationError("LLMモデルが設定されていません。")
    info = inspect_model(path)
    if not info.exists:
        raise ModelValidationError("指定されたGGUFファイルが見つかりません。")
    if info.path.suffix.lower() != ".gguf":
        raise ModelValidationError("GGUF形式（.gguf）のモデルを選択してください。")
    return info
