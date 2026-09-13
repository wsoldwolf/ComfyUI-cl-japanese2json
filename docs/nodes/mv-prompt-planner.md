# CL MV Prompt Planner (GGUF)

Vocalノードの固定タイムラインと歌詞コメントを保ったまま、ショット構図、人物動作、環境及びH3カメラ運動を計画します。出力は既存コンパイラで再検証できる日本語縮小Markdownです。

```text
Vocal.prompt_text ───────────────> prompt_segments
Planning Markdown ──────────────> planning_markdown
planned_markdown ───────────────> Japanese to JSON.plain_text
```

Planning Markdownには`# サブジェクト`、任意の`# 保持分析`及び任意の`# 共通プロンプト`だけを書きます。人物、変えてはいけない特徴、全編の画風・世界・禁止事項を簡潔に指定し、Scene、Shot及び音響はVocal出力へ任せます。

Enhancerが出力した共通バレット`情景の固定要素: ...`は全プロファイルで引き継ぎます。最初の要求シーンの最初のショットにも原文を提示するため、Scene Limiterで途中から開始する場合にも情景を指定できます。寄りのショットで一時的に画角外になることは許容し、全ショットを同じ構図には固定しません。

## 視覚拡張プロファイル

- `performance_only`: 人物演技、既存環境、照明、カメラを中心に計画
- `lyric_visuals_light_8b`: 8B向け。Sceneごとに限定した付加映像を追加
- `lyric_visuals_full`: 14B以上推奨。より豊富な象徴物、軌道、状態変化、前景遷移等を許可

8Bモデルで`lyric_visuals_full`を選ぶと重大WARNINGを表示しますが、自動変更はしません。モデルとプロファイルは外部STRING overrideからも指定できます。

## 再試行とguard

Plannerは最初にSong Bibleを作り、その後Sceneをバッチ計画します。有効Sceneを保持し、欠落又は不正なSceneだけを個別予算で再試行します。

歌詞アクション事前計画は`# 共通プロンプト`と情景固定要素を世界制約として参照します。歌詞の中心動作を固定しつつ、本計画が生成した予備動作、追従、反動及び立て直しを統合します。引用された歌詞、発話、口の動き又は画面内文字としてしか解釈できなかった歌詞行は、代替行がある場合に次の試行候補から除外されます。句読点だけを削って同じ解釈を繰り返さず、次の非発話動作へ進みます。Subjectを含むSceneの身体動作下限は長さに応じて1～4段階となり、8秒以上の複数Shotでは切替時刻が一方へ極端に偏らないよう広い許容窓内へ補正されます。

- `camera_guard=warn`: カメラ型と説明の高確度な矛盾を警告して保持
- `camera_guard=strict`: 該当Sceneだけ再試行
- `vocal_guard=warn`: Timelineと矛盾し得る発声cueを警告して保持
- `vocal_guard=strict`: 該当Sceneだけ再試行
- `save_debug_output=True`: `ComfyUI/output/cl_mv_prompt_planner_debug/`へ要求、生応答、検証結果及び最終部分状態を保存

動作不足の定型補完は本計画との最終統合後に一度だけ行います。各Shot内の句点・空白差と同一文の重複を除いてから不足を判定し、左右、否定、対象の違い及び明示的な反復は維持します。補助動作は原則として中心動作と合わせて4行までとし、最後の可視結果を別枠で保持します。完全な意味の重複や動作の自然さは自動判定できないため、最終動画の確認は必要です。

カメラ系列が指定されるプロファイルでは、前進・後退・横移動・上下移動の説明から人物動作のコピーを取り除きます。有効な経路と奥行きの説明が残らない場合は、指定済みの型・移動量・速度を維持した経路だけを補います。人物動作の不足をカメラ運動で補う処理ではありません。

詳細は[Plannerコア仕様](../spec/cl_mv_prompt_planner_spec.md)、[ノード仕様](../spec/cl_mv_prompt_planner_comfyui_node_spec.md)、[Song Bible仕様](../spec/cl_mv_prompt_planner_song_bible_spec.md)及び[視覚拡張プロファイル仕様](../spec/cl_mv_prompt_visual_profiles_spec.md)を参照してください。
