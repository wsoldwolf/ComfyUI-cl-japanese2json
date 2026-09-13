# CL Scene Limiter (Reduced Markdown)

縮小Markdownから、`scene_start_number`以降の連続した`scene_limit_count`個までのSceneをコメントごと抽出します。LLMや翻訳は使用しません。

```text
Vocal.prompt_text ──> Scene Limiter.reduced_markdown
limited_markdown ───> MV Prompt Planner.prompt_segments
```

`# サブジェクト`、`# 保持分析`、`# 共通プロンプト`はそのまま残します。`// シーン N`、`// 検出状態`、`// 楽曲セクション`、`// 歌詞`及びブロックコメントも所属Sceneと一緒に保持します。

- `scene_start_number`: 開始Scene番号、1～128
- `scene_limit_count`: 最大保持数、1～128
- `disable=True`: Markdown検証を含む制限処理を行わず完全パススルー

終端が最終Sceneを越える場合は最終Sceneで打ち切ります。開始番号が存在しない場合は停止します。詳細は[Scene Limiter仕様](../spec/cl_scene_limiter_spec.md)を参照してください。
