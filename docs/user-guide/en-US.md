# Local Prompt Studio v3.1.1 — User Guide

[README](../../README.md) | **English** | [日本語](ja-JP.md) | [简体中文](zh-CN.md) | [Русский](ru-RU.md) | [한국어](ko-KR.md)

## 1. Overview

Local Prompt Studio is a Windows desktop tool that uses a GGUF language model on your PC to prepare prompts for image and video models. Its three main modes are **Prompt Generation**, **AI Chat**, and **Prompt Library**.

Prompt Generation supports MiniMax H3, Wan 2.2, LTX-2.3, Krea 2, and Anima profiles. Each profile selects its own renderer and output format. ComfyUI is not required unless you want to send a completed prompt to a widget in an open ComfyUI workflow.

The application is portable: there is no installer, and its persistent application data stays in the extracted folder.

## 2. Requirements and Download

- Windows 10 or Windows 11 x64.
- A writable folder for the extracted application.
- Enough System RAM for your chosen GGUF and Context Size.
- For Vulkan GPU use, a Vulkan-capable GPU and a working vendor driver.
- Python, Git, pip, CUDA, administrator rights, and manual PATH setup are not required.

Download only from the [official GitHub Releases page](https://github.com/tarou61300/Local-Prompt-Studio/releases/latest). GGUF and `mmproj` files are not included. Avoid executable files redistributed by unofficial sources.

Compare the downloaded ZIP with `SHA256SUMS.txt` from the same Release. In PowerShell:

```powershell
Get-FileHash .\Local-Prompt-Studio-<version>-win-x64-portable.zip -Algorithm SHA256
```

## 3. First 10 Minutes

1. Extract the ZIP into a new writable folder; do not run it inside the ZIP.
2. Start `LocalPromptStudio.exe`.
3. Select an existing GGUF when setup asks for one.
4. Start with **CPU**, or choose a **Vulkan GPU** device detected by llama.cpp.
5. Choose **Category**, **Model**, **Variant**, and **Task**.
6. Enter what you want in **Request (input language is automatic)**.
7. Select **Generate Prompt**.
8. Review the editable result, then use **Copy**, **Save as TXT**, or open **Prompt Library** and save it as a **New Prompt**.

## 4. Initial Setup

Use **Choose GGUF** to reference an existing `.gguf` file. The application does not copy or download it; moving or deleting that file means you must select it again.

Current guidance recommends Qwen3-8B Q4_K_M for higher quality. Qwen3-4B Q4_K_M is an option for PCs with less memory. This is guidance, not a claim that every GGUF or quantization is compatible.

MiniMax H3 alone requires the official H3 Prompt Skill. Use the explicit Skill download action while online; it is stored inside portable `data/skills`. Wan 2.2, LTX-2.3, Krea 2, and Anima do not use that Skill.

Choose CPU first when uncertain. If the bundled Vulkan llama.cpp runtime detects a device, you can select Vulkan and that device. Review the setup summary, then finish setup.

## 5. Choosing Backend, Context, and Memory Settings

- **CPU** is the default and fallback. **CPU Threads** controls the thread count.
- **Vulkan GPU** is available only for devices reported by `llama-server --list-devices`. Select the required **Vulkan Device**.
- **GPU Offload** defaults to Auto; advanced users may set a layer count.
- Context presets are 4096, **8192 — Recommended**, 16384, and 32768. 4096 may be too small for the complete H3 Skill plus input and output. Higher values use more memory.
- The status shows Available RAM and Total RAM. GPU-reported memory on unified-memory systems is not added to System RAM, avoiding double counting.
- A warning appears when the selected model and context are likely to exceed current or installed RAM. Close other applications, reduce Context Size, or use a smaller model.
- Prompt and chat operations can wait up to 300 seconds for generation.

The application owns one external llama-server process. It reuses the server while model, backend, device, context, and thread settings match, and restarts it when required. **Unload model** stops the application-owned server and frees its model memory; the next generation starts and loads it again. Cancel, timeout, and application shutdown stop only the server owned by Local Prompt Studio.

## 6. Prompt Generation

Choose **Category**, **Model**, **Variant**, **Task**, and **Prompt Transformation Style**. Enter the main instruction in **Request** and, where the selected Task provides them, use optional overall/start/end supplements. **Automatically add quality tags** controls only renderer-defined quality terms; user-entered terms remain.

Use **Generate Prompt**, **Cancel**, and **Regenerate** as needed. The generated Prompt is editable. You can use **Copy**, **Save as TXT**, **Translate & Edit**, or **Send to ComfyUI**. Anima displays separate Positive and Negative outputs; the current ComfyUI bridge sends Positive only, so copy Negative separately.

The Task selected in the UI is the only source of Task routing. Text inside Request never changes the Task. I2VA and Ref2VA use Task Schema Lock: if the model returns a schema for a different Task, the result is rejected rather than repaired, retried, or assigned to another Task. The message identifies the selected Task and, when available, mismatching top-level fields. The Profile and its renderer determine prompt form. Model names, Task tokens such as `I2VA`, and schema field names are not translated.

## 7. Request Guide, Literal Content, and Protected Terms

**Input guide** can append examples for timing, a fixed camera, cuts, speech, and visible text. Treat them as editable guidance, not uploaded media. Clearly state timing intervals, camera/cut intent, spoken words, and visible text in Request or the appropriate supplement.

Use paired Literal Content markers for exact text:

```text
[speech:ja]おかえりなさい。[/speech]
[text:en]OPEN[/text]
[speech:zh]你好[/speech]
[speech:ru]Привет[/speech]
[speech:ko]안녕하세요[/speech]
```

Multiline paired blocks are supported. Legacy line-leading forms such as `[speech:en] Hello` remain accepted for backward compatibility and extend to the end of that line. Paired markers take priority. The content must appear exactly in the generated Prompt; the marker itself is removed. Protected Terms are likewise preserved. If validation fails, the result is not adopted. The diagnostic shows the source field, detection type, character count, and a short non-content identifier without displaying the protected text itself. Check Request and collapsed supplements for the reported item.

## 8. Prompt Translation Editor

The Profile output language is the translation source, and the language selected under **UI Language** is the translation target. Current built-in Profiles output English:

- Japanese UI: English ↔ Japanese.
- Simplified Chinese UI: English ↔ Simplified Chinese.
- Russian UI: English ↔ Russian.
- Korean UI: English ↔ Korean.
- With English UI and an English-output Profile, **Translate & Edit** is hidden.

**Protect structure** isolates Skill-defined structure, Protected Terms, and Literal Content from translation. The first Original-to-UI-language translation runs when the editor opens; **Auto translate** remains off by default. Turn it on for one-second debounced synchronization, or use **Update Translation** manually. Revision checks discard stale responses. **Apply** always writes the Profile-language Original Prompt—English for current built-in Profiles—back to the Prompt output. Changing UI Language requires restarting the application.

## 9. AI Chat and Vision

AI Chat can share the Prompt Generation GGUF or use a separate Chat GGUF. Only one model remains resident; switching use safely stops the current application-owned server before loading the other model. Conversation context is retained for the current chat session.

For vision, configure a matching `mmproj` for the effective Chat model. Use **+ Image** or drag and drop a PNG, JPG/JPEG, or static WebP. Animated WebP is unsupported. A static WebP is decoded and normalized to PNG in memory before it is sent to the local llama-server.

**Analyze** provides a normal chat analysis. **Prompt Reference Analysis** produces model-independent reference information intended for Prompt Generation. Review the transfer preview, edit it if needed, and use **Send to prompt supplement** to append it to Request, Overall Supplement, Start image supplement, or End image supplement as supported by the target Task.

Images, chat messages, and prompts are sent only to the application-managed localhost llama-server, not to an external cloud service.

## 10. Prompt Library

Prompt Library is independent from History. A record contains Title, Model, Task, Tags, and the exact Prompt body. Use Favorites and existing-tag suggestions to reuse Tags. Model, Task, Title, and Tags filter the library; multiple selected Tags use AND matching. Selecting a Tag starts the search automatically. Removing the final selected Tag intentionally does not display every record automatically—select **Search** when you want that.

Selecting a result row displays **Prompt Details**. Metadata editing changes Title and Tags without changing Prompt, Model, Task, or its identifier. You can copy one result or check and **Copy selected prompts**. Delete requires confirmation. **Settings** controls Tag rows, Result rows, and minimum Prompt-detail lines.

## 11. Prompt Library Datasets

The original `data/prompt_library.sqlite3` remains the **Default Dataset** and is not moved automatically. Use **New dataset** for an independent library, the Dataset selector to switch, **Load dataset** to import a valid SQLite library as a managed copy, and **Export dataset** to create a portable database copy. The active Dataset is remembered.

Prompts, Tags, and Favorite Tag state belong to each Dataset and remain isolated from other Datasets. Dataset merge is not implemented.

For an older v3.0.0 installation, close both applications and use **Prompt Library → Load dataset** in the new version to load the old `data/prompt_library.sqlite3`. The source database is validated and is not moved. Do not overwrite the old release folder; retain it as a backup. Use Dataset export/import for library migration. No general automated migration or merge is provided for Settings, History, or additional Datasets, so do not assume that copying selected internal files is supported.

## 12. ComfyUI Integration

ComfyUI integration is optional.

1. Close ComfyUI and copy the packaged `ComfyUI-Bridge/MMH3PromptBridge` folder to `ComfyUI/custom_nodes/MMH3PromptBridge`.
2. Restart ComfyUI, then open or reload its browser page.
3. Open **Settings → ComfyUI Integration** in Local Prompt Studio.
4. For local ComfyUI, keep `http://127.0.0.1:8188` and select **Test Connection**.
5. Select **Pair with ComfyUI**. Confirm that the six-digit code matches in both applications, then select **Allow** in ComfyUI.
6. Right-click the desired text/Prompt node in ComfyUI and choose **MMH3 Prompt Bridge → Set as MMH3 Target**, then the target STRING/multiline STRING widget.
7. Generate or edit a Prompt and select **Send to ComfyUI**.

Sending replaces text in the selected browser workflow widget. It does not queue the workflow, start generation, broadcast a prompt, or use the `/prompt` API. A browser reload or workflow/tab change may require selecting the target again. Remote ComfyUI requires HTTPS; never expose an unauthenticated ComfyUI instance to the internet. The stored credential is protected with Windows DPAPI CurrentUser. There is no manual token workflow. The bridge extension's browser JavaScript UI remains English.

## 13. Settings Reference

- **LLM Model Path**: existing Prompt Generation GGUF.
- **Inference Backend**, **Vulkan Device**, **CPU Threads**, **GPU Offload**, **Context Size**: llama.cpp execution and memory controls.
- **Skill Location**: MiniMax H3 Prompt Skill location and update actions.
- **History**: optional local generation History; off by default.
- **Theme** and **UI Language**: application appearance and UI locale. A language change applies after restart.
- **AI Chat Model** and `mmproj`: share the Prompt model or choose a Chat GGUF, then assign a matching image projection file per model.
- **Prompt Library**: visible Tag rows, Result rows, and Prompt-detail minimum lines.
- **ComfyUI Integration**: server URL, connection test, and pairing.

Settings are portable and do not change your GGUF files.

## 14. Portable Data, Privacy, and Offline Use

The portable release stores persistent application data under its own `data` directory:

- Settings: `data/config.json`
- Application log: `data/local-prompt-studio.log`
- llama-server logs: `data/llama-server/`
- Optional History: `data/history.sqlite3`
- Default Prompt Library: `data/prompt_library.sqlite3`
- Dataset registry: `data/prompt_library_datasets.json`
- Additional Datasets: `data/prompt_library_datasets/<dataset-id>/prompt_library.sqlite3`
- Downloaded H3 Skill: `data/skills/h3-prompt-writing/`
- ComfyUI credential: `data/comfyui_credentials.dat`

The application does not use the Registry for application settings, install a Windows service, or create Start Menu shortcuts. Close it and delete the extracted folder to remove it. There is no telemetry, advertising, or analytics. Normal Prompt Generation uses the selected GGUF through `127.0.0.1`. Network access occurs only for explicit operations such as H3 Skill retrieval/update checks, opening an official model page, or ComfyUI Test Connection/Pair/Send. Requests and generated Prompts are not normally written to the application log. The ComfyUI credential is protected with DPAPI CurrentUser and must not be shared.

After the required local data has been prepared, normal generation can run offline.

## 15. Updating and Backing Up

1. Close Local Prompt Studio and wait for its llama-server to exit.
2. Keep the old release folder as a backup; do not extract a new ZIP over it.
3. Extract the new release into a separate writable folder and start it there.
4. User data is under the old folder's `data` directory. Back it up while the application is closed.
5. For Prompt Library migration, use **Export dataset** in the old version and **Load dataset** in the new one. A v3.0.0 `data/prompt_library.sqlite3` can be loaded directly.
6. Verify the new installation before deleting the old folder. You may retain the Release ZIP, `SHA256SUMS.txt`, and backups.

Local Prompt Studio does not provide cloud synchronization, automatic Dataset merge, or a general automatic migration for all portable data.

## 16. Troubleshooting

- **The application cannot write settings**: extract the ZIP to a folder your account can write to, such as a user-owned folder. Do not run from inside the ZIP.
- **No model is configured**: choose an existing GGUF in Settings. The model is referenced, not copied.
- **H3 Skill is missing**: for MiniMax H3 only, use the official Skill download while online. Other Profiles do not require it.
- **No Vulkan device is detected**: update/check the GPU driver, or switch to CPU. The app trusts llama.cpp device detection.
- **Context is insufficient**: use 8192 or, when memory permits, 16384. Reduce input if necessary.
- **RAM is insufficient**: close other software, lower Context Size, or choose a smaller GGUF.
- **Generation is slow**: CPU generation can take several minutes; the maximum wait is 300 seconds.
- **Cancel is still finishing**: wait a few seconds while the owned server stops. If needed, close the application; it shuts down only its own server.
- **ComfyUI connection fails**: check that ComfyUI is running, the Bridge is installed, the browser page was reloaded, and the URL is correct.
- **No ComfyUI target**: right-click the target node and select the STRING widget again through MMH3 Prompt Bridge.

When reporting a problem in [GitHub Issues](https://github.com/tarou61300/Local-Prompt-Studio/issues), include Windows version, CPU/GPU, GGUF filename, backend, Context Size, the exact error message, and relevant `data/llama-server/` logs. Do not post private Requests, prompts, credentials, or personal data.
