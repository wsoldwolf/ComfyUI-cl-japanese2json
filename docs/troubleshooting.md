# トラブルシューティング

## モデルが見つからない

- テキストGGUFは`ComfyUI/models/LLM/GGUF/`以下へ配置します。
- Visionでは本体GGUFと対応する`mmproj` GGUFを同じモデルディレクトリへ配置します。
- Whisperは公式`.pt`を`ComfyUI/models/whisper/`以下へ配置します。
- 配置後はComfyUIを再起動します。
- 外部STRINGからモデルを指定する場合は、ノードのモデルCOMBOに表示される相対IDと完全一致させ、先頭へ`/`を付けないでください。

## Python依存関係

- `llama-cpp-python`をimportできない場合は、ComfyUIが使用するPythonへ対応版を手動導入します。
- Visionで`MTMDChatHandler`がない場合は、Vision対応版の`llama-cpp-python`が必要です。
- Whisperをimportできない場合は`openai-whisper`を導入します。同名の別パッケージは使用できません。
- `Windows Error 0xc000001d`は、実行CPUが非対応の命令を含むwheelで起こることがあります。[導入ガイド](installation.md)の`GGML_NATIVE=OFF`、AVX-512/AMX無効化設定で再ビルドしてください。

## ComfyUIで入力値がずれる

ノードの入力順序を変更した版で古いワークフローを開くと、保存済みwidget値が別の入力へ割り当てられることがあります。最新版へ更新後にComfyUIとブラウザを再起動し、ワークフローを読み直してください。値が不正な場合は、同じノードを新規配置して値を比較します。

## コンパイラ

- コンテキスト不足: `n_ctx`又は`max_tokens`を見直します。
- プレースホルダ欠落: `save_debug_output=True`で生応答を確認します。`retry_max`を増やすだけで恒常的な省略が直るとは限りません。
- 台詞エラー: 台詞より前の同じShotバレットへ`<Subject N>`を書き、`発声: 指定台詞のみ`を許可します。`(Sx)`は入力しません。
- リップシンクエラー: 台詞指定と参照音声方式を混在させず、それぞれ対応する`発声`を指定します。
- BGMエラー: 既存Audioは`BGM再利用`、生成音楽は`BGM`を使用し、同時に指定しません。
- Shotエラー: 最初は`## ショット`、2個目以降は昇順の`## ショット N秒`です。
- コメントエラー: `/* ... */`の入れ子、閉じ忘れ、対応しない`*/`及びHTMLコメントを確認します。

## VocalとLyrics

`self test failed`は、入力Lyrics本文と出力SRT本文の件数、順序又は文字列が一致していない状態です。現在のノードは不完全な出力を返さず停止し、失敗結果を正常キャッシュしません。

- ボーカルステムとLyricsの言語を確認します。
- `language`は日本語なら`ja`、英語なら`en`です。
- 音源先頭、Lyrics行順、重複歌詞及びWhisperが認識できない発声を確認します。
- 必要に応じて`lyrics_match_threshold`、`lyrics_neighbor_threshold`、`condition_on_previous_text`を調整します。
- 末尾パディング警告が出た場合はフルミックスとボーカルを`CL Audio Pad Pair`へ接続し、`pad_position=end`を使用します。

## Plannerの再試行

Plannerは有効なSceneを保持し、不正なSceneだけを再試行します。同じSceneが上限まで残る場合、最後の検証理由を確認してください。

- 8Bモデルには`lyric_visuals_light_8b`又は`performance_only`を使用します。
- `lyric_visuals_full`は14B以上を推奨します。
- `camera_guard`と`vocal_guard`は既定の`warn`で問題箇所を残し、`strict`では該当Sceneを再試行します。
- `save_debug_output=True`で最終部分状態とLLM生応答を保存できます。

## VisionとEnhancer

Visionの4Bモデルは、行指向形式のフィールド数、カテゴリ、可視性又は日本語化に失敗する場合があります。現在の実装は修復用LLMプロンプトを使って再試行します。繰り返し失敗する場合は`save_debug_output=True`で生応答と修復要求を確認し、8B Visionモデルも試してください。

Enhancerでは人間が確定した指示を`user_prompt`へ接続します。時間帯等の確定条件が自動背景と矛盾する場合、人物・動作・カメラ・音響を含まない自動背景行だけが除外されます。ユーザー指示自体はLLMの分類対象になりません。

## デバッグ出力

| ノード | 保存先 |
| --- | --- |
| Japanese to JSON | `ComfyUI/output/cl_japanese2json_debug/` |
| MV Prompt Planner | `ComfyUI/output/cl_mv_prompt_planner_debug/` |
| Image Analyzer | `ComfyUI/output/cl_vision_analyzer_debug/` |
| Prompt Enhancer | `ComfyUI/output/cl_prompt_enhancer_debug/` |

デバッグ出力には入力、プロンプト、LLM生応答等が含まれます。共有前に内容を確認してください。
