from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
APPLICATION_REQUIRED_FILES = (
    "LocalPromptStudio.exe",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_LICENSES.md",
    "CHANGELOG.md",
    "VERSION",
    "data/README.txt",
    "_internal/locales/en-US.json",
    "_internal/locales/ja-JP.json",
    "_internal/locales/zh-CN.json",
    "_internal/profiles/video/minimax_h3/manifest.json",
    "_internal/profiles/video/minimax_h3/instructions.md",
    "_internal/profiles/video/minimax_h3/variants/base.json",
    "_internal/profiles/video/wan_2_2/manifest.json",
    "_internal/profiles/video/wan_2_2/instructions.md",
    "_internal/profiles/video/wan_2_2/variants/a14b.json",
    "_internal/profiles/video/ltx_2_3/manifest.json",
    "_internal/profiles/video/ltx_2_3/instructions.md",
    "_internal/profiles/video/ltx_2_3/variants/dev.json",
    "_internal/profiles/video/ltx_2_3/variants/distilled_1_1.json",
    "_internal/profiles/image/krea_2/manifest.json",
    "_internal/profiles/image/krea_2/instructions.md",
    "_internal/profiles/image/krea_2/variants/raw.json",
    "_internal/profiles/image/krea_2/variants/turbo.json",
    "_internal/profiles/image/anima/manifest.json",
    "_internal/profiles/image/anima/instructions.md",
    "_internal/profiles/image/anima/variants/base_v1_0.json",
    "_internal/profiles/image/anima/variants/aesthetic_v1_1.json",
    "_internal/profiles/image/anima/variants/turbo_v1_0.json",
    "licenses/llama.cpp-LICENSE.txt",
    "licenses/LGPL-3.0.txt",
    "licenses/LLVM-LICENSE.txt",
    "licenses/Python-LICENSE.txt",
    "licenses/PyInstaller-COPYING.txt",
    "_internal/runtime/cpu/llama-server.exe",
    "_internal/runtime/vulkan/llama-server.exe",
)
BRIDGE_REQUIRED_FILES = (
    "ComfyUI-Bridge/MMH3PromptBridge/__init__.py",
    "ComfyUI-Bridge/MMH3PromptBridge/js/mmh3_bridge.js",
    "ComfyUI-Bridge/MMH3PromptBridge/README.md",
    "ComfyUI-Bridge/MMH3PromptBridge/LICENSE",
)
COMBINED_REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "THIRD_PARTY_LICENSES.md",
    "CHANGELOG.md",
    "VERSION",
    *BRIDGE_REQUIRED_FILES,
)
ALLOWED_BRIDGE_FILES = set(BRIDGE_REQUIRED_FILES)
REQUIRED_RUNTIME_DLLS = (
    "msvcp140.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
)
BANNED_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "__pycache__",
    ".tmp",
    ".dev-data",
    "prompt_library_datasets",
    "tests",
    "scripts",
    "packaging",
}
BANNED_SUFFIXES = {".gguf", ".log"}
BANNED_FILENAMES = {
    "config.json",
    "history.sqlite3",
    "prompt_library.sqlite3",
    "prompt_library.sqlite3-wal",
    "prompt_library.sqlite3-shm",
    "prompt_library_datasets.json",
    "comfyui_credentials.dat",
    "bridge.json",
    ".gitkeep",
}


def _development_path_markers() -> tuple[str, ...]:
    candidates = {PROJECT_ROOT, PROJECT_ROOT.parent, Path.home()}
    for variable in ("USERPROFILE", "HOME"):
        if value := os.environ.get(variable):
            candidates.add(Path(value))
    markers: set[str] = set()
    for candidate in candidates:
        value = str(candidate.resolve())
        markers.update((value, value.replace("\\", "/"), value.replace("/", "\\")))
    return tuple(sorted((value for value in markers if len(value) >= 4), key=len, reverse=True))


BANNED_TEXT = _development_path_markers()
BANNED_BINARY_MARKERS = tuple(
    marker
    for value in BANNED_TEXT
    for marker in (value.lower().encode("utf-8"), value.lower().encode("utf-16le"))
)
TEXT_SUFFIXES = {".txt", ".md", ".json", ".ini", ".xml", ".url"}


def audit_release(root: Path) -> dict[str, int | str]:
    root = root.resolve()
    errors: list[str] = []
    combined_distribution = (root / "LocalPromptStudio" / "LocalPromptStudio.exe").is_file()
    application_root = root / "LocalPromptStudio" if combined_distribution else root
    if combined_distribution:
        required_files = [
            *(f"LocalPromptStudio/{relative}" for relative in APPLICATION_REQUIRED_FILES),
            *COMBINED_REQUIRED_FILES,
        ]
    else:
        required_files = [*APPLICATION_REQUIRED_FILES, *BRIDGE_REQUIRED_FILES]
    for relative in required_files:
        if not (root / relative).is_file():
            errors.append(f"required file missing: {relative}")
    version_files = [root / "VERSION"]
    if combined_distribution:
        version_files.append(application_root / "VERSION")
    for version_file in version_files:
        if (
            version_file.is_file()
            and version_file.read_text(encoding="utf-8-sig").strip() != VERSION
        ):
            errors.append(f"VERSION is not {VERSION}: {version_file.relative_to(root)}")

    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        relative = path.relative_to(root)
        relative_posix = relative.as_posix()
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & BANNED_PARTS:
            errors.append(f"development path included: {relative}")
        if path.suffix.lower() in BANNED_SUFFIXES:
            errors.append(f"banned file type included: {relative}")
        if path.suffix.lower() == ".py" and relative_posix not in ALLOWED_BRIDGE_FILES:
            errors.append(f"unexpected Python source included: {relative}")
        if path.name.lower() in BANNED_FILENAMES:
            errors.append(f"user/development file included: {relative}")
        if path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 5_000_000:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                continue
            for marker in BANNED_TEXT:
                if marker.lower() in text.lower():
                    errors.append(f"workspace/user absolute path leaked into: {relative}")
        longest_marker = max(len(marker) for marker in BANNED_BINARY_MARKERS)
        overlap = b""
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                searchable = (overlap + chunk).lower()
                if any(marker in searchable for marker in BANNED_BINARY_MARKERS):
                    errors.append(f"workspace/user path bytes leaked into: {relative}")
                    break
                overlap = searchable[-longest_marker:]

    bridge_root = root / "ComfyUI-Bridge" / "MMH3PromptBridge"
    bridge_files = {
        path.relative_to(root).as_posix()
        for path in bridge_root.rglob("*")
        if path.is_file()
    }
    unexpected_bridge_files = bridge_files - ALLOWED_BRIDGE_FILES
    if unexpected_bridge_files:
        errors.extend(
            f"unexpected Bridge file included: {relative}"
            for relative in sorted(unexpected_bridge_files)
        )
    if (bridge_root / "data").exists():
        errors.append("Bridge runtime data directory is included")

    for variant in ("cpu", "vulkan"):
        runtime = application_root / "_internal" / "runtime" / variant
        for dll_name in REQUIRED_RUNTIME_DLLS:
            if not (runtime / dll_name).is_file():
                errors.append(f"{variant} dependency missing: {dll_name}")

    skill_files = [path for path in files if path.name == "SKILL.md"]
    if skill_files:
        errors.append("MiniMax Skill content is included")
    if errors:
        raise RuntimeError("Release audit failed:\n- " + "\n- ".join(errors))
    return {
        "root": str(root),
        "application_root": str(application_root),
        "distribution_type": "combined-community-test" if combined_distribution else "portable-app",
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_release(args.release_root), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
