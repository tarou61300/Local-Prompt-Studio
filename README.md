# MMH3 Prompt Builder v1.0.0

**Local Prompt Builder for MiniMax H3 — Windows x64 Portable Edition**

Release date: 2026-08-10

MMH3 Prompt Builderは、日本語または英語の動画生成指示を、ローカルGGUFモデルを使って
MiniMax H3向けの英語Promptへ整える非公式コミュニティツールです。

v1.0.0はFeature Freeze済みです。CPUと、AMD / Intel / NVIDIAに対応するVulkan GPUを
利用できます。CUDA、HIP、SYCL、画像解析、ComfyUI連携は含みません。

## 動作環境

- Windows 10またはWindows 11 x64
- 書き込み可能なフォルダへZIPを展開できること
- 使用するGGUFモデルに十分なSystem RAM
- Vulkanを使う場合は、Vulkan対応GPUと正常なGPUドライバー

Python、Git、pip、llama.cpp、PySide6、Visual Studio、CUDA、PATH設定、管理者権限、
インストーラーは不要です。

## 起動方法

1. `MMH3-Prompt-Builder-v1.0.0-win-x64-portable.zip` を右クリックします。
2. 「すべて展開」を選びます。
3. Downloads、Documents、Desktop等の書き込み可能な場所へ展開します。
4. 展開された `MMH3-Prompt-Builder-v1.0.0-win-x64-portable` フォルダを開きます。
5. `MMH3PromptBuilder.exe` をダブルクリックします。

CMDまたはPowerShellを開く必要はありません。アプリとllama-serverはコンソールウィンドウを
表示せずに動作します。

## 初回セットアップ

1. 「既存のGGUFを選択」を押し、PC上の `.gguf` ファイルを選択します。
2. 「MiniMax公式H3 Prompt Skillを取得」を押します。
3. 最初は「CPU（デフォルト）」を選びます。
4. 内容を確認し、「完了」を押します。

推奨品質モデルはQwen3-8B Q4_K_Mです。メモリが少ないPCではQwen3-4B Q4_K_Mも使用できます。
GGUFはZIPへ含まれず、自動ダウンロードもされません。元のGGUFファイルを移動または削除すると、
再度選択が必要です。

MiniMax H3 Prompt SkillもZIPには含まれません。「取得」を押した場合だけ公式MiniMax
リポジトリへ接続し、ポータブルフォルダ内の `data/skills` へ保存します。設定で外部の
Skillフォルダを選んだ場合は読み取りに使用できますが、アプリ自身の取得先には使用しません。

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
- アプリログ: `data/mmh3-prompt-builder.log`
- llama-serverログ: `data/llama-server/`
- 履歴（初期状態OFF）: `data/history.sqlite3`
- 取得したSkill: `data/skills/h3-prompt-writing/`

Windowsレジストリ、Windowsサービス、スタートメニュー、デスクトップショートカットは
使用しません。完全に削除する場合は、アプリを閉じて展開したフォルダを削除してください。

通常のPrompt生成は、選択したGGUFと `127.0.0.1` のローカルllama-serverだけで完結します。
Requestや生成Promptを通常ログへ保存せず、テレメトリ、広告、アクセス解析もありません。

## オフライン利用

GGUFとSkillを準備済みなら、通常生成はオフラインで利用できます。llama-serverもoffline modeで
起動します。ネットワークを使う操作は、公式Skillの取得・更新確認、または公式モデルページを
ユーザー自身が開いた場合だけです。

## トラブルシューティング

- **この場所には設定を書き込めません**: Downloads、Documents、Desktop等へZIPを展開し直します。
- **モデル未設定**: 設定から既存GGUFを選びます。GGUFはアプリへコピーされません。
- **Skill未設定**: オンライン状態で公式Skill取得ボタンを押します。
- **Vulkan GPU未検出**: GPUドライバーを確認するか、CPUへ戻します。
- **Context Size不足**: 8192、必要に応じて16384を選びます。
- **RAM不足**: 他のアプリを閉じ、より小さいモデルまたはContextを検討します。
- **生成が遅い**: CPUでは数分かかる場合があります。最大待機時間は300秒です。
- **Cancel後も終了しない**: 数秒待ち、アプリを閉じます。所有serverは終了処理されます。

問題報告時は、Windows版、CPU/GPU名、GGUFファイル名、backend、Context、エラー文、
`data/llama-server` のログを共有してください。Request本文や個人情報は不要です。

## 整合性確認

ZIPと主要EXEのSHA256は、ZIPと同じ場所の `SHA256SUMS.txt` に記録されます。PowerShellを利用
できる上級者は、次のコマンドでZIPを確認できます。

```powershell
Get-FileHash .\MMH3-Prompt-Builder-v1.0.0-win-x64-portable.zip -Algorithm SHA256
```

## ライセンス

MMH3 Prompt Builder本体はMIT Licenseです。`LICENSE`、`THIRD_PARTY_LICENSES.md`、
`licenses` フォルダを参照してください。MiniMax公式製品ではありません。
