# Changelog

## 2.1.1 — 2026-08-24

Task-specific Skill routing and output schema validation patch release.

### Fixed

- Made the Task selected in Target Profile the authoritative and sole generation mode.
- Prevented Request text from overriding or reclassifying the selected Task, including phrases such as `starting image`, `first frame`, or `process as Ref2VA`.
- Excluded generic mode auto-detection and non-selected Task schema sections from the runtime Skill material.
- Ensured that selected Ref2VA generation consistently uses the Ref2VA guide and its six-section `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, and `non_diegetic_music` schema.
- Ensured that I2VA and other selected Tasks retain their own guide and schema even when Request text names Ref2VA fields or another mode.
- Added a Task Schema Lock for I2VA and Ref2VA output. If the model returns fields or field ordering for a different Task, Local Prompt Studio rejects the result instead of exposing it as a completed Prompt.
- Added localized Task/schema mismatch guidance that identifies the selected Task and known mismatched fields, explains why output was stopped, and directs users to check the Target Profile Task and any conflicting Request instructions.
- Kept Request text unchanged and did not add automatic Task switching, schema repair, or retry behavior.
- Added regression coverage for conflicting Request text, Task switching, Skill reload, Context size changes, final API prompt assembly, AI Chat, and rejected-output Copy/Send state.

### Compatibility

- Kept Prompt generation behavior outside Task routing, AI Chat, Profiles/Renderers, portable storage, themes, and MMH3 Prompt Bridge protocol unchanged.

## 2.1.0 — 2026-08-23

Stable theme and visibility update for Local Prompt Studio.

### Theme and appearance

- Added localized **Normal** and **Dark** theme selection in Settings.
- Saved the selected theme in portable configuration and applied it immediately when Settings is saved.
- Restored the saved theme at application startup.
- Migrated and normalized legacy System, Light, and Dark configuration values without changing Config schema v7.
- Added a complete dark palette with improved text, control, menu, disabled-state, tooltip, translation-highlight, and error-state contrast.
- Preserved the native Windows appearance when returning to Normal theme, including repeated Normal/Dark switching.
- Improved the Dark theme visibility of the Request and editable Prompt input boundaries, with distinct normal and focused outlines limited to those two editors.

### Compatibility

- Kept Prompt generation, AI Chat, local VLM analysis, Profile/Renderer behavior, portable storage, and MMH3 Prompt Bridge protocol unchanged.

## 2.0.0 — 2026-08-22

First stable Local Prompt Studio release, consolidating the v2 community-test series into a portable local Prompt, Chat, image-analysis, and ComfyUI workflow.

### Product and prompt architecture

- Completed the product transition to **Local Prompt Studio** while preserving the existing MMH3 Prompt Bridge name and protocol compatibility.
- Added Profile- and Skill-based prompt generation with five self-contained model renderers: MiniMax H3, Wan 2.2, LTX-2.3, Krea 2, and Anima.
- Added profile/task targets, Faithful/Balanced/Creative transformation styles, optional automatic quality tags, Prompt History, and English/Japanese UI locales.
- Added exact Literal Content and Protected Terms handling, including paired `[speech:xx]...[/speech]` and `[text:xx]...[/text]` syntax.
- Added adaptive Anima Natural/Tag/Hybrid handling with separate Positive and Negative Prompt editing.

### Local AI workflows

- Added session-based AI Chat using the shared Prompt model or a separate Chat GGUF, with one application-managed model resident at a time.
- Added optional model-specific mmproj configuration, local VLM image analysis, Drag & Drop image attachment, and Prompt Reference Analysis.
- Added editable Prompt Transfer previews and append-only transfer to Request, Overall Supplement, Start Image Supplement, and End Image Supplement where supported.
- Added responsive Prompt and Settings layouts, compact system details, IME-aware text editing, and explicit model unload/reload.

### Prompt fidelity and translation editing

- Removed semantic-category hard rejection that could reject valid generated text, while retaining exact Literal Content and Protected Terms validation.
- Strengthened MiniMax H3 intent preservation for visual medium, camera/cut intent, action order, and every explicitly stated temporal interval without overriding Skill/Profile syntax authority.
- Added a localized Request input guide for timing, fixed camera, cuts, speech, and visible text.
- Added an H3 visual-style selector for Unspecified, 2D Animation, Live Action, and 3D CG, with safe visible insertion at the beginning of Request.
- Added a bidirectional Original/Japanese Translation Editor with Structure Protection, protected-span highlighting, manual translation updates, optional debounced Auto translate, and stale-response protection.

### Portable runtime, integration, and privacy

- Preserved Windows 10/11 x64 portable operation with bundled llama.cpp b9637 CPU and Vulkan runtimes; Git, Python, and an installer are not required.
- Kept settings, optional history, logs, downloaded Skill data, and protected ComfyUI credentials inside the extracted application folder for delete-the-folder removal.
- Preserved optional ComfyUI text delivery through MMH3 Prompt Bridge without workflow modification, `/prompt` calls, queueing, or automatic generation.
- Kept GGUF, mmproj, MiniMax H3 Prompt Skill content, user data, Prompt text, image bytes, and credentials out of the release package and normal application logs.

### Known limitations

- GGUF and mmproj files are not bundled; users provide compatible local files.
- MiniMax H3 requires its Prompt Skill, obtained separately from the application UI when needed.
- MMH3 Prompt Bridge is optional and must be copied to the ComfyUI `custom_nodes` folder for integration.
- Generated image/video quality and literal rendering or pronunciation depend on the selected target model and workflow.
- Automatic model downloads, cloud inference, automatic ComfyUI workflow editing, and automatic ComfyUI queueing are not included.
## 2.0.0-beta.5 — 2026-08-17

Community test pre-release for local AI Chat, optional VLM image analysis, and reusable Prompt transfer.

### Added and changed

- Added a persistent-session AI Chat tab using the local llama-server, with either the Prompt-generation GGUF or a separately selected Chat GGUF and a one-model-resident switching policy.
- Added model-specific optional mmproj settings, local image attachment, Drag & Drop, thumbnail preview, normal image analysis, and model-independent **Prompt Reference Analysis**.
- Added editable transfer previews for AI responses and reference analyses, with append-only destinations for Request, Overall Supplement, Start Image Supplement, and End Image Supplement where supported by the selected task.
- Added PromptTransferRenderer to remove conversational boilerplate without changing concrete transfer content; reference analysis results are already transfer-ready and are not transformed twice.
- Preserved Request, Overall, Start, and End roles through Prompt generation, kept Request as the central intent, prevented Start/End role mixing, and kept Anima mode selection centered on Request.
- Added explicit model unloading, responsive Settings scrolling, improved Chat composer and tab presentation, IME-aware placeholders, a clearer transfer Card, and scroll-safe FL2VA supplement editors for compact screens.
- Kept image bytes and base64, Chat/Prompt text, credentials, and other private content out of normal application logs. Image analysis remains local to the application-managed localhost llama-server.

### Known limitations

- GGUF and mmproj files are not bundled; users provide compatible local files.
- Image understanding requires a GGUF/mmproj combination supported by the bundled llama.cpp runtime.
- MMH3 Prompt Bridge is optional and must be installed separately on the ComfyUI side.
- Generated media quality depends on the selected target model and workflow settings.
- Literal Content guarantees prompt-text preservation, not successful target-model rendering or pronunciation.
- Automatic model download, online Profile updates, cloud inference, automatic ComfyUI workflow editing, and automatic ComfyUI queueing are not included.

## 2.0.0-beta.4 — 2026-08-16

Community test pre-release for the compact two-column workspace and explicit model unloading.

### Added and changed

- Reorganized the main window into a two-column layout with scrollable compact settings on the left and a wider Request/Prompt workspace on the right.
- Made the system status compact while retaining its collapsible details view.
- Rebalanced Request and Prompt sizing, with Prompt receiving the larger initial share of the editable workspace.
- Changed Duration, Motion, Camera, and Shot to full-width one-item-per-row controls in the video settings area.
- Moved the task-specific mode supplement above Request in the right workspace and kept it collapsed by default.
- Added **Unload model**, which stops only the application-managed llama-server process to release model RAM/GPU memory; the normal generation flow reloads it when next needed.
- Updated the current repository identity and user-facing source links to `https://github.com/tarou61300/Local-Prompt-Studio` while preserving the MMH3 Prompt Bridge name and protocol compatibility.

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
