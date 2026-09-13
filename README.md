# ComfyUI-cl-japanese2json

[![千年鳥居 - MiniMax H3 MV Generator Demo (Qwen3 8B)](https://img.youtube.com/vi/WU7u8sk0n-U/maxresdefault.jpg)](https://www.youtube.com/watch?v=WU7u8sk0n-U)

参照画像、楽曲、ボーカルステム及び歌詞から、歌詞に同期した人物動作、情景、カメラワーク、リップシンク及びMiniMax H3用プロンプトを生成し、ミュージックビデオ制作を自動化するComfyUIカスタムノード集です。上の動画はQwen3 8Bを使用した自動MV生成例です。

## できること

- Vision GGUFで参照画像を解析し、人物の外観と情景を日本語PlanningBriefへ変換
- 人間が指定したSubject、保持条件、世界観及び禁止事項を保護しながらプロンプトを統合・拡張
- ボーカルステムとLyricsをWhisperで整列し、Scene、SRT及びSource Vocalリップシンクを生成
- 歌詞に応じた具体的な人物動作、付加映像及びH3カメラワークをGGUFで計画
- 日本語縮小Markdownを検証済みMiniMax H3 Contex-Loop Plan JSONへコンパイル
- MiniMax H3のRef2VAをScene単位で連続生成し、フルミックスを維持したMVへ結合
- Scene範囲限定、音声尺の安全なPCM無音補完、モデル／プロファイルの外部切替

LLMが最終JSONやMarkdown構造を自由生成する設計ではありません。翻訳、画像観測、背景補足及びScene計画を行指向の検証可能な形式で受け取り、参照タグ、時間軸、音響、保持規則及びJSONはPythonが決定論的に組み立てます。

## MV自動生成フロー

```text
参照画像 ──> Image Analyzer ──> Prompt Merger ──> Prompt Enhancer ─┐
                                                                    ├─> MV Prompt Planner
ボーカル + Lyrics ──> Vocal to Prompt Segments ──> Scene Limiter ─┘
                                                                        │
                                                                        v
                                                          Japanese to JSON
                                                                        │
フルミックス + ボーカル ──> Audio Pad Pair ──> MiniMax H3 Ref2VA ─────┤
                                                                        v
                                                                  完成MV
```

## クイックスタート

1. [導入ガイド](docs/installation.md)に従い、本プロジェクト、`llama-cpp-python`、GGUF、Whisper及び必要なH3ノードを配置します。
2. `workflows/minimax_h3_ref2va_integrated_mv_generator_short.json`をComfyUIで開きます。
3. 参照画像、フルミックス、同尺・同起点のボーカルステム、Suno Lyrics及び使用モデルを選択します。
4. 短時間版で一連の処理とシアン色の成功ログを確認します。
5. 通常版`workflows/minimax_h3_ref2va_integrated_mv_generator.json`へ素材を設定し、全Sceneを生成します。

ワークフローごとの用途は[ワークフローガイド](workflows/README.md)を参照してください。`workflows/development/`以下はノードと仕様の検証用であり、動作を保証しません。

## 主なカスタムノード

| ノード | 用途 |
| --- | --- |
| [CL Image Analyzer (Vision GGUF)](docs/nodes/image-analyzer.md) | 画像を解析し、Ref2V用IMAGEとPlanningBriefを出力 |
| [CL Prompt Merger (Reduced Markdown)](docs/nodes/prompt-merger.md) | Subject、保持分析及びCommonを機械的に統合 |
| [CL Prompt Enhancer (GGUF)](docs/nodes/prompt-enhancer.md) | 外部画風プロファイルと背景密度を適用 |
| [CL Vocal to Prompt Segments](docs/nodes/vocal-to-prompt-segments.md) | ボーカルとLyricsからScene、SRT及び検証JSONを生成 |
| [CL MV Prompt Planner (GGUF)](docs/nodes/mv-prompt-planner.md) | 歌詞に応じた人物動作、情景及びカメラを計画 |
| [CL Japanese to JSON (GGUF)](docs/nodes/japanese-to-json.md) | 日本語縮小MarkdownをH3 Plan JSONへコンパイル |
| [CL Scene Limiter (Reduced Markdown)](docs/nodes/scene-limiter.md) | デバッグ対象の連続Scene範囲を抽出 |
| [CL Audio Pad / Pair](docs/nodes/audio-pad.md) | Source TimelineをH3安全尺へPCM無音補完 |
| [CL Load Text File](docs/nodes/text-file.md) | 任意場所のUTF-8テキストをD&D入力 |
| [CL String Combo](docs/nodes/string-combo.md) | ユーザー定義文字列リストから値を選択 |
| [CL Connected Combo](docs/nodes/connected-combo.md) | 接続先COMBOの候補へサブグラフ越しに追従 |

すべてのガイドは[カスタムノード一覧](docs/nodes/README.md)から参照できます。

## ドキュメント

| 文書 | 内容 |
| --- | --- |
| [ドキュメント索引](docs/README.md) | 利用目的別の入口 |
| [導入ガイド](docs/installation.md) | 必要環境、モデル配置、Windowsビルド及び導入 |
| [ワークフローガイド](workflows/README.md) | 通常版、短時間版及び入力素材 |
| [縮小Markdownコンパイラガイド](docs/compiler-guide.md) | Scene、Shot、参照、台詞、音響及びJSON接続 |
| [トラブルシューティング](docs/troubleshooting.md) | よくあるエラー、再試行及びデバッグ出力 |
| [実装仕様一覧](docs/spec/README.md) | ノード契約と決定論的処理の正本 |
| [開発・検証用ワークフロー](workflows/development/README.md) | 動作を保証しない検証用スナップショット |

## 必要環境

- Python 3.11以降を使用するComfyUI
- テキスト用GGUF及び必要に応じてVision GGUFと対応する`mmproj`
- 使用環境向けに手動導入した`llama-cpp-python`
- Vocal解析を使う場合は`openai-whisper`とローカル`.pt`モデル
- MiniMax H3及び統合ワークフローが参照する関連カスタムノード

本プロジェクトはモデルやPython依存関係を自動ダウンロード・更新しません。詳細とWindows向けCUDAビルド例は[導入ガイド](docs/installation.md)にあります。

## テスト

実GGUFやGPUを使わない自動テストをプロジェクトルートから実行できます。

```text
python -m unittest discover -s tests -t . -v
```

## ライセンスと参考元

このプロジェクトは[GNU General Public License v3.0](LICENSE)で配布します。

実装検討ではComfyUI、`llama-cpp-python`、MiniMax H3の公式プロンプト文書及びComfyUI-QwenVL-Modを参考にしています。本実装は独立しており、ComfyUI-QwenVL-Modをimportせず実行時依存しません。

- https://github.com/huchukato/ComfyUI-QwenVL-Mod
- https://github.com/abetlen/llama-cpp-python
- https://docs.comfy.org/custom-nodes/backend/server_overview
- https://docs.comfy.org/custom-nodes/backend/datatypes

`llama-cpp-python`自体はMIT Licenseです。
