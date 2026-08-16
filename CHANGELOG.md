# Changelog

## 2.0.0-beta.4 — 2026-08-16

Community test pre-release for the compact two-column workspace and explicit model unloading.

### Added and changed

- Reorganized the main window into a two-column layout with scrollable compact settings on the left and a wider Request/Prompt workspace on the right.
- Made the system status compact while retaining its collapsible details view.
- Rebalanced Request and Prompt sizing, with Prompt receiving the larger initial share of the editable workspace.
- Changed Duration, Motion, Camera, and Shot to full-width one-item-per-row controls in the video settings area.
- Moved the task-specific mode supplement above Request in the right workspace and kept it collapsed by default.
- Added **Unload model**, which stops only the application-managed llama-server process to release model RAM/GPU memory; the normal generation flow reloads it when next needed.

### Fixed

- Prevented settings and workspace controls from being crushed or overlapping at supported compact window sizes.
- Prevented the Motion selector from becoming too narrow to read.
- Prevented the Request Literal Content helper from overlapping the group frame when the mode supplement is expanded.
- Improved resizing behavior at 1366×768 while preserving manual splitter resizing and the scrollable settings column.
- Kept the existing MMH3 Prompt Bridge protocol and ComfyUI behavior unchanged.

### Known limitations

- GGUF models and the MiniMax H3 Prompt Skill are not bundled; users provide a compatible GGUF and obtain the optional H3 Skill when needed.
- MMH3 Prompt Bridge is optional and must be installed separately on the ComfyUI side.
- Generated media quality depends on the selected target model and workflow settings.
- Literal Content guarantees prompt-text preservation, not successful target-model rendering or pronunciation.
- Automatic model download, online Profile updates, cloud inference, automatic ComfyUI workflow editing, and automatic ComfyUI queueing are not included.

## 2.0.0-beta.3 — 2026-08-15

Emergency community test pre-release for prompt metadata controls and optional automatic quality tags.

### Fixed and changed

- Prevented all five self-contained renderers from accepting automatically introduced rating/safety tags, `score_*` ranking tags, artist/byline tags, age/demographic tags, copyright/character tags, and year/era tags such as `retro` or `vintage` when the user did not request them.
- Removed fixed `safe`, `score_*`, and artist components from the Anima Base, Aesthetic, and Turbo profiles while preserving explicitly requested tags.
- Added the localized **Automatically add quality tags** option for MiniMax H3, Wan 2.2, LTX-2.3, Krea 2, and Anima; it is enabled by default and saved in portable Config schema v6.
- When automatic quality tags are disabled, renderer-defined quality components are not added; quality terms explicitly supplied by the user remain preserved.
- Deduplicated renderer-defined quality components against user-supplied quality terms.
- Kept the existing MMH3 Prompt Bridge protocol and ComfyUI behavior unchanged.

### Known limitations

- GGUF models and the MiniMax H3 Prompt Skill are not bundled; users provide a compatible GGUF and obtain the optional H3 Skill when needed.
- MMH3 Prompt Bridge is optional and must be installed separately on the ComfyUI side.
- Generated media quality depends on the selected target model and workflow settings.
- Literal Content guarantees prompt-text preservation, not successful target-model rendering or pronunciation.
- Automatic model download, online Profile updates, cloud inference, automatic ComfyUI workflow editing, and automatic ComfyUI queueing are not included.

## 2.0.0-beta.2 — 2026-08-14

Community test pre-release for the self-contained renderer architecture and prompt guidance UI.

### Added and changed

- Migrated MiniMax H3, Wan 2.2, LTX-2.3, Krea 2, and Anima to one self-contained renderer per model, selected internally by Profile without a renderer-selection UI.
- Added adaptive Anima Natural, Tag, and Hybrid handling with separate Positive/Negative output; Hybrid preserves its tag/trigger portion alongside natural-language content instead of forcing either format.
- Kept Krea 2 output as natural-language image prompts for Faithful, Balanced, and Creative transformations.
- Added paired `[speech:xx]...[/speech]` and `[text:xx]...[/text]` Literal Content syntax while retaining the existing line-start syntax for compatibility.
- Added contextual recognition for Japanese and common multilingual quotation marks, distinguishing speech from visible text such as signs when context identifies it.
- Invalidated stale Positive/Negative output when a new generation starts or fails so old results cannot be copied or sent as the current result.
- Added localized Variant guidance and model-specific Prompt Transformation Style guidance directly below their selectors in the Japanese and English UI.

### Known limitations

- GGUF models and the MiniMax H3 Prompt Skill are not bundled; users provide a compatible GGUF and obtain the optional H3 Skill when needed.
- MMH3 Prompt Bridge is optional and must be installed separately on the ComfyUI side.
- Generated media quality depends on the selected target model and workflow settings.
- Literal Content guarantees prompt-text preservation, not successful target-model rendering or pronunciation.
- Automatic model download, online Profile updates, cloud inference, automatic ComfyUI workflow editing, and automatic ComfyUI queueing are not included.

## 2.0.0-beta.1 — 2026-08-13

Community test beta release candidate for Local Prompt Studio.

### Added and changed

- Renamed the current desktop product to Local Prompt Studio while preserving the MMH3 Prompt Bridge name, protocol, and v1.x history.
- Added profile-based multi-model prompt transformation for the MiniMax H3, Wan 2.2, and LTX-2.3 video profiles and the Krea 2 and Anima image profiles.
- Added Faithful, Balanced, and Creative transformation styles, exact Literal Content preservation, and Protected Terms.
- Added separate Positive and Negative outputs for Anima profiles.
- Added English and Japanese UI locales with English fallback.
- Preserved local llama.cpp inference with bundled CPU and Vulkan runtimes and portable application-owned data storage.
- Preserved optional ComfyUI integration through MMH3 Prompt Bridge without automatically editing or queuing a workflow.

### Known limitations

- GGUF models and the MiniMax H3 Prompt Skill are not bundled; users provide a compatible GGUF and obtain the optional H3 Skill when needed.
- MMH3 Prompt Bridge is optional and must be installed separately on the ComfyUI side.
- Generated media quality depends on the selected target model and workflow settings.
- Literal Content guarantees prompt-text preservation, not successful target-model rendering or pronunciation.
- Automatic model download, online Profile updates, cloud inference, automatic ComfyUI workflow editing, and automatic ComfyUI queueing are not included.

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
- Added Anima Base v1.0, Aesthetic v1.1, and Turbo v1.0 with a new `danbooru_tags` renderer and structured tag-category LLM contract.
- Added deterministic Anima positive/negative component assembly, variant-aware score-tag handling, tag normalization/order, and exact Literal/Protected preservation.
- Added separate Positive/Negative output UI for profiles that declare `separate_negative_prompt`; existing ComfyUI Send continues to send only the current Positive Prompt.

This is a development foundation, not a release. Phase 2A-3 supports MiniMax H3, Wan 2.2, LTX-2.3, Krea 2, and Anima.

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
