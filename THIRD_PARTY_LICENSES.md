# Third-party software and content

MMH3 Prompt Builder v1.0.0 is distributed as a portable Windows x64 onedir
application. The corresponding license texts are included in the `licenses`
folder of the portable release.

## Python 3.12.13

- License: Python Software Foundation License
- Upstream: https://www.python.org/
- Included license: `licenses/Python-LICENSE.txt`

The portable executable contains the Python runtime. Users do not need to
install Python separately.

## PySide6 / Qt for Python / Shiboken6 6.11.1

- License used by this project: GNU Lesser General Public License v3.0
- Upstream: https://doc.qt.io/qtforpython/
- Included license: `licenses/LGPL-3.0.txt`

Qt and PySide libraries remain dynamically linked files in the PyInstaller
onedir `_internal` folder. Users may replace compatible library files subject
to the LGPL terms. MMH3 Prompt Builder does not restrict reverse engineering
for debugging modifications to those LGPL components.

## PyInstaller 6.21.0 bootloader

- License: GPL-2.0-or-later with the PyInstaller bootloader exception
- Upstream: https://pyinstaller.org/
- Included license and exception: `licenses/PyInstaller-COPYING.txt`

PyInstaller is used only to create the portable onedir distribution.

## llama.cpp b9637

- License: MIT License
- Upstream: https://github.com/ggml-org/llama.cpp
- Commit: `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3`
- Included license: `licenses/llama.cpp-LICENSE.txt`

Official Windows x64 CPU and Vulkan release assets are redistributed without
modification. Their exact asset names, source URLs, and SHA256 values are
recorded under `_internal/runtime/<variant>` and in the root runtime pin files.

## LLVM OpenMP runtime

- Component: `libomp140.x86_64.dll`, included by the official llama.cpp assets
- License: Apache License 2.0 with LLVM exceptions
- Upstream: https://llvm.org/
- Included license: `licenses/LLVM-LICENSE.txt`

## Microsoft Visual C++ runtime files

`msvcp140.dll`, `vcruntime140.dll`, and `vcruntime140_1.dll` are redistributed
with the CPU and Vulkan runtime folders so a clean Windows PC does not require
Visual Studio. They are Microsoft redistributable components and remain subject
to Microsoft's corresponding Visual Studio license terms:
https://visualstudio.microsoft.com/license-terms/

## Qwen3 model

- License: Apache License 2.0
- Recommended upstream model: https://huggingface.co/Qwen/Qwen3-8B-GGUF

No GGUF model is included, copied, or downloaded automatically by the release.
The user explicitly selects an existing local GGUF file.

## MiniMax H3 Prompt Writing Skill

- Upstream: https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing

The Skill is not included in the portable ZIP. It is downloaded from the
official MiniMax repository only after explicit user action and is stored under
the portable `data` folder. Upstream licensing terms apply.

MiniMax, H3, Qwen, Qt, Python, llama.cpp, LLVM, Microsoft, AMD, Intel, and NVIDIA
names identify their respective upstream projects. MMH3 Prompt Builder is an
unofficial community tool and claims no ownership of those names.
