# Local Prompt Studio v3.1.1 — 用户指南

[README](../../README.md) | [English](en-US.md) | [日本語](ja-JP.md) | **简体中文** | [Русский](ru-RU.md) | [한국어](ko-KR.md)

## 1. 概述

Local Prompt Studio是一款Windows桌面工具，使用本机GGUF语言模型为图像和视频模型整理Prompt。它包含三个主要模式：**Prompt Generation**、**AI Chat**和**Prompt Library**。

Prompt生成支持MiniMax H3、Wan 2.2、LTX-2.3、Krea 2和Anima Profile；每个Profile会选择自己的Renderer和输出格式。不使用Prompt发送功能时无需安装ComfyUI。本程序为免安装Portable应用，持久数据保存在解压目录内。

## 2. 系统要求与下载

- Windows 10／11 x64
- 可写入的解压目录
- 满足所选GGUF和Context Size需求的System RAM
- 使用Vulkan时需要支持Vulkan的GPU和正常驱动
- 无需Python、Git、pip、CUDA、管理员权限或手动配置PATH

仅从[官方GitHub Releases](https://github.com/tarou61300/Local-Prompt-Studio/releases/latest)下载Windows Portable ZIP。安装包不含GGUF和`mmproj`。请避免使用非官方来源重新分发的EXE。

请将ZIP的hash与同一Release中的`SHA256SUMS.txt`比较：

```powershell
Get-FileHash .\Local-Prompt-Studio-<version>-win-x64-portable.zip -Algorithm SHA256
```

## 3. 最初10分钟

1. 将ZIP解压到新的可写目录，不要在ZIP内直接运行。
2. 启动`LocalPromptStudio.exe`。
3. 在初始设置中选择已有GGUF。
4. 首次使用选择**CPU**，或选择llama.cpp检测到的**Vulkan GPU**。
5. 选择**类别**、**模型**、**Variant**和**Task**。
6. 在**Request（自动识别输入语言）**中输入需求。
7. 点击**生成Prompt**。
8. 检查可编辑结果，然后**复制**、**保存为TXT**，或在**Prompt Library → 新建Prompt**中保存。

## 4. 初始设置

使用**选择GGUF**引用PC上的`.gguf`文件。程序不会复制或自动下载GGUF；移动或删除原文件后需要重新选择。

当前说明将Qwen3-8B Q4_K_M作为较高质量选择，并将Qwen3-4B Q4_K_M作为低内存PC的选择。这只是建议，不代表所有GGUF或量化都兼容。

只有MiniMax H3需要官方H3 Prompt Skill。联网时执行明确的Skill获取操作后，文件保存在Portable目录的`data/skills`。Wan 2.2、LTX-2.3、Krea 2和Anima不需要该Skill。

不确定时先使用CPU。如果Vulkan版llama.cpp检测到设备，可选择Vulkan和对应设备。确认摘要后完成设置。

## 5. Backend、Context与内存设置

- **CPU**是默认和fallback；**CPU Threads**控制线程数。
- **Vulkan GPU**只允许选择`llama-server --list-devices`报告的**Vulkan Device**。
- **GPU Offload**默认为Auto，高级用户可指定layer数。
- Context选项为4096、**8192 — Recommended**、16384、32768。4096可能无法同时容纳完整H3 Skill、输入和输出；更大值需要更多内存。
- 界面显示Available RAM／Total RAM。统一内存设备报告的GPU memory不会再次加入System RAM，避免重复计算。
- 内存不足警告出现时，请关闭其他程序、降低Context Size或使用更小的GGUF。
- Prompt和Chat生成最长等待300秒。

程序只管理一个外部llama-server process。model、backend、device、Context和thread一致时会复用它，需要时才重启。**卸载模型**会停止本程序拥有的server并释放model memory，下次生成会重新加载。Cancel、timeout和退出也只终止本程序启动的server。

## 6. Prompt Generation

选择**类别**、**模型**、**Variant**、**Task**和**Prompt转换风格**。主要指示写入**Request**；若Task支持，可使用整体／开始图像／结束图像补充。**自动添加质量标签**只控制Renderer定义的质量词，用户输入的词会保留。

可使用**生成Prompt**、**取消**和**重新生成**。输出可编辑，并可**复制**、**保存为TXT**、**翻译并编辑**或**发送到ComfyUI**。Anima分别显示Positive／Negative；当前Bridge只发送Positive，Negative需单独复制。

UI中选择的Task是唯一Task来源，Request内容不会改变Task。I2VA／Ref2VA使用Task Schema Lock：模型返回其他Task schema时，结果会被拒绝，不进行自动修复、retry或Task切换。提示会显示当前Task以及可用时的不一致top-level field。Prompt格式由Profile和Renderer决定。Model名、`I2VA`等Task token及schema field不会翻译。

## 7. Request Guide、Literal Content与Protected Terms

**输入指南**可向Request添加timing、固定camera、cut、speech和visible text示例，它不会解析媒体文件。请在Request或相应补充中明确时间区间、camera／cut意图、发言和画面文字。

需要完全一致的内容请使用paired Literal格式：

```text
[speech:ja]おかえりなさい。[/speech]
[text:en]OPEN[/text]
[speech:zh]你好[/speech]
[speech:ru]Привет[/speech]
[speech:ko]안녕하세요[/speech]
```

paired block支持多行。旧版行首形式如`[speech:zh] 你好`仍兼容，范围到该行末尾；paired marker优先。正文必须在生成Prompt中完全一致，marker本身会移除。Protected Terms也会保留。validation失败时不会采用结果。诊断只显示来源、检测方式、字符数和不包含正文的短ID；请检查Request及折叠的补充输入框。

## 8. Prompt Translation Editor

Profile的Prompt输出语言是翻译源，**UI语言**是翻译目标。当前Builtin Profile输出英文Prompt：

- 日本語UI：English ↔ Japanese
- 简体中文UI：English ↔ Simplified Chinese
- Русский UI：English ↔ Russian
- 한국어 UI：English ↔ Korean
- 英文UI配合英文输出Profile时，不显示**翻译并编辑**。

**保护结构**会把Skill定义结构、Protected Terms和Literal Content从翻译中隔离。打开Editor时只自动执行首次Original→UI语言翻译，**自动翻译**仍默认为OFF。开启后在停止输入1秒后同步；关闭时使用**更新翻译**。revision检查会丢弃旧响应。点击**应用**后写回的是Profile输出语言一侧的Original；当前Builtin Profile为英文。修改UI语言后需重启应用。

## 9. AI Chat与Vision

AI Chat可以共享Prompt生成GGUF，也可以使用专用Chat GGUF。同时只驻留一个model；切换用途时会先安全停止当前server，再加载所需model。会话Context保留在当前chat session中。

图像识别需要为有效Chat model配置匹配的`mmproj`。使用**+ 图像**或Drag & Drop添加PNG、JPG／JPEG、静止WebP。animated WebP不受支持。静止WebP会在memory中标准化为PNG，再发送给localhost llama-server。

**分析**生成普通Chat回答；**Prompt Reference分析**生成便于Prompt生成复用的model-independent信息。检查并编辑transfer preview，然后通过**发送到Prompt补充**追加到Request、整体补充、开始图像补充或结束图像补充。

图像、Chat内容和Prompt不会发送到外部cloud，只由本程序管理的localhost llama-server处理。

## 10. Prompt Library

Prompt Library与历史记录相互独立。记录包含Title、Model、Task、Tags和完整Prompt正文。可通过收藏和已有Tag建议复用Tag；Model、Task、Title、Tags用于筛选，多Tag采用AND条件。选择Tag会自动搜索；取消最后一个Tag时不会自动显示全部记录，需要时点击**搜索**。

选择结果row后显示Prompt详情。metadata编辑只修改Title／Tags，不修改Prompt正文、Model、Task或标识符。支持单条复制、勾选后**复制所选Prompt**和删除确认。**设置**可调整Tag行数、结果行数和Prompt详情最小行数。

## 11. Prompt Library Datasets

原有`data/prompt_library.sqlite3`保持为**Default Dataset**，不会自动移动。使用**新建Dataset**创建独立Library，通过selector切换；**加载Dataset**会将有效SQLite Library导入为受管理副本；**导出Dataset**创建可移植database副本。active Dataset会被记住。

Prompt、Tags和收藏Tag状态在Dataset之间相互隔离。Dataset merge尚未实现。

从v3.0.0迁移时，先关闭新旧应用，再在新版**Prompt Library → 加载Dataset**中选择旧版`data/prompt_library.sqlite3`。source DB会被验证且不会移动。不要覆盖旧release目录，应将其保留为backup。Library迁移请使用Dataset导入／导出。Settings、History和附加Dataset不存在通用自动迁移或merge，因此不要假定复制个别内部文件一定受支持。

## 12. ComfyUI集成

ComfyUI集成为可选功能。

1. 关闭ComfyUI，将同捆`ComfyUI-Bridge/MMH3PromptBridge`复制到`ComfyUI/custom_nodes/MMH3PromptBridge`。
2. 重启ComfyUI并打开或刷新browser页面。
3. 打开Local Prompt Studio的**设置 → ComfyUI集成**。
4. 本地ComfyUI使用`http://127.0.0.1:8188`并点击**测试连接**。
5. 点击**与ComfyUI配对**；确认双方六位code一致后，才在ComfyUI中点击**Allow**。
6. 右键ComfyUI中的目标text／Prompt node，选择**MMH3 Prompt Bridge → Set as MMH3 Target**及目标STRING／multiline STRING widget。
7. 生成或编辑Prompt后点击**发送到ComfyUI**。

发送只替换当前browser workflow中所选widget的文字，不queue workflow、不启动生成、不broadcast，也不使用`/prompt` API。browser reload或workflow／tab改变后可能需要重选target。远程ComfyUI必须使用HTTPS，禁止将无认证ComfyUI暴露到internet。credential由Windows DPAPI CurrentUser保护，无需手动token。Bridge browser JavaScript UI保持英文。

## 13. 设置参考

- **LLM模型路径**：Prompt生成GGUF
- **推理Backend**、**Vulkan Device**、**CPU Threads**、**GPU Offload**、**Context Size**：llama.cpp执行与内存
- **Skill位置**：MiniMax H3 Skill及更新
- **历史记录**：可选本地生成记录，默认OFF
- **主题**、**UI语言**：外观与界面语言；语言在重启后生效
- **AI Chat模型**、`mmproj`：共享Prompt model或专用Chat GGUF，以及每个model的图像projection文件
- **Prompt Library**：Tag／结果行数与Prompt详情最小行数
- **ComfyUI集成**：URL、连接测试与配对

设置保存在Portable目录中，不修改GGUF文件。

## 14. Portable数据、隐私与离线使用

持久数据均位于应用目录下的`data`：

- `data/config.json`
- `data/local-prompt-studio.log`
- `data/llama-server/`
- `data/history.sqlite3`
- `data/prompt_library.sqlite3`
- `data/prompt_library_datasets.json`
- `data/prompt_library_datasets/<dataset-id>/prompt_library.sqlite3`
- `data/skills/h3-prompt-writing/`
- `data/comfyui_credentials.dat`

程序不使用Registry保存应用设置，不安装Windows service，也不创建Start Menu shortcut。关闭后删除解压目录即可卸载。程序没有telemetry、广告或analytics。普通Prompt生成只使用所选GGUF和`127.0.0.1`。只有明确执行H3 Skill获取／更新检查、打开官方model page、ComfyUI Test Connection／Pair／Send等操作才访问network。Request和生成Prompt通常不会写入应用log。ComfyUI credential由DPAPI CurrentUser保护，禁止分享。准备好本地数据后，普通生成可离线运行。

## 15. 更新与备份

1. 退出Local Prompt Studio并等待llama-server结束。
2. 保留旧release目录作为backup，不要在其上覆盖解压新ZIP。
3. 将新版解压到另一个可写目录。
4. 用户数据位于旧目录的`data`；只在应用关闭时备份。
5. Prompt Library使用旧版**导出Dataset**和新版**加载Dataset**迁移；v3.0.0的`data/prompt_library.sqlite3`可直接加载。
6. 验证新版后再删除旧目录。可保留Release ZIP、`SHA256SUMS.txt`和backup。

程序不提供cloud同步、Dataset自动merge或覆盖所有Portable数据的通用自动migration。

## 16. 故障排除

- **无法写入设置**：重新解压到当前用户可写目录，不要在ZIP内运行。
- **未设置model**：在设置中选择已有GGUF，程序只引用而不复制。
- **未设置H3 Skill**：仅MiniMax H3需要在线获取官方Skill。
- **未检测到Vulkan Device**：检查GPU驱动或改用CPU；检测结果以llama.cpp为准。
- **Context不足**：选择8192，内存允许时选16384，并按需缩短输入。
- **RAM不足**：关闭其他程序、降低Context或使用更小GGUF。
- **生成缓慢**：CPU可能需要数分钟，最长等待300秒。
- **Cancel后仍在结束**：等待数秒让server停止，必要时关闭应用；只会终止自身server。
- **ComfyUI连接失败**：确认ComfyUI已运行、Bridge已安装、browser已刷新且URL正确。

在[GitHub Issues](https://github.com/tarou61300/Local-Prompt-Studio/issues)报告问题时，请提供Windows版本、CPU／GPU、GGUF文件名、backend、Context Size、完整error信息和相关`data/llama-server/` log。不要发布私人Request、Prompt、credential或个人信息。
