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

背景密度の変更は、場所や主要な構造物を削除する指定ではありません。元の場所・物体・位置関係をLLMが固定要素として分類し、Pythonが`情景の固定要素: ...`という共通バレットへ原文のまま残します。後段Plannerもこの情報を読み、最初の要求シーンに提示します。

`semantic_guard`は実験的な追加意味監査で、既定はFalseです。8Bでは正しい昼夜変更も拒否する誤検出が確認されたため、通常はFalseを推奨します。固定要素の原文コピーとPlannerへの受け渡しはFalseでも有効です。Trueでは変更行の欠落をLLMで照合し、追加推論と最大2回の意味修復を行いますが、誤検出による停止の可能性があります。

## ユーザー指示を保護する

人間が最後に確定した指示は`user_prompt`へ接続します。この内容はLLMの分類対象にせず、source拡張後に機械的にマージします。夜間等の確定時間帯と自動背景が矛盾した場合は、自動生成された背景行だけを除外します。

画風と背景を両方`passthrough`にするとGGUFをロードしません。`keep_model_loaded=False`が既定です。モデル、画風及び背景は外部STRING overrideでも変更できます。

詳細は[Prompt Enhancer仕様](../spec/cl_prompt_enhancer_spec.md)を参照してください。
