from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from .profile_models import (
    LengthGuidance,
    LoadedProfile,
    ProfileCatalog,
    ProfileLoadError,
    ProfileManifest,
    ProfileSource,
    ProfileVariant,
    PromptComponents,
)
from .renderers import RendererRegistry


PROFILE_INVALID = "PROFILE_INVALID"
PROFILE_DUPLICATE_ID = "PROFILE_DUPLICATE_ID"
PROFILE_UNSUPPORTED_SCHEMA = "PROFILE_UNSUPPORTED_SCHEMA"
PROFILE_UNSAFE_PATH = "PROFILE_UNSAFE_PATH"
PROFILE_UNKNOWN_RENDERER = "PROFILE_UNKNOWN_RENDERER"

_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_LANGUAGE = re.compile(r"^[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*$")
_TASK = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,31}$")
_SOURCE_TYPES = {
    "official_documentation",
    "official_model_card",
    "studio",
    "community",
    "custom",
}
_ALLOWED_SUFFIXES = {".json", ".md", ".txt"}
_LOGGER = logging.getLogger(__name__)


class ProfileValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _object(value: Any, code: str = PROFILE_INVALID) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProfileValidationError(code)
    return value


def _safe_relative(root: Path, value: str) -> Path:
    relative = Path(value)
    if (
        not value
        or relative.is_absolute()
        or relative.suffix.lower() not in _ALLOWED_SUFFIXES
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ProfileValidationError(PROFILE_UNSAFE_PATH)
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ProfileValidationError(PROFILE_UNSAFE_PATH) from exc
    return resolved


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProfileValidationError(PROFILE_INVALID)
    return tuple(value)


def _sources(value: Any) -> tuple[ProfileSource, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProfileValidationError(PROFILE_INVALID)
    sources: list[ProfileSource] = []
    for item in value:
        raw = _object(item)
        source_type = raw.get("type")
        url = raw.get("url")
        verified_at = raw.get("verified_at")
        if source_type not in _SOURCE_TYPES:
            raise ProfileValidationError(PROFILE_INVALID)
        if url is not None and (not isinstance(url, str) or not url.startswith(("https://", "http://"))):
            raise ProfileValidationError(PROFILE_INVALID)
        if verified_at is not None and not isinstance(verified_at, str):
            raise ProfileValidationError(PROFILE_INVALID)
        sources.append(ProfileSource(source_type, url, verified_at))
    return tuple(sources)


def _components(value: Any) -> PromptComponents:
    raw = _object(value or {})
    allowed = {
        "positive_prefix",
        "positive_suffix",
        "negative_prefix",
        "negative_suffix",
    }
    if set(raw) - allowed:
        raise ProfileValidationError(PROFILE_INVALID)
    return PromptComponents(
        positive_prefix=_strings(raw.get("positive_prefix")),
        positive_suffix=_strings(raw.get("positive_suffix")),
        negative_prefix=_strings(raw.get("negative_prefix")),
        negative_suffix=_strings(raw.get("negative_suffix")),
    )


def _dependencies(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProfileValidationError(PROFILE_INVALID)
    dependencies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        raw = _object(item)
        if set(raw) - {"id", "kind", "required", "bundled"}:
            raise ProfileValidationError(PROFILE_INVALID)
        dependency_id = raw.get("id")
        kind = raw.get("kind")
        required = raw.get("required")
        bundled = raw.get("bundled")
        if (
            not isinstance(dependency_id, str)
            or _ID.fullmatch(dependency_id) is None
            or dependency_id in seen
            or not isinstance(kind, str)
            or _ID.fullmatch(kind) is None
            or not isinstance(required, bool)
            or not isinstance(bundled, bool)
        ):
            raise ProfileValidationError(PROFILE_INVALID)
        dependencies.append(dict(raw))
        seen.add(dependency_id)
    return tuple(dependencies)


def _length_guidance(value: Any) -> LengthGuidance:
    raw = _object(value or {})
    allowed = {
        "unit",
        "recommended_minimum",
        "recommended_maximum",
        "hard_maximum",
        "source_level",
        "source_reference",
    }
    if set(raw) - allowed:
        raise ProfileValidationError(PROFILE_INVALID)
    unit = raw.get("unit")
    if unit is not None and unit not in {"words", "tokens", "tags"}:
        raise ProfileValidationError(PROFILE_INVALID)
    numbers: dict[str, int | None] = {}
    for key in ("recommended_minimum", "recommended_maximum", "hard_maximum"):
        number = raw.get(key)
        if number is not None and (not isinstance(number, int) or number < 0):
            raise ProfileValidationError(PROFILE_INVALID)
        numbers[key] = number
    if (
        numbers["recommended_minimum"] is not None
        and numbers["recommended_maximum"] is not None
        and numbers["recommended_minimum"] > numbers["recommended_maximum"]
    ):
        raise ProfileValidationError(PROFILE_INVALID)
    source_level = raw.get("source_level")
    if source_level is not None and source_level not in {
        "official",
        "studio",
        "community",
        "custom",
    }:
        raise ProfileValidationError(PROFILE_INVALID)
    reference = raw.get("source_reference")
    if reference is not None and not isinstance(reference, str):
        raise ProfileValidationError(PROFILE_INVALID)
    return LengthGuidance(
        unit=unit,
        source_level=source_level,
        source_reference=reference,
        **numbers,
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")))
    except ProfileValidationError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ProfileValidationError(PROFILE_INVALID) from exc


def _load_variant(root: Path, path: Path) -> ProfileVariant:
    raw = _load_json(path)
    allowed = {
        "id",
        "name",
        "target_model_version",
        "required_prompt",
        "recommended_prompt",
        "optional_prompt",
        "length_guidance",
        "inference_recommendations",
        "sources",
    }
    if set(raw) - allowed:
        raise ProfileValidationError(PROFILE_INVALID)
    variant_id = raw.get("id")
    name = raw.get("name")
    target_model_version = raw.get("target_model_version")
    if not isinstance(variant_id, str) or _ID.fullmatch(variant_id) is None:
        raise ProfileValidationError(PROFILE_INVALID)
    if path.stem != variant_id:
        raise ProfileValidationError(PROFILE_INVALID)
    if not isinstance(name, str) or not name.strip():
        raise ProfileValidationError(PROFILE_INVALID)
    if target_model_version is not None and not isinstance(target_model_version, str):
        raise ProfileValidationError(PROFILE_INVALID)
    recommendations = raw.get("inference_recommendations", {})
    if not isinstance(recommendations, dict):
        raise ProfileValidationError(PROFILE_INVALID)
    return ProfileVariant(
        id=variant_id,
        name=name,
        target_model_version=target_model_version,
        required_prompt=_components(raw.get("required_prompt")),
        recommended_prompt=_components(raw.get("recommended_prompt")),
        optional_prompt=_components(raw.get("optional_prompt")),
        length_guidance=_length_guidance(raw.get("length_guidance")),
        inference_recommendations=recommendations,
        sources=_sources(raw.get("sources")),
        path=path,
    )


def load_profile(root: Path, layer: str, renderer_registry: RendererRegistry) -> LoadedProfile:
    if root.is_symlink() or root.parent.is_symlink():
        raise ProfileValidationError(PROFILE_UNSAFE_PATH)
    root = root.resolve()
    try:
        profile_files = tuple(path for path in root.rglob("*") if path.is_file())
    except OSError as exc:
        raise ProfileValidationError(PROFILE_INVALID) from exc
    if any(path.is_symlink() or path.suffix.lower() not in _ALLOWED_SUFFIXES for path in profile_files):
        raise ProfileValidationError(PROFILE_UNSAFE_PATH)
    manifest_path = root / "manifest.json"
    raw = _load_json(manifest_path)
    allowed_manifest = {
        "schema_version",
        "id",
        "name",
        "profile_version",
        "category",
        "renderer",
        "output_language",
        "default_variant",
        "supported_tasks",
        "capabilities",
        "description_key",
        "instructions_file",
        "external_dependencies",
        "sources",
    }
    if set(raw) - allowed_manifest:
        raise ProfileValidationError(PROFILE_INVALID)
    if raw.get("schema_version") != 1:
        raise ProfileValidationError(PROFILE_UNSUPPORTED_SCHEMA)
    profile_id = raw.get("id")
    name = raw.get("name")
    profile_version = raw.get("profile_version")
    category = raw.get("category")
    renderer = raw.get("renderer")
    output_language = raw.get("output_language")
    default_variant = raw.get("default_variant")
    tasks = raw.get("supported_tasks")
    capabilities = raw.get("capabilities")
    if not isinstance(profile_id, str) or _ID.fullmatch(profile_id) is None:
        raise ProfileValidationError(PROFILE_INVALID)
    if not isinstance(name, str) or not name.strip():
        raise ProfileValidationError(PROFILE_INVALID)
    if not isinstance(profile_version, str) or _VERSION.fullmatch(profile_version) is None:
        raise ProfileValidationError(PROFILE_INVALID)
    if category not in {"image", "video"}:
        raise ProfileValidationError(PROFILE_INVALID)
    if renderer not in renderer_registry.ids:
        raise ProfileValidationError(PROFILE_UNKNOWN_RENDERER)
    if not isinstance(output_language, str) or _LANGUAGE.fullmatch(output_language) is None:
        raise ProfileValidationError(PROFILE_INVALID)
    if not isinstance(default_variant, str) or _ID.fullmatch(default_variant) is None:
        raise ProfileValidationError(PROFILE_INVALID)
    if not isinstance(tasks, list) or not tasks or not all(
        isinstance(task, str) and _TASK.fullmatch(task) for task in tasks
    ):
        raise ProfileValidationError(PROFILE_INVALID)
    if len(set(tasks)) != len(tasks) or not isinstance(capabilities, dict):
        raise ProfileValidationError(PROFILE_INVALID)
    description_key = raw.get("description_key")
    if description_key is not None and not isinstance(description_key, str):
        raise ProfileValidationError(PROFILE_INVALID)
    instructions_file = raw.get("instructions_file", "instructions.md")
    if not isinstance(instructions_file, str):
        raise ProfileValidationError(PROFILE_UNSAFE_PATH)
    instructions_path = _safe_relative(root, instructions_file)
    if not instructions_path.is_file():
        raise ProfileValidationError(PROFILE_INVALID)
    variants_root = root / "variants"
    variants: dict[str, ProfileVariant] = {}
    try:
        variant_paths = sorted(variants_root.glob("*.json"))
    except OSError as exc:
        raise ProfileValidationError(PROFILE_INVALID) from exc
    for path in variant_paths:
        variant = _load_variant(root, path)
        if variant.id in variants:
            raise ProfileValidationError(PROFILE_DUPLICATE_ID)
        variants[variant.id] = variant
    if default_variant not in variants:
        raise ProfileValidationError(PROFILE_INVALID)
    dependencies = _dependencies(raw.get("external_dependencies"))
    manifest = ProfileManifest(
        schema_version=1,
        id=profile_id,
        name=name,
        profile_version=profile_version,
        category=category,
        renderer=renderer,
        output_language=output_language,
        default_variant=default_variant,
        supported_tasks=tuple(tasks),
        capabilities=capabilities,
        description_key=description_key,
        instructions_file=instructions_file,
        external_dependencies=dependencies,
        sources=_sources(raw.get("sources")),
    )
    digest = hashlib.sha256()
    for path in (manifest_path, instructions_path, *sorted(variants_root.glob("*.json"))):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return LoadedProfile(
        manifest=manifest,
        instructions=instructions_path.read_text(encoding="utf-8"),
        variants=variants,
        root=root,
        layer=layer,
        content_hash=digest.hexdigest(),
    )


class ProfileLoader:
    """Discover non-executable profile data with official-over-builtin precedence."""

    def __init__(
        self,
        builtin_root: Path | str,
        data_dir: Path | str,
        renderer_registry: RendererRegistry | None = None,
    ) -> None:
        self.builtin_root = Path(builtin_root)
        self.data_dir = Path(data_dir)
        self.official_root = self.data_dir / "profiles" / "official"
        self.custom_root = self.data_dir / "profiles" / "custom"
        self.renderer_registry = renderer_registry or RendererRegistry()

    @staticmethod
    def _directories(root: Path) -> tuple[Path, ...]:
        if not root.is_dir():
            return ()
        return tuple(
            path
            for path in sorted(root.glob("*/*"))
            if path.is_dir() and (path / "manifest.json").is_file()
        )

    def discover(self) -> ProfileCatalog:
        catalog = ProfileCatalog()
        for layer, root in (("builtin", self.builtin_root), ("official", self.official_root)):
            if root.is_symlink():
                catalog.errors.append(ProfileLoadError(PROFILE_UNSAFE_PATH, layer))
                continue
            for directory in self._directories(root):
                self._add(catalog, directory, layer, custom=False)
        if self.custom_root.is_symlink():
            catalog.errors.append(ProfileLoadError(PROFILE_UNSAFE_PATH, "custom"))
        else:
            for directory in self._directories(self.custom_root):
                self._add(catalog, directory, "custom", custom=True)
        return catalog

    def _add(self, catalog: ProfileCatalog, directory: Path, layer: str, custom: bool) -> None:
        try:
            profile = load_profile(directory, layer, self.renderer_registry)
            destination = catalog.custom_profiles if custom else catalog.profiles
            if profile.manifest.id in destination:
                existing = destination[profile.manifest.id]
                if layer == "official" and not custom and existing.layer == "builtin":
                    destination[profile.manifest.id] = profile
                    return
                raise ProfileValidationError(PROFILE_DUPLICATE_ID)
            if custom and profile.manifest.id in catalog.profiles:
                raise ProfileValidationError(PROFILE_DUPLICATE_ID)
            destination[profile.manifest.id] = profile
        except ProfileValidationError as exc:
            catalog.errors.append(ProfileLoadError(exc.code, layer))
            _LOGGER.warning(
                "Profile rejected",
                extra={"error_code": exc.code, "profile_layer": layer},
            )
