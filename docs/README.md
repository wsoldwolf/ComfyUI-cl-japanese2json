# ドキュメント

このディレクトリには、ComfyUI-cl-japanese2jsonの利用ガイドと実装仕様を目的別に収録しています。

## はじめに

- [導入ガイド](installation.md): 必要環境、Windowsでの`llama-cpp-python`ビルド、モデル配置及びComfyUIへの導入
- [ワークフローガイド](../workflows/README.md): 推奨MV生成ワークフローと短時間確認版
- [縮小Markdownコンパイラガイド](compiler-guide.md): 入力文法、Scene、Shot、参照、音響及びJSON生成
- [トラブルシューティング](troubleshooting.md): よくある導入・推論・検証エラーとデバッグ出力

## カスタムノード

- [ノードガイド一覧](nodes/README.md)
- [CL Image Analyzer (Vision GGUF)](nodes/image-analyzer.md)
- [CL Prompt Merger (Reduced Markdown)](nodes/prompt-merger.md)
- [CL Prompt Enhancer (GGUF)](nodes/prompt-enhancer.md)
- [CL Vocal to Prompt Segments](nodes/vocal-to-prompt-segments.md)
- [CL MV Prompt Planner (GGUF)](nodes/mv-prompt-planner.md)
- [CL Japanese to JSON (GGUF)](nodes/japanese-to-json.md)
- [CL Scene Limiter (Reduced Markdown)](nodes/scene-limiter.md)
- [CL Audio Pad / CL Audio Pad Pair](nodes/audio-pad.md)
- [CL Load Text File (Drag & Drop)](nodes/text-file.md)
- [CL String Combo](nodes/string-combo.md)
- [CL Connected Combo](nodes/connected-combo.md)

## 開発者向け

- [実装仕様一覧](spec/README.md): 入出力契約、検証規則及び決定論的処理の正本
- [開発資料](development/README.md): 過去の実装依頼等、利用者向けではない資料
- [開発・検証用ワークフロー](../workflows/development/README.md): 動作を保証しないノード検証用ワークフロー
