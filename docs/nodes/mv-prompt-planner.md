# CL MV Prompt Planner (GGUF)

Vocalノードの固定タイムラインと歌詞コメントを保ったまま、ショット構図、人物動作、環境及びH3カメラ運動を計画します。出力は既存コンパイラで再検証できる日本語縮小Markdownです。

```text
Vocal.prompt_text ───────────────> prompt_segments
Planning Markdown ──────────────> planning_markdown
planned_markdown ───────────────> Japanese to JSON.plain_text
```

Planning Markdownには`# サブジェクト`、任意の`# 保持分析`及び任意の`# 共通プロンプト`だけを書きます。人物、変えてはいけない特徴、全編の画風・世界・禁止事項を簡潔に指定し、Scene、Shot及び音響はVocal出力へ任せます。

## 視覚拡張プロファイル

- `performance_only`: 人物演技、既存環境、照明、カメラを中心に計画
- `lyric_visuals_light_8b`: 8B向け。Sceneごとに限定した付加映像を追加
- `lyric_visuals_full`: 14B以上推奨。より豊富な象徴物、軌道、状態変化、前景遷移等を許可

8Bモデルで`lyric_visuals_full`を選ぶと重大WARNINGを表示しますが、自動変更はしません。モデルとプロファイルは外部STRING overrideからも指定できます。

## 再試行とguard

Plannerは最初にSong Bibleを作り、その後Sceneをバッチ計画します。有効Sceneを保持し、欠落又は不正なSceneだけを個別予算で再試行します。

- `camera_guard=warn`: カメラ型と説明の高確度な矛盾を警告して保持
- `camera_guard=strict`: 該当Sceneだけ再試行
- `vocal_guard=warn`: Timelineと矛盾し得る発声cueを警告して保持
- `vocal_guard=strict`: 該当Sceneだけ再試行
- `save_debug_output=True`: `ComfyUI/output/cl_mv_prompt_planner_debug/`へ要求、生応答、検証結果及び最終部分状態を保存

詳細は[Plannerコア仕様](../spec/cl_mv_prompt_planner_spec.md)、[ノード仕様](../spec/cl_mv_prompt_planner_comfyui_node_spec.md)、[Song Bible仕様](../spec/cl_mv_prompt_planner_song_bible_spec.md)及び[視覚拡張プロファイル仕様](../spec/cl_mv_prompt_visual_profiles_spec.md)を参照してください。
