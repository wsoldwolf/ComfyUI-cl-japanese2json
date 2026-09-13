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

動作不足は本計画との最終統合後に判定します。各Shot内の同一文、Subjectに続く「の／は」の差及び「ゆっくり」だけが異なる重複を整理します。左右、否定、対象の違い及び明示的な反復は保持します。Scene全体だけでなく各Shotの長さに応じた身体動作下限を検査し、不足時は同じLLMでACTIONだけを再生成します。歌詞由来の固定動作・順序・最終結果と、構図・環境・カメラ・Shot時刻は保持します。ACTION修復はSceneごとに最大2回で、検証に失敗した応答は採用せず、既存のScene再試行へ戻します。無関係な屈伸や腕振りをPythonの定型文で追加する処理は本経路で使用しません。

唇・舌の独立した振付、口内の発光及び眉の上下・振動は歌詞事前計画と最終Sceneの両方で検査します。顔だけの動作を身体演技として固定せず、感情を手・肩・胴体・支持脚へ具体化します。直前SceneからはSubject参照とカメラの種類・振幅・速度だけを渡し、構図・動作・環境・カメラ説明文の丸写しによる演技の連鎖を抑えます。情景の継続は共通制約とScene anchorsを使用しますが、前Scene終端の正確な姿勢は自動継承しません。動作の自然さや最終動画での表現はレンダリングで確認してください。

カメラ系列が指定されるプロファイルでは、前進・後退・横移動・上下移動の説明から人物動作のコピーを取り除きます。有効な経路と奥行きの説明が残らない場合は、指定済みの型・移動量・速度を維持した経路だけを補います。人物動作の不足をカメラ運動で補う処理ではありません。

詳細は[Plannerコア仕様](../spec/cl_mv_prompt_planner_spec.md)、[ノード仕様](../spec/cl_mv_prompt_planner_comfyui_node_spec.md)、[Song Bible仕様](../spec/cl_mv_prompt_planner_song_bible_spec.md)及び[視覚拡張プロファイル仕様](../spec/cl_mv_prompt_visual_profiles_spec.md)を参照してください。
