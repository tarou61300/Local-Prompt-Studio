# Changelog

## 2.0.0-alpha.1 — 2026-08-12

- Renamed the next-generation desktop product to Local Prompt Studio while preserving v1.x history.
- Added UTF-8 English/Japanese UI localization with English fallback and persistent locale IDs.
- Added Profile Schema v1, builtin/official/custom loading layers, strict validation, and data-only security rules.
- Migrated MiniMax H3 generation behind the profile, variant, Core Transformation Policy, and `video_narrative` renderer architecture.
- Added Literal Content, Protected Terms, advisory length guidance, and profile metadata in history.
- Preserved the external MiniMax H3 Skill policy, local llama.cpp behavior, and MMH3 Prompt Bridge v1.2 compatibility.
- Added the supplied Wan 2.2 and LTX-2.3 video profiles through the existing Schema v1 and `video_narrative` renderer.
- Made profile, variant, and task selection catalog-driven while retaining H3-only advanced controls through profile capabilities.
- Made the MiniMax H3 Skill optional during first-run setup and required only when the selected profile declares that dependency.
- Added Krea 2 Raw/Turbo as the first image profile, using a new `natural_language` renderer and catalog-driven Image category selection.
- Kept Krea 2 length/detail guidance advisory: no generic quality tags, negative prompt, truncation, or forced token target is added.

This is a development foundation, not a release. Phase 2A-2 supports MiniMax H3, Wan 2.2, LTX-2.3, and Krea 2.

## 1.1.0-beta.1 — 2026-08-11

Community test pre-release. This is not the final v1.1.0 release.

### Added

- Added optional ComfyUI integration while keeping MMH3 fully usable without ComfyUI.
- Added local-first configuration with `http://127.0.0.1:8188` as the default URL and HTTPS support for remote ComfyUI servers.
- Added Test Connection and secure Pair with ComfyUI using a six-digit visual verification code.
- Added Windows DPAPI CurrentUser protection for the paired-client credential and a Pair Again flow.
- Added ComfyUI Prompt Bridge v1.2 with explicit ordinary STRING/multiline STRING target selection from the node right-click menu.
- Added Send to ComfyUI using the current edited MMH3 output, not a cached generation result.

### Security and behavior

- Plaintext paired-client credentials are not stored in `config.json` and are never displayed for manual copying.
- The DPAPI-protected MMH3 credential is stored only in the portable app-owned `data` directory.
- Loopback HTTP is allowed only for local ComfyUI; remote ComfyUI connections require HTTPS.
- Sending never calls `/prompt`, never queues a workflow, never starts ComfyUI generation, and never retries automatically.
- No persistent ComfyUI background worker, polling loop, heartbeat, or startup connection was added.
- Merely starting MMH3 creates zero ComfyUI network traffic.
- Bundled llama.cpp child processes use a Windows short-path alias when needed so redirected runtime output remains usable from Japanese or other non-ASCII extraction paths.

### Testing

- Automated suite: 265 passed, 1 skipped in the release-build environment. The skipped test is the real Windows DPAPI round-trip because DPAPI was unavailable in that isolated test context; fake-protector and credential behavior tests passed.
- Real end-to-end validation completed from Windows MMH3 Prompt Builder to GPUhub ComfyUI with Bridge v1.2, including pairing, target selection, edited-output delivery, restart persistence, and confirmation that no workflow was queued.
- Local Windows ComfyUI has not been validated on the developer PC. Community testing across portable, desktop, and manual local ComfyUI installations is explicitly requested before final v1.1.0.

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
