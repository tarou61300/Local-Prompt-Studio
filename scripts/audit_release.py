from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()
REQUIRED_FILES = (
    "MMH3PromptBuilder.exe",
    "README.md",
    "LICENSE",
    "THIRD_PARTY_LICENSES.md",
    "CHANGELOG.md",
    "VERSION",
    "data/README.txt",
    "licenses/llama.cpp-LICENSE.txt",
    "licenses/LGPL-3.0.txt",
    "licenses/LLVM-LICENSE.txt",
    "licenses/Python-LICENSE.txt",
    "licenses/PyInstaller-COPYING.txt",
    "_internal/runtime/cpu/llama-server.exe",
    "_internal/runtime/vulkan/llama-server.exe",
)
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
    "tests",
    "scripts",
    "packaging",
}
BANNED_SUFFIXES = {".gguf", ".log", ".py"}
BANNED_FILENAMES = {"config.json", "history.sqlite3", ".gitkeep"}


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
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"required file missing: {relative}")
    version_file = root / "VERSION"
    if version_file.is_file() and version_file.read_text(encoding="utf-8-sig").strip() != VERSION:
        errors.append(f"VERSION is not {VERSION}")

    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        relative = path.relative_to(root)
        lowered_parts = {part.lower() for part in relative.parts}
        if lowered_parts & BANNED_PARTS:
            errors.append(f"development path included: {relative}")
        if path.suffix.lower() in BANNED_SUFFIXES:
            errors.append(f"banned file type included: {relative}")
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

    for variant in ("cpu", "vulkan"):
        runtime = root / "_internal" / "runtime" / variant
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
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_root", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_release(args.release_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
