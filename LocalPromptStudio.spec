# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

import PySide6


project_root = Path(SPECPATH).resolve()
pyside_root = Path(PySide6.__file__).resolve().parent
vc_runtime_names = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
vc_runtime_binaries = []
for runtime_name in vc_runtime_names:
    source = pyside_root / runtime_name
    if not source.is_file():
        raise SystemExit(f"Required Visual C++ runtime DLL is missing: {source}")
    vc_runtime_binaries.extend(
        [
            (str(source), "runtime/cpu"),
            (str(source), "runtime/vulkan"),
        ]
    )

runtime_metadata = (
    "LLAMA_CPP_VERSION_PIN.txt",
    "LLAMA_CPP_COMMIT_PIN.txt",
    "LLAMA_CPP_CPU_SHA256_PIN.txt",
    "LLAMA_CPP_VULKAN_SHA256_PIN.txt",
    "README.md",
)

a = Analysis(
    [str(project_root / "src" / "main.py")],
    pathex=[str(project_root / "src")],
    binaries=vc_runtime_binaries,
    datas=[
        (str(project_root / "runtime" / "cpu"), "runtime/cpu"),
        (str(project_root / "runtime" / "vulkan"), "runtime/vulkan"),
        *[
            (str(project_root / "runtime" / filename), "runtime")
            for filename in runtime_metadata
        ],
        (str(project_root / "locales"), "locales"),
        (str(project_root / "profiles"), "profiles"),
        (str(project_root / "LICENSE"), "."),
        (str(project_root / "THIRD_PARTY_LICENSES.md"), "."),
        (str(project_root / "README.md"), "."),
        (str(project_root / "CHANGELOG.md"), "."),
        (str(project_root / "VERSION"), "."),
        (str(project_root / "licenses" / "llama.cpp-LICENSE.txt"), "licenses"),
        (str(project_root / "licenses" / "LGPL-3.0.txt"), "licenses"),
        (str(project_root / "licenses" / "LLVM-LICENSE.txt"), "licenses"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalPromptStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
    version=str(project_root / "packaging" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LocalPromptStudio",
)
