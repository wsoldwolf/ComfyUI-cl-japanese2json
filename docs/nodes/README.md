# カスタムノードガイド

## MV自動生成の主経路

```text
Image Analyzer ──> Prompt Merger ──> Prompt Enhancer ──┐
                                                       ├─> MV Prompt Planner ──> Japanese to JSON ──> H3 Plan
Vocal to Prompt Segments ──> Scene Limiter ────────────┘

Full Mix + Vocal ──> Audio Pad Pair ──> H3 Audio Tracks
```

## ノード一覧

| ノード | 用途 |
| --- | --- |
| [CL Image Analyzer](image-analyzer.md) | 参照画像をVision GGUFで解析し、画像とPlanningBriefを出力 |
| [CL Prompt Merger](prompt-merger.md) | 二つのグローバル縮小Markdownを機械的に統合 |
| [CL Prompt Enhancer](prompt-enhancer.md) | 画風プロファイルと背景密度を適用 |
| [CL Vocal to Prompt Segments](vocal-to-prompt-segments.md) | ボーカル、Lyrics、WhisperからSceneとSRTを生成 |
| [CL MV Prompt Planner](mv-prompt-planner.md) | 歌詞に応じた人物動作、情景、カメラを計画 |
| [CL Japanese to JSON](japanese-to-json.md) | 縮小MarkdownをH3 Plan JSONへコンパイル |
| [CL Scene Limiter](scene-limiter.md) | テスト対象の連続Scene範囲を抽出 |
| [CL Audio Pad / Pair](audio-pad.md) | Source Timeline用にPCM無音を追加 |
| [CL Load Text File](text-file.md) | 任意場所のUTF-8テキストをD&D入力 |
| [CL String Combo](string-combo.md) | プロパティーで定義した文字列候補を選択 |
| [CL Connected Combo](connected-combo.md) | 接続先COMBOの候補へ自動追従 |
| [CL 32-bit Seed](seed32.md) | GGUFとH3へ同じ保存可能な32-bit seedを供給 |
