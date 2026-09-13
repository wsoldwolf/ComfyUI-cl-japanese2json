# CL Image Analyzer (Vision GGUF)

参照画像をローカルVision GGUFで観測し、検証済み観測から日本語概要、Subject、情景、PlannerBrief又は構造化JSONをPythonで生成します。解析に使った元画像を通常のComfyUI `IMAGE`として返します。

## 基本操作

1. 画像をノードへD&Dするか`image`で選択します。
2. 本体GGUFを選択します。対応する`mmproj`は同じディレクトリから自動選択されます。
3. `analysis_profile`を選択します。
4. Plannerへ渡す場合は通常`planner_brief`を使用します。

`general`、`subject_only`、`scene_only`、`planner_brief`、`structured_json`を選択できます。外部の画像ローダー又は生成ノードのテンソルを使う場合は`IMAGE`を`image_override`へ接続します。接続中は内部画像より外部テンソルが優先され、バッチはそのまま`image`へ返し、Vision解析には先頭画像を使います。

## Subjectヒント

画像だけで種族や設定を判別しにくい場合は、`subject_hint`へ短い自然言語を入力します。

- `hint_mode=assist`: 観測時の意味分類を補助
- `hint_mode=lock_identity`: ヒントを確定設定として最終出力へ保持
- `hint_conflict=warn`: 明瞭な画像証拠との矛盾を警告
- `hint_conflict=strict`: 矛盾時に停止

`additional_instruction`は眉、瞳、衣装等、重点的に観測する箇所を指定します。

## Picture参照とキャッシュ

`picture_reference_mode=auto_h3`では、H3の`ref_image_0`～`ref_image_8`への接続から`<Picture 1>`～`<Picture 9>`を解決し、ノード下部へ編集不能ラベルを表示します。未接続時や手動指定が必要な場合はモードと`picture_index`を切り替えます。

- `reuse`: 同じ画像、モデル、指示及び推論設定の検証済み観測を再利用
- `refresh`: 強制再解析してcacheを更新
- `disabled`: cacheを使用しない

詳細は[Visionノード仕様](../spec/cl_image_analyzer_vision_gguf_spec.md)を参照してください。
