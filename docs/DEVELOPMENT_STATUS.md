# Development status

## v1.0.0 — ready for source freeze (2026-08-10)

MMH3 Prompt Builder v1.0 is feature-frozen. CPU and Vulkan real-model generation have been
confirmed. CUDA, HIP, SYCL, new model features, image analysis, and ComfyUI integration are not
part of v1.0.0.

The validated Windows x64 release is built as a fully portable PyInstaller onedir ZIP:

- `release/MMH3-Prompt-Builder-v1.0.0-win-x64-portable.zip`
- Direct GUI launch through `MMH3PromptBuilder.exe`; no console window
- No Python, Git, pip, Visual Studio, CUDA, PATH changes, installer, service, registry settings,
  shortcuts, administrator privileges, or uninstaller required
- Application-owned configuration, logs, history, and downloaded Skill data remain under the
  extracted folder's `data` directory
- CPU and Vulkan llama.cpp b9637 runtimes are bundled
- GGUF models and the MiniMax H3 Prompt Skill are not bundled

## Runtime pins

- llama.cpp version: `b9637`
- llama.cpp commit: `aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3`
- CPU archive SHA256: `f7783c2b8c007f95e710ac40f26a24861a80b603b0b739fc54d7c926a4716c1e`
- Vulkan archive SHA256: `a353945604cffdac3d0d6da6392de78ca565a531a6f2ff3521f44b9b7c6e553f`
- Bundled backends: CPU and `Vulkan GPU (AMD / Intel / NVIDIA)`

## Verification status

- Automated test suite: 72 passed
- Twenty consecutive mock generations: covered by process-reuse and bounded-memory tests
- Prompt cache: `--cache-prompt` enabled
- Repeated generation: identical launch configuration reuses the owned llama-server process
- Cancellation and application exit: owned llama-server cleanup covered by tests
- Release audit: 323 files, no GGUF, Skill, development caches, temporary data, user configuration,
  logs, source files, or known user/workspace absolute paths
- Extracted-release smoke test: passed from a project-local path containing Japanese characters and
  spaces
- Packaged CPU and Vulkan `llama-server.exe --version`: passed
- Portable data placement and no app-specific LocalAppData writes: passed

## Release state

The packaged smoke tests and real-model regression test passed. The v1.0.0 source snapshot is ready
for its first official commit and tag, but it has not been published.

The test fixture under `tests/fixtures/skills` is not the official MiniMax Skill and is excluded from
release packages. The installed application retrieves the official Skill only after explicit user
action.
