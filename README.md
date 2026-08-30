# Local Prompt Studio v3.1.1

**Windows x64 Portable**

[English](docs/user-guide/en-US.md) | [日本語](docs/user-guide/ja-JP.md) | [简体中文](docs/user-guide/zh-CN.md) | [Русский](docs/user-guide/ru-RU.md) | [한국어](docs/user-guide/ko-KR.md)

## Download

**[Download the latest official release](https://github.com/tarou61300/Local-Prompt-Studio/releases/latest)**

Download the Windows portable ZIP and verify it against the included `SHA256SUMS.txt`. Avoid executables redistributed by unofficial sources.

Local Prompt Studio is a portable Windows desktop application for turning requests into model-specific prompts with a local GGUF language model. It also provides local AI chat and a local Prompt Library. ComfyUI is optional.

## Main modes

- **Prompt Generation** — transforms a Request with a profile-specific renderer.
- **AI Chat** — local chat, with optional image understanding when a matching `mmproj` is configured.
- **Prompt Library** — stores, searches, tags, copies, and exports completed prompts in local SQLite datasets.

Supported profiles: **MiniMax H3**, **Wan 2.2**, **LTX-2.3**, **Krea 2**, and **Anima**.

UI languages: English (`en-US`), 日本語 (`ja-JP`), 简体中文 (`zh-CN`), Русский (`ru-RU`), and 한국어 (`ko-KR`).

## Quick start

1. Download the portable ZIP from the official Release page and extract it to a new writable folder.
2. Run `LocalPromptStudio.exe` and select an existing GGUF model.
3. Start with CPU, or select a Vulkan device detected by the bundled llama.cpp runtime.
4. Choose a Profile and Task, enter a Request, select **Generate Prompt**, then copy the result or save it to Prompt Library.

GGUF models and `mmproj` files are **not bundled**. MiniMax H3 additionally requires its official Prompt Skill, which the application downloads only when you explicitly request it. The optional `ComfyUI-Bridge/MMH3PromptBridge` extension is included for ComfyUI integration.

## Portable and private by design

Application settings, logs, optional History, Prompt Library datasets, downloaded Skill data, and ComfyUI credentials stay under the extracted folder's `data` directory. The application does not install a Windows service, write application settings to the Registry, create Start Menu shortcuts, or use telemetry, advertising, or analytics. Normal prompt generation communicates only with the application-managed llama-server on `127.0.0.1`.

To remove the application, close it and delete its extracted folder. Back up the `data` directory or export Prompt Library datasets before deleting an installation you want to keep.

## Documentation and support

- [English User Guide](docs/user-guide/en-US.md)
- [日本語ユーザーガイド](docs/user-guide/ja-JP.md)
- [简体中文用户指南](docs/user-guide/zh-CN.md)
- [Руководство пользователя на русском](docs/user-guide/ru-RU.md)
- [한국어 사용자 가이드](docs/user-guide/ko-KR.md)
- [Report a problem](https://github.com/tarou61300/Local-Prompt-Studio/issues)

## 日本語概要

Local Prompt Studioは、手元のGGUFモデルを使ってモデル別Promptを生成し、AIチャットとPrompt Libraryも利用できるWindows x64向けPortableアプリです。公式配布物は[GitHub Releases](https://github.com/tarou61300/Local-Prompt-Studio/releases/latest)から取得し、詳しい導入・設定・更新方法は[日本語ユーザーガイド](docs/user-guide/ja-JP.md)を参照してください。GGUFと`mmproj`は同梱されません。

## License

Local Prompt Studio is released under the [MIT License](LICENSE). See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) and the `licenses` directory for bundled third-party components. Local Prompt Studio is not an official MiniMax product.
