# Local Prompt Studio v3.1.1 — ユーザーガイド

[README](../../README.md) | [English](en-US.md) | **日本語** | [简体中文](zh-CN.md) | [Русский](ru-RU.md) | [한국어](ko-KR.md)

## 1. 概要

Local Prompt Studioは、PC上のGGUF言語モデルを使い、画像・動画モデル向けPromptを整えるWindowsデスクトップツールです。メインモードは**Prompt Generation**、**AIチャット**、**Prompt Library**の3つです。

Prompt生成はMiniMax H3、Wan 2.2、LTX-2.3、Krea 2、Anima Profileに対応し、各Profileが専用Rendererと出力形式を選びます。完成PromptをComfyUIへ送らない限り、ComfyUIは不要です。インストーラーを使わないPortable構成で、永続データは展開フォルダ内に保存されます。

## 2. 動作環境とダウンロード

- Windows 10／11 x64
- 展開先として使用できる書き込み可能なフォルダ
- 選択するGGUFとContext Sizeに十分なSystem RAM
- Vulkan利用時はVulkan対応GPUと正常なGPUドライバー
- Python、Git、pip、CUDA、管理者権限、手動PATH設定は不要

[公式GitHub Releases](https://github.com/tarou61300/Local-Prompt-Studio/releases/latest)からWindows Portable ZIPを取得してください。GGUFと`mmproj`は含まれません。非公式な再配布EXEは避けてください。

同じReleaseの`SHA256SUMS.txt`とZIPを照合します。PowerShellでは次を使用できます。

```powershell
Get-FileHash .\Local-Prompt-Studio-<version>-win-x64-portable.zip -Algorithm SHA256
```

## 3. 最初の10分

1. ZIPを新しい書き込み可能なフォルダへ展開します。ZIP内から直接実行しません。
2. `LocalPromptStudio.exe`を起動します。
3. 初期設定で手元のGGUFを選択します。
4. 最初は**CPU**、またはllama.cppが検出した**Vulkan GPU**を選びます。
5. **カテゴリ**、**モデル**、**バリアント**、**タスク**を選びます。
6. **Request（入力言語は自動判定）**へ内容を入力します。
7. **Promptを生成**を押します。
8. 編集可能な結果を確認し、**コピー**、**TXTで保存**、または**Prompt Library → 新規Prompt**で保存します。

## 4. 初回セットアップ

**既存のGGUFを選択**でPC上の`.gguf`を参照します。アプリはGGUFをコピーも自動ダウンロードもしません。元ファイルを移動・削除した場合は再選択が必要です。

現行の案内では、品質重視にQwen3-8B Q4_K_M、メモリが少ないPCにQwen3-4B Q4_K_Mを提示しています。これは目安であり、すべてのGGUFや量子化の互換性を保証するものではありません。

MiniMax H3だけは公式H3 Prompt Skillが必要です。オンライン時に明示的な取得操作を行うと、Portableの`data/skills`へ保存されます。Wan 2.2、LTX-2.3、Krea 2、Animaでは不要です。

迷う場合はCPUから始めます。Vulkan版llama.cppがdeviceを検出した場合はVulkanと対象deviceを選択できます。要約を確認してセットアップを完了します。

## 5. Backend、Context、Memory設定

- **CPU**は既定兼fallbackです。**CPU Threads**でthread数を指定します。
- **Vulkan GPU**では、`llama-server --list-devices`が報告した**Vulkan Device**だけを選べます。
- **GPU Offload**の既定はAutoです。詳細設定でlayer数を指定できます。
- Contextは4096、**8192 — Recommended**、16384、32768です。4096ではH3 Skill全文・入力・出力が収まらない場合があります。大きい値ほど多くのmemoryを使います。
- Available RAM／Total RAMを表示します。unified memory環境でGPU報告memoryをSystem RAMへ加算せず、二重計上しません。
- 推定memoryが不足する場合は警告します。他アプリを閉じる、Contextを下げる、より小さいGGUFを使う方法があります。
- Prompt生成とChatの最大待機時間は300秒です。

アプリは外部llama-server processを1つだけ管理します。model、backend、device、Context、threadが同じ間は再利用し、必要なときだけ再起動します。**モデルをアンロード**はアプリ管理下serverを停止してmodel memoryを解放し、次回生成時に再読込します。Cancel、timeout、アプリ終了時も、このアプリが起動したserverだけを終了します。

## 6. Prompt Generation

**カテゴリ**、**モデル**、**バリアント**、**タスク**、**Prompt変換スタイル**を選択します。主な指示は**Request**へ、対象Taskで利用できる場合は共通／開始画像／終了画像補足へ入力します。**品質タグを自動追加**はRenderer定義の品質語だけを制御し、ユーザーが入力した語は保持します。

**Promptを生成**、**キャンセル**、**再生成**を利用できます。出力は編集可能で、**コピー**、**TXTで保存**、**翻訳付き編集**、**ComfyUIへ送信**を選べます。AnimaはPositive／Negativeを別表示します。現在のBridgeが送るのはPositiveだけなので、Negativeは別にコピーしてください。

UIで選択したTaskが唯一のTask決定源です。Request本文はTaskを変更しません。I2VA／Ref2VAではTask Schema Lockを使い、別Taskのschemaが返った場合は、修復・retry・Task変更をせず結果を拒否します。メッセージには選択Taskと、利用可能なら不一致top-level fieldが表示されます。Prompt形式はProfileとRendererが決めます。Model名、`I2VA`等のTask token、schema field名は翻訳されません。

## 7. Request Guide、Literal Content、Protected Terms

**入力ガイド**はtiming、固定camera、cut、speech、visible textの例をRequestへ追記します。media fileを解析する機能ではありません。時間区間、camera／cut意図、発話、表示文字はRequestまたは対応補足へ明記してください。

完全一致させたい本文はpaired Literal形式で指定します。

```text
[speech:ja]おかえりなさい。[/speech]
[text:en]OPEN[/text]
[speech:zh]你好[/speech]
[speech:ru]Привет[/speech]
[speech:ko]안녕하세요[/speech]
```

paired blockは複数行にも対応します。従来の行頭`[speech:ja] こんにちは`形式も後方互換で、その行末までを範囲とします。paired markerが優先されます。本文は生成Promptで完全一致し、marker自体は除去されます。Protected Termsも保持されます。validation失敗時は結果を採用しません。診断は発生元、検出方式、文字数、非内容の短い識別IDを表示し、Literal本文は表示しません。Requestと折りたたまれた補足欄を確認してください。

## 8. Prompt Translation Editor

ProfileのPrompt出力言語が翻訳元、**UI言語**が翻訳先です。現在のBuiltin Profileは英語Promptを出力します。

- 日本語UI: English ↔ Japanese
- 简体中文UI: English ↔ Simplified Chinese
- Русский UI: English ↔ Russian
- 한국어 UI: English ↔ Korean
- 英語UI＋英語出力Profileでは**翻訳付き編集**を表示しません。

**構造を保護**はSkill定義構造、Protected Terms、Literal Contentを翻訳対象から分離します。Editorを開くと最初のOriginal→UI言語翻訳だけを実行し、**自動翻訳**は既定OFFのままです。ONにすると入力停止1秒後に同期し、OFFでは**翻訳を更新**で手動同期します。revision確認により古い応答は破棄されます。**適用**後にPrompt欄へ戻るのはProfile出力言語側のOriginalで、現行Builtinでは英語です。UI言語変更は再起動後に反映されます。

## 9. AIチャットとVision

AIチャットはPrompt生成GGUFを共有するか、専用GGUFを使用できます。同時常駐modelは1つで、用途切替時は現在のアプリ管理serverを停止してから必要modelを読み込みます。会話Contextは現在のchat session内で保持されます。

画像認識には、有効なChat modelに対応する`mmproj`を設定します。**＋画像**またはDrag & DropでPNG、JPG／JPEG、静止WebPを添付できます。animated WebPは非対応です。静止WebPはmemory上でPNGへ正規化してからlocalhost llama-serverへ送ります。

**通常解析**は自由なChat回答を、**Prompt参照用解析**はPrompt生成で再利用しやすいmodel非依存情報を作ります。転送previewを確認・編集し、**Prompt補足へ転送**でRequest、共通補足、開始画像補足、終了画像補足の対応先へ追記します。

画像、Chat本文、Promptは外部cloudへ送られず、アプリ管理下のlocalhost llama-serverだけで処理されます。

## 10. Prompt Library

Prompt LibraryはHistoryとは独立しています。recordはTitle、Model、Task、Tags、Prompt本文を持ちます。お気に入りと既存Tag候補でTagを再利用できます。Model、Task、Title、Tagsで検索し、複数TagはAND条件です。Tag選択時は自動検索します。最後のTagを解除したときは意図せず全件表示しないため自動検索せず、必要なら**検索**を押します。

結果rowを選ぶとPrompt詳細を表示します。metadata編集はTitle／Tagsだけを変更し、Prompt本文、Model、Task、識別子は変更しません。単一copy、checkした複数Promptのcopy、安全な削除確認に対応します。**設定**からTags表示行数、検索結果表示行数、Prompt詳細の最小行数を変更できます。

## 11. Prompt Library Datasets

従来の`data/prompt_library.sqlite3`は**Default Dataset**のままで、自動移動しません。**新規データセット**で独立Libraryを作り、selectorで切り替え、**データセットを読み込み**で有効なSQLite Libraryをmanaged copyとして取り込み、**データセットを書き出し**でportable database copyを作ります。active Datasetは保持されます。

Prompt、Tags、お気に入りTag状態はDatasetごとに分離されます。Dataset mergeは未実装です。

v3.0.0から移行する場合は両アプリを終了し、新版の**Prompt Library → データセットを読み込み**から旧版`data/prompt_library.sqlite3`を指定します。source DBは検証され、移動されません。旧release folderを上書きせずbackupとして残してください。Libraryの安全な移行にはDataset書き出し／読み込みを使用します。Settings、History、追加Dataset全体を対象にした一般的な自動移行やmergeはないため、未確認の内部file copyを前提にしないでください。

## 12. ComfyUI連携

ComfyUI連携は任意です。

1. ComfyUIを終了し、同梱`ComfyUI-Bridge/MMH3PromptBridge`を`ComfyUI/custom_nodes/MMH3PromptBridge`へコピーします。
2. ComfyUIを再起動し、browser画面を開くか再読み込みします。
3. Local Prompt Studioの**設定 → ComfyUI連携**を開きます。
4. ローカルでは`http://127.0.0.1:8188`のまま**接続テスト**を押します。
5. **ComfyUIとPairing**を押し、双方の6桁codeが一致するときだけComfyUI側で**Allow**します。
6. ComfyUIの送信先text／Prompt nodeを右クリックし、**MMH3 Prompt Bridge → Set as MMH3 Target**からSTRING／multiline STRING widgetを選びます。
7. Promptを生成・編集して**ComfyUIへ送信**を押します。

送信は現在開いているbrowser workflowの選択widgetの文字列だけを置換します。workflowをqueueせず、生成を開始せず、broadcastせず、`/prompt` APIも使いません。browser reloadやworkflow／tab変更後はtarget再選択が必要な場合があります。remote ComfyUIはHTTPS必須です。認証なしでinternet公開しないでください。保存credentialはWindows DPAPI CurrentUserで保護され、手動token操作はありません。Bridgeのbrowser JavaScript UIは英語のままです。

## 13. 設定リファレンス

- **LLMモデルパス**: Prompt生成用の既存GGUF
- **推論Backend**、**Vulkan Device**、**CPU Threads**、**GPU Offload**、**Context Size**: llama.cpp実行・memory設定
- **Skill保存場所**: MiniMax H3 Skillと更新操作
- **履歴**: 任意のlocal生成History。既定OFF
- **外観テーマ**、**UI言語**: 表示設定。言語変更は再起動後に反映
- **AIチャットモデル**、`mmproj`: Prompt model共有またはChat専用GGUF、modelごとの画像認識file
- **Prompt Library**: Tags／検索結果の表示行数とPrompt詳細最小行数
- **ComfyUI連携**: URL、接続テスト、Pairing

設定はPortable内へ保存され、GGUF自体を変更しません。

## 14. Portable Data、Privacy、Offline利用

Portable版の永続データは同じfolderの`data`内です。

- 設定: `data/config.json`
- アプリlog: `data/local-prompt-studio.log`
- llama-server log: `data/llama-server/`
- 任意History: `data/history.sqlite3`
- Default Prompt Library: `data/prompt_library.sqlite3`
- Dataset registry: `data/prompt_library_datasets.json`
- 追加Dataset: `data/prompt_library_datasets/<dataset-id>/prompt_library.sqlite3`
- 取得したH3 Skill: `data/skills/h3-prompt-writing/`
- ComfyUI credential: `data/comfyui_credentials.dat`

アプリ設定にRegistryを使わず、Windows serviceやStart Menu shortcutを作りません。終了後に展開folderを削除すれば除去できます。telemetry、広告、analyticsはありません。通常のPrompt生成は選択GGUFと`127.0.0.1`だけを使います。networkはH3 Skill取得／更新確認、公式model pageを開く操作、ComfyUI Test Connection／Pair／Send等をユーザーが明示した場合だけです。Requestと生成Promptを通常logへ保存しません。credentialはDPAPI CurrentUser保護で、共有してはいけません。必要data準備後は通常生成をoffline利用できます。

## 15. 更新とバックアップ

1. Local Prompt Studioを終了し、llama-server終了を待ちます。
2. 旧release folderをbackupとして残し、新ZIPを上書き展開しません。
3. 新版を別の書き込み可能folderへ展開します。
4. user dataは旧folderの`data`にあります。アプリ停止中にbackupしてください。
5. Prompt Libraryは旧版で**データセットを書き出し**、新版で**データセットを読み込み**ます。v3.0.0の`data/prompt_library.sqlite3`は直接読み込めます。
6. 新版確認後にだけ旧folderを削除します。Release ZIP、`SHA256SUMS.txt`、backupを保持して構いません。

cloud同期、Dataset自動merge、すべてのPortable dataを対象にした一般的な自動migrationはありません。

## 16. トラブルシューティング

- **設定を書き込めない**: userが書き込めるfolderへ展開し直します。ZIP内から実行しません。
- **モデル未設定**: 設定で既存GGUFを選びます。modelはcopyされず参照されます。
- **H3 Skill未設定**: MiniMax H3だけ、onlineで公式Skill取得を行います。他Profileには不要です。
- **Vulkan Device未検出**: GPU driverを確認・更新するかCPUへ戻ります。device判定はllama.cppに従います。
- **Context不足**: 8192、memoryに余裕があれば16384を使い、必要なら入力を減らします。
- **RAM不足**: 他アプリを閉じ、Contextを下げるか小さいGGUFを選びます。
- **生成が遅い**: CPUでは数分かかる場合があり、最大待機は300秒です。
- **Cancel後も終了中**: 所有serverの停止に数秒待ちます。必要ならアプリを閉じると、自身のserverだけを終了します。
- **ComfyUI接続失敗**: ComfyUI起動、Bridge導入、browser reload、URLを確認します。
- **ComfyUI targetなし**: nodeを右クリックしMMH3 Prompt BridgeからSTRING widgetを再選択します。

[GitHub Issues](https://github.com/tarou61300/Local-Prompt-Studio/issues)へ報告するときはWindows版、CPU／GPU、GGUF filename、backend、Context Size、正確なerror文、関連する`data/llama-server/` logを添えてください。private Request、Prompt、credential、個人情報は投稿しないでください。
