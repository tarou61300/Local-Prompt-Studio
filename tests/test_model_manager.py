from __future__ import annotations

import pytest

from core.model_manager import ModelValidationError, friendly_model_name, inspect_model, validate_model


def test_gguf_validation_and_recommendation(tmp_path):
    model = tmp_path / "Qwen3-8B-Q4_K_M.gguf"
    model.write_bytes(b"GGUF-test")
    info = validate_model(model)
    assert info.exists
    assert info.is_recommended
    assert info.path == model.resolve()
    assert info.display_name == "Qwen3-8B Q4_K_M"


def test_model_name_normalization_is_safe_and_filename_is_fallback(tmp_path):
    model = tmp_path / "qwen3-4b-q4_k_m.gguf"
    model.write_bytes(b"123456")
    info = inspect_model(model)
    assert info.display_name == "Qwen3-4B Q4_K_M"
    assert info.filename == "qwen3-4b-q4_k_m.gguf"
    assert info.size_bytes == 6
    assert friendly_model_name("custom-possibly-qwen.gguf") == "custom-possibly-qwen.gguf"


def test_invalid_model_paths(tmp_path):
    with pytest.raises(ModelValidationError, match="設定されていません"):
        validate_model("")
    with pytest.raises(ModelValidationError, match="見つかりません"):
        validate_model(tmp_path / "missing.gguf")
    wrong = tmp_path / "model.bin"
    wrong.write_bytes(b"x")
    with pytest.raises(ModelValidationError, match="GGUF形式"):
        validate_model(wrong)


def test_model_path_supports_japanese_and_spaces(tmp_path):
    model = tmp_path / "日本語 モデル フォルダ" / "Qwen3-4B-Q4_K_M.gguf"
    model.parent.mkdir()
    model.write_bytes(b"GGUF")
    info = validate_model(model)
    assert info.path == model.resolve()
    assert info.display_name == "Qwen3-4B Q4_K_M"
