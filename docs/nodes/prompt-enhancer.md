# CL Prompt Enhancer (GGUF)

Prompt Merger等のグローバル縮小Markdownへ、外部画風プロファイルと背景密度を適用します。Subject、保持分析、Markdown構造及び画風文の挿入はPythonが管理します。

```text
Prompt Merger.merged_markdown ──> source_markdown ┐
ユーザー最終指示 ───────────────> user_prompt     ├─> enhanced_markdown
Connected Combo ────────────────> 各override ─────┘
```

## 画風と背景

`style_profile`には年代別アニメ、映画実写、写真実写、各種スケッチ、水彩画、イラスト、マスターピース及び`passthrough`があります。アニメ系は人物をキーポーズ、二コマ／三コマ打ち及び限定的な中割りによる手描きリミテッドアニメへ拘束し、3DCG、フルアニメ、Live2D、ボーンリグ、パペット、トゥイーン及び連続モーフィング風の動きを除外します。

`background_detail`は`reduce`、`low`、`medium`、`high`、`ultra`、`passthrough`です。プロファイルは`node_prompt_enhancer/prompts/`以下のmanifestから検出します。

## ユーザー指示を保護する

人間が最後に確定した指示は`user_prompt`へ接続します。この内容はLLMの分類対象にせず、source拡張後に機械的にマージします。夜間等の確定時間帯と自動背景が矛盾した場合は、自動生成された背景行だけを除外します。

画風と背景を両方`passthrough`にするとGGUFをロードしません。`keep_model_loaded=False`が既定です。モデル、画風及び背景は外部STRING overrideでも変更できます。

詳細は[Prompt Enhancer仕様](../spec/cl_prompt_enhancer_spec.md)を参照してください。
