# Local Prompt Studio v3.1.0

**Windows x64 portable stable release**

Release date: 2026-08-28

Repository: [https://github.com/tarou61300/Local-Prompt-Studio](https://github.com/tarou61300/Local-Prompt-Studio)

Download: [Latest release](https://github.com/tarou61300/Local-Prompt-Studio/releases/latest)

Local Prompt Studioは、ローカルGGUFモデルを使うプロファイル駆動型Prompt変換ツールです。
対応する動画モデルプロファイルはMiniMax H3、Wan 2.2、LTX-2.3です。
画像モデルプロファイルはKrea 2 Raw/TurboとAnima Base/Aesthetic/Turboに対応します。Animaでは入力をNatural / Tag / Hybridとして内部判定し、「品質タグを自動追加」がONの場合だけ、モデル推奨の品質系Positive/NegativeをProfileから決定的に組み立てます。

UI言語はEnglishと日本語を選択でき、入力言語とは独立しています。ComfyUIをインストールして
いない場合も、従来のローカルMiniMax H3 Prompt生成をそのまま利用できます。既存の
MMH3 Prompt Bridge v1.2との後方互換性も維持します。

Local Prompt Studioでは、Prompt Generation、AI Chat、Prompt Libraryの3つのメインモードを利用できます。Prompt生成と
同じGGUFを共有するか、AI Chat専用GGUFを選択できます。アプリが常駐させるモデルは1つだけで、
用途を切り替えると既存のllama-server管理機構が必要なモデルへ安全に切り替えます。読み込み済み
モデルは「モデルをアンロード」から停止でき、次回のChatまたはPrompt生成時に再読み込みします。

対応するmmprojをGGUFごとに任意設定すると、画像添付、Drag & Drop、thumbnail表示、通常解析、
**Prompt Reference Analysis（Prompt参照用解析）**をローカルVLMで利用できます。参照用解析は
モデル非依存の準構造化情報を生成し、編集プレビューからRequest、全体、開始画像、終了画像の補足へ
追記できます。Requestと各補足の役割はPrompt生成LLMと選択Rendererまで分離して渡されます。

AI Chatの画像添付はPNG、JPG／JPEG、静止WebPに対応します。Animated WebPは非対応で、
静止WebP、PNG、JPEGを使用するよう画面に案内します。

Prompt Libraryには完成したPromptをTitle、Model、Task、Tagsとともに保存できます。Model、Task、複数の
AND条件Tags、任意のTitleで検索でき、Favorite Tags、既存Tagの検索と再利用、Title/Tags metadata編集、
単一・複数Promptのコピー、安全な削除に対応します。Prompt本文は保存時の内容を完全に維持し、metadata編集では
Prompt本文、Model、Task、UUIDを変更しません。LibraryはHistoryとは独立したローカルSQLite databaseを使用します。

Tagを選択すると検索結果が自動更新され、複数TagではAND条件が即時反映されます。最後のTagを解除した場合は
全件を自動表示しません。検索結果rowを選択するとPrompt詳細を表示します。Tags、Results、Prompt詳細の表示行数は
Settingsから変更でき、既定値はTags 3段、Results 3行、Prompt詳細最低10行です。

Dataset selectorから独立したPrompt Library Datasetを新規作成、読み込み、書き出しできます。従来の
`data/prompt_library.sqlite3`はDefault Datasetとしてそのまま利用され、Dataset同士は自動結合されません。

Prompt生成では、意味カテゴリ語に対する誤検出しやすいhard rejectionを廃止し、Literal Contentと
Protected Termsの厳密保持を維持したまま、Renderer instructionによるintent preservationを強化しました。
MiniMax H3では、明示されたvisual medium、camera/cut、開始・終了を含むtemporal intervalを保持し、
時間表記や構造は現在のSkill/Profileをauthorityとして扱います。

Request付近の入力ガイドから時間・カメラ・カット・発話・表示文字の例を追記できます。MiniMax H3では
映像スタイルを未指定／2Dアニメーション／実写／3D CGから選び、選択内容をRequest先頭へ可視的に
追加できます。生成済みPromptはTranslation EditorでOriginalと日本語訳を並べて編集でき、構造保護、
保護範囲highlight、手動翻訳更新、任意の自動翻訳に対応します。

## Theme

設定画面の「外観テーマ」から、通常またはダークを選択できます。選択したThemeはポータブル設定へ
保存され、保存時に即時適用されるほか、次回起動時にも復元されます。旧設定のSystem／Light／Dark値は
安全に通常／ダークへ移行されます。ダークThemeでは各UI部品のコントラストを調整し、RequestとPromptの
入力欄は通常時とフォーカス時で枠線を切り替えて、編集対象を判別しやすくしています。

## Profile architecture

Builtin Profile、将来のOfficial Update、Custom ProfileをUTF-8のJSON／Markdown／textデータとして
読み込みます。Profileはコードを実行せず、Python、JavaScript、PowerShell、実行ファイル、DLL、
絶対パス、path traversalを許可しません。詳細は`docs/PROFILE_SCHEMA_V1.md`を参照してください。

MiniMax H3、Wan 2.2、LTX-2.3、Krea 2、Animaは、それぞれ独立した自己完結Rendererを使用します。
UIにRenderer選択はなく、Profile切替時に内部で自動選択されます。正式なLiteral Content形式は
`[speech:ja]おかえりなさい。[/speech]`と`[text:en]OPEN[/text]`で、複数行blockにも対応します。
従来の行頭`[speech:ja] ...` / `[text:en] ...`も後方互換で認識します。生成後は本文の完全一致を
検証し、marker自体は出力しません。`length_guidance`は助言のみで、切り詰め、padding、圧縮用の再生成は行いません。
Literal Content validationに失敗した場合、v2.1.2では検出総数、Missing数、Request／各補足の発生元、
paired／legacy／quoteの検出方式、文字数、SHA-256由来の短い識別IDを表示します。Literal本文自体は
診断表示、signal、通常ログへ格納しません。この診断追加は案内改善のみであり、Literal検出条件と
完全一致validationは従来どおりです。

Target Profileで選択したTaskが生成modeの唯一の決定元です。Request本文は生成したい内容として扱われ、
「開始画像」「first frame」「Ref2VAとして処理」など別Taskを示唆する記述が含まれても、選択Taskを変更・再判定しません。
I2VA / Ref2VAでは生成後にもTask Schema Lockを適用し、モデルが選択Taskと異なるschemaを返した場合は、
完成Promptとして採用せずCopy / Sendを無効のままにします。画面には選択Task、不一致field、TaskまたはRequestの確認方法を表示します。
Request本文の自動変更、Taskの自動変更、schemaの自動修復、自動retryは行いません。

AnimaはNatural / Tag / Hybridを入力から自動判定し、Base v1.0、Aesthetic v1.1、Turbo v1.0を選択できます。
Aestheticを含む全Anima Variantで、「品質タグを自動追加」がONの場合に自動付与する固定componentは品質系だけです。OFFの場合は品質componentを自動付与せず、ユーザーが明示した品質タグはどちらの場合も保持します。`safe` / `sensitive` / `nsfw` / `explicit`、
`score_*`、artist、character/copyright、age/demographic、year/era系タグは、ユーザーが明示した場合だけ保持します。AnimaではPositiveとNegativeを
別々に表示します。既存ComfyUI Bridgeは送信先が1つだけなので、Animaで「Send to ComfyUI」を押した場合は
Positive Promptだけを送信し、Negative Promptは手動コピーします。

## 動作環境

- Windows 10またはWindows 11 x64
- 書き込み可能なフォルダへZIPを展開できること
- 使用するGGUFモデルに十分なSystem RAM
- Vulkanを使う場合は、Vulkan対応GPUと正常なGPUドライバー

Python、Git、pip、llama.cpp、PySide6、Visual Studio、CUDA、PATH設定、管理者権限、
インストーラーは不要です。

## 起動方法

1. `Local-Prompt-Studio-v3.1.0-win-x64-portable.zip`を、書き込み可能なフォルダへ展開します。
2. 展開した`Local-Prompt-Studio-v3.1.0-win-x64-portable`フォルダ内の`LocalPromptStudio.exe`を起動します。

既存v1.xリリースは上書きせず、別フォルダへ展開してください。

CMDまたはPowerShellを開く必要はありません。アプリとllama-serverはコンソールウィンドウを
表示せずに動作します。

## 初回セットアップ

1. 「既存のGGUFを選択」を押し、PC上の `.gguf` ファイルを選択します。
2. MiniMax H3を使用する場合だけ「MiniMax公式H3 Prompt Skillを取得」を押します。Wan 2.2、LTX-2.3、Krea 2、Animaでは不要です。
3. 最初は「CPU（デフォルト）」を選びます。
4. 内容を確認し、「完了」を押します。

推奨品質モデルはQwen3-8B Q4_K_Mです。メモリが少ないPCではQwen3-4B Q4_K_Mも使用できます。
GGUFはZIPへ含まれず、自動ダウンロードもされません。元のGGUFファイルを移動または削除すると、
再度選択が必要です。

MiniMax H3 Prompt SkillもZIPには含まれません。「取得」を押した場合だけ公式MiniMax
リポジトリへ接続し、ポータブルフォルダ内の `data/skills` へ保存します。設定で外部の
Skillフォルダを選んだ場合は読み取りに使用できますが、アプリ自身の取得先には使用しません。

## AI Chatとローカル画像解析

- AI Chatはセッション中の会話Contextを保持し、通常のPrompt生成とは独立して利用できます。
- Prompt生成と同じGGUF、またはAI Chat専用GGUFを設定できます。GGUFはユーザーが選択し、配布物には含まれません。
- 画像認識は対応するmmprojをモデルごとに設定した場合だけ有効です。mmprojも配布物には含まれません。
- 画像は外部サービスへuploadせず、アプリが管理するlocalhostのllama-serverへだけ送信します。
- 「通常解析」は自由なChat回答を生成し、「Prompt参照用解析」は後段のPrompt生成へ再利用しやすいモデル非依存情報を生成します。
- AI回答の転送時は不要な挨拶等を整理し、転送内容をユーザーが編集してから各入力欄へ追記できます。
- Chat本文、画像base64、Request、生成Promptは通常ログへ記録しません。

## 既知の制限

- GGUFモデルは同梱されません。ユーザーが互換GGUFを別途用意して選択する必要があります。
- MMH3 Prompt BridgeによるComfyUI連携は任意機能で、ComfyUI側へBridgeを別途導入する必要があります。
- 実際に生成される画像・動画の品質は、対象モデル、workflow、sampler等の設定に依存します。
- Literal ContentはPrompt文字列の完全一致を検証しますが、対象モデルによる描画・発音の成功までは保証しません。

## ComfyUI連携（任意）

Local Prompt StudioはComfyUIなしでも利用できます。連携を試す場合、配布物に含まれる
`ComfyUI-Bridge/MMH3PromptBridge` フォルダを、次の場所へフォルダごとコピーしてください。

```text
ComfyUI/custom_nodes/MMH3PromptBridge
```

1. ComfyUIを終了し、Bridgeフォルダを上記の場所へコピーします。
2. ComfyUIを再起動し、ComfyUIのブラウザ画面を開くか再読み込みします。
3. Local Prompt Studioの「設定」→「ComfyUI Integration」を開きます。
4. ローカルComfyUIでは、既定URL `http://127.0.0.1:8188` を使用します。
5. 「Test Connection」を押します。
6. 「Pair with ComfyUI」を押します。
7. Local Prompt StudioとComfyUIに表示された6桁コードが一致することを確認します。
8. 一致している場合だけ、ComfyUI側で「Allow」を押します。
9. 送信先にするテキスト／Promptノードを右クリックします。
10. 「MMH3 Prompt Bridge」→「Set as MMH3 Target」→対象の `text` を選びます。
11. Local Prompt StudioでPromptを生成し、必要なら出力欄を手動編集します。
12. 「Send to ComfyUI」を押し、選択した欄の文字列が変わることを確認します。

Local Prompt StudioはComfyUIのworkflowをキューへ追加せず、画像・動画生成を開始しません。`/prompt` APIも
使用しません。「Paired」は現在のURLに対応する有効なローカル保存資格情報があるという意味で、
ComfyUIが現在オンラインであることを示すものではありません。

Local Prompt Studioを再起動してもペアリング資格情報は保持されます。ComfyUIまたはブラウザを再起動・再読み込み
した後は、送信先ターゲットを再選択する必要がある場合があります。Bearer token、client credential、
Authorization headerを手動でコピーする手順はありません。リモートComfyUIにはHTTPSが必須で、
ComfyUI自体を認証なしでインターネット公開しないでください。

テスト結果の報告項目と、共有してはいけない機密情報については
`COMMUNITY_TEST_CHECKLIST.md` を参照してください。

## CPU / Vulkan GPU

- **CPU**: デフォルト兼フォールバックです。
- **Vulkan GPU (AMD / Intel / NVIDIA)**: Vulkan版llama.cpp自身が検出したGPUだけを選べます。
- **GPU Offload**: デフォルトはAutoです。Advanced設定でレイヤー数を指定できます。

GPUが見つからない場合もCPUは使用できます。Windowsのデバイス名だけからGPU対応を推測せず、
`llama-server --list-devices` の結果を使用します。統合GPUのメモリをSystem RAMと二重計上
しないよう、GPU報告メモリは情報表示だけに使います。

## Context SizeとRAM

標準は **8192 — Recommended** です。

- 4096 — Low Memory（MiniMax H3 Skill全文が収まらない場合があります）
- 8192 — Recommended
- 16384 — Large
- 32768 — Very Large / high memory

画面には `RAM: Available ... / Total ...` と表示します。Availableは現在利用可能な物理RAM、
Totalは搭載物理RAMです。起動、モデル変更、Context変更、Generate直前、生成終了・Cancel時に
再測定し、Generateの安全判定はキャッシュされた古い値を使用しません。

生成前に入力token数、出力予算、safety marginがContextへ収まるか確認します。Context不足や
RAM不足の場合は日本語で説明します。CPU/GPU生成は最大300秒待機します。

## Prompt Cacheとserver lifecycle

llama.cpp Prompt Cacheは有効です。同じモデル・backend・device・Context・thread設定で通常の
Generateを繰り返す場合、アプリ所有のllama-serverを再利用します。これらの起動設定を変更した
場合だけserverを安全に再起動します。

Cancel、timeout、アプリ終了時には、このアプリが起動したllama-serverだけを終了します。

## Portable Modeとプライバシー

配布ZIPではPortable Modeが常に有効です。`--portable-data` を指定しても保存先は変更されません。
アプリが作成するデータはすべて、EXEと同じ
ポータブルフォルダ内の `data` に保存します。

- 設定: `data/config.json`
- アプリログ: `data/local-prompt-studio.log`
- llama-serverログ: `data/llama-server/`
- 履歴（初期状態OFF）: `data/history.sqlite3`
- Prompt Library: `data/prompt_library.sqlite3`（Prompt Libraryを初めて開いた時に作成）
- Prompt Library Dataset registry: `data/prompt_library_datasets.json`
- 追加Prompt Library Dataset: `data/prompt_library_datasets/<dataset-id>/prompt_library.sqlite3`
- 取得したSkill: `data/skills/h3-prompt-writing/`
- ComfyUIペアリング資格情報: `data/comfyui_credentials.dat`（Windows DPAPI CurrentUserで保護）

Windowsレジストリ、Windowsサービス、スタートメニュー、デスクトップショートカットは
使用しません。完全に削除する場合は、アプリを閉じて展開したフォルダを削除してください。

通常のPrompt生成は、選択したGGUFと `127.0.0.1` のローカルllama-serverだけで完結します。
Requestや生成Promptを通常ログへ保存せず、テレメトリ、広告、アクセス解析もありません。
ComfyUIへの通信は、ユーザーがTest Connection、Pair、Sendを明示的に実行した場合だけ発生します。

## オフライン利用

GGUFと選択Profileに必要な依存データを準備済みなら、通常生成はオフラインで利用できます。llama-serverもoffline modeで
起動します。ネットワークを使う操作は、公式Skillの取得・更新確認、公式モデルページを
ユーザー自身が開いた場合、またはComfyUIのTest Connection・Pair・Sendを明示的に実行した場合だけです。

## トラブルシューティング

- **この場所には設定を書き込めません**: Downloads、Documents、Desktop等へZIPを展開し直します。
- **モデル未設定**: 設定から既存GGUFを選びます。GGUFはアプリへコピーされません。
- **H3 Skill未設定**: MiniMax H3を使用する場合は、オンライン状態で公式Skill取得ボタンを押します。Wan 2.2、LTX-2.3、Krea 2、Animaには不要です。
- **Vulkan GPU未検出**: GPUドライバーを確認するか、CPUへ戻します。
- **Context Size不足**: 8192、必要に応じて16384を選びます。
- **RAM不足**: 他のアプリを閉じ、より小さいモデルまたはContextを検討します。
- **生成が遅い**: CPUでは数分かかる場合があります。最大待機時間は300秒です。
- **Cancel後も終了しない**: 数秒待ち、アプリを閉じます。所有serverは終了処理されます。
- **ComfyUIへ接続できない**: ComfyUIが起動していること、Bridgeをインストールしてブラウザを再読み込みしたこと、URLを確認します。
- **送信先がない**: ComfyUIの対象ノードを右クリックし、MMH3 Prompt Bridgeから対象のSTRING欄を選び直します。

問題報告時は、Windows版、CPU/GPU名、GGUFファイル名、backend、Context、エラー文、
`data/llama-server` のログを共有してください。Request本文や個人情報は不要です。

## 整合性確認

ZIPと主要EXEのSHA256は、ZIPと同じ場所の `SHA256SUMS.txt` に記録されます。PowerShellを利用
できる上級者は、次のコマンドでZIPを確認できます。

```powershell
Get-FileHash .\Local-Prompt-Studio-<version>-win-x64-portable.zip -Algorithm SHA256
```

## ライセンス

Local Prompt Studio本体はMIT Licenseです。`LICENSE`、`THIRD_PARTY_LICENSES.md`、
`licenses` フォルダを参照してください。MiniMax公式製品ではありません。
