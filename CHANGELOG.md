# Changelog

## 1.0.0 — 2026-08-10

- Feature Freeze: CPU and Vulkan GPU inference only for v1.0.0.
- Added the portable Windows x64 PyInstaller onedir release workflow.
- Made `data` beside the executable the default location for all application-created files.
- Prevented packaged builds from redirecting persistent data with `--portable-data`.
- Restricted packaged Skill downloads to `data/skills/h3-prompt-writing`.
- Added Japanese guidance for read-only extraction locations.
- Pinned and included official llama.cpp b9637 CPU and Vulkan Windows x64 runtimes.
- Added Vulkan GPU support for compatible AMD, Intel, and NVIDIA devices.
- Added Vulkan device discovery, Auto/explicit GPU offload, and conservative UMA handling.
- Kept CPU as the default and no-device fallback.
- Set the default Context to 8192 and added 4096/8192/16384/32768 presets.
- Added fresh available/total RAM checks and selected-GGUF-based approximate warnings.
- Added exact llama.cpp input-token preflight and privacy-safe Japanese HTTP diagnostics.
- Explicitly enabled llama.cpp offline mode and Prompt Cache.
- Reused the owned llama-server for unchanged Generate settings and restarted only on changes.
- Added 300-second generation timeout plus owned-process Cancel/exit cleanup.
- Added privacy-safe CPU/Vulkan performance diagnostics.
- Added 20-generation process/memory regression coverage.
- Added release-content auditing for models, Skill files, caches, logs, user settings, and paths.
- Added dependency and portable extraction smoke testing in Japanese and space-containing paths.
- Completed v1.0.0 README, About/version display, license notices, and SHA256 generation.

Not included in v1.0.0: CUDA, HIP, SYCL, image analysis, ComfyUI integration, automatic GGUF
downloads, or bundled MiniMax Skill content.
