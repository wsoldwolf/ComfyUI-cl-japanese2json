# CL MV Prompt Planner (GGUF) ComfyUIノード仕様

本書はv0.2.0の`clmv-line-v2`ノード契約を記述する。Song Bible、構造化LLM入力、直前Scene最終状態、完全重複検出、`camera_guard`及び`vocal_guard`の詳細は`docs/cl_mv_prompt_planner_song_bible_spec.md`を正本とする。

## 1. ノード契約

- 登録名: `CLMVPromptPlannerGGUF`
- 表示名: `CL MV Prompt Planner (GGUF)`
- カテゴリ: `MiniMax H3/Prompt Tools`
- 実装: `node_mv_prompt_planner/node.py`
- 関数: `plan_mv_prompt`

## 2. 入力

| 名前 | 型 | 既定値 | 用途 |
|---|---|---:|---|
| `prompt_segments` | STRING socket | - | `CL Vocal to Prompt Segments.prompt_text` |
| `planning_markdown` | STRING socket | - | Subject、Retention、Commonの既存文法サブセット。テキスト又はPrimitive Stringノードから接続する |
| `model_name` | COMBO | 発見先頭 | `ComfyUI/models/LLM/GGUF`以下のGGUF |
| `chat_format` | COMBO | `auto` | GGUFテンプレート自動判定、又は`qwen`/`gemma`明示 |
| `max_tokens` | INT | 4096 | 1回の最大生成token |
| `temperature` | FLOAT | 0.1 | 計画の多様性 |
| `top_p` | FLOAT | 0.9 | nucleus sampling |
| `repetition_penalty` | FLOAT | 1.05 | 反復抑制 |
| `gpu_layers` | INT | -1 | llama.cpp GPU layer |
| `n_batch` | INT | 256 | llama.cpp batch |
| `n_ctx` | INT | 0 | 0はモデル既定 |
| `flash_attn` | BOOLEAN | true | Flash Attention |
| `kv_cache_type` | COMBO | `q8_0` | KV cache型 |
| `op_offload` | BOOLEAN | true | operation offload |
| `keep_model_loaded` | BOOLEAN | false | 実行後モデル保持。H3実行前のVRAM解放を既定とする |
| `seed` | INT | 1 | 初回seed。再試行ごとに決定論的に増分 |
| `scenes_per_batch` | INT | 6 | Scene計画の1回当たり上限 |
| `retry_max` | INT | 10 | 不正Sceneごとの最大再試行回数。バッチ内の別Sceneとは共有しない |
| `save_debug_output` | BOOLEAN（optional） | false | plannerの全リクエスト、生応答、検証結果及び最終部分状態を保存 |
| `camera_guard` | COMBO（optional） | `warn` | 高確度のカメラ意味矛盾を警告又はScene再試行にする。候補は`warn`/`strict` |
| `vocal_guard` | COMBO（optional） | `warn` | Timelineと矛盾する発声cueを警告又はScene再試行にする。候補は`warn`/`strict` |

`chat_format=auto`では`llama-cpp-python`へchat formatを明示せずGGUF metadata/templateへ委ねる。Qwen系、Gemma系及び将来の互換モデルを同じモデル選択欄で切り替えられる。明示値はメタデータ不備のGGUF用である。

GGUF実行に共通する`model_name`、`max_tokens`、`temperature`、`top_p`、`repetition_penalty`、`gpu_layers`、`n_batch`、`n_ctx`、`flash_attn`、`kv_cache_type`、`op_offload`、`keep_model_loaded`、`seed`及び`retry_max`のUI既定値は`CL Japanese to JSON (GGUF)`と一致させる。Planner固有の`chat_format`、`scenes_per_batch`、`camera_guard`及び`vocal_guard`を追加する。`retry_max`は各Sceneへ個別に適用し、同じbatchの先行Sceneが後続Sceneの予算を消費してはならない。

`prompt_segments`と`planning_markdown`は最初から接続専用の`forceInput`ソケットとして定義する。接続後にmultiline widgetをソケットへ変換する構造は使用しない。ComfyUIでは変換済みwidgetが非表示になった際、旧`widgets_values`配列と新しいINPUT_TYPESの位置対応が崩れ、model、chat format及び数値設定が後続widgetへずれることがあるためである。

## 3. 出力

| 名前 | 型 | 内容 |
|---|---|---|
| `planned_markdown` | STRING | `CL Japanese to JSON (GGUF).plain_text`へ渡す日本語縮小Markdown |
| `planner_json` | STRING | song bible、Scene計画及び入力固定要素の検証用JSON |
| `status` | STRING | モデル、Scene数、batch数、再試行数の要約 |

## 4. モデル実行

共有`common/gguf/runtime.py`及び`common/gguf/discovery.py`を使用する。Qwen3では共有ランタイムのnon-thinking制御を使用する。Gemma系又はautoではGGUF chat templateを使用する。

Song bibleとScene計画は別リクエストにする。LLM応答はJSONではなく、タブ区切りの固定フィールドからなる行指向プロトコルとする。Scene計画は小バッチ化し、正常Sceneを保持して未解決Sceneだけを再送する。要求対象IDと件数を毎回明示し、再試行には前回の構文・検証エラーも含める。`retry_max=0`は再試行なし。各再試行のseedは`seed + attempt`を32-bit非ゼロ範囲へ正規化する。

Scene応答は`SCENE`、`SCENE_INTENT`、`SHOT`、`COMPOSITION`、1～8個の連番`ACTION`、`ENVIRONMENT`、`CAMERA`、`END_SHOT`及び`END_SCENE`を固定順で持つ。各Sceneレコードを独立して厳密検証し、壊れたレコードは推測修復しない。複数ACTIONの番号順は実行時系列であり、Pythonが同じ順序の別バレットへ変換して時間接続語を付与する。

ComfyUIのキャッシュ判定には、選択GGUFの実パス・サイズ・更新時刻と、2個のplanner system promptのサイズ・更新時刻・SHA-256を含める。モデル又はsystem promptを変更した場合は、入力値が同じでも再実行する。

`save_debug_output=True`では、実行ごとのディレクトリを`ComfyUI/output/cl_mv_prompt_planner_debug/`へ作る。入力`prompt_segments.md`と`planning_markdown.md`、設定manifest、各推論のsystem prompt、送信要求JSON、行指向の生LLM応答、Pythonが解析した検証済みデータ及び検証metadataを保存する。`final_state.json`には、最後に正常確定したSong BibleとScene、実行中batch/attempt、回収済みScene ID、未解決Scene ID及び最終エラーを保存する。成功時は`planned_markdown.md`と`planner.json`、失敗時は`error.txt`も保存する。入力や歌詞を含むため共有前に内容を確認する。

Plannerは会話履歴を累積しない。Song Bibleと各Scene batchはそれぞれsystem promptとuser JSONから成る独立リクエストである。Scene間の必要情報はSong Bible、Scene別`active_section_motifs`及び、直前に検証済みのSceneから作る構造化`previous_scene_tail`として明示的に渡す。同一応答内の後続Sceneには直前レコードを継続情報として扱わせる。従ってdebug bundleの各eventが、その推論時点の完全なチャット入力と出力である。

PythonからLLMへの入力だけを浅いJSONとし、`PlanningBrief`は`hard_requirements.subjects`、`retention`、`common`の配列へ分解する。歌詞はSong Bible用の`section_sources`とScene用の`lyric_groups`へ一意にまとめる。LLM出力はJSONではなく、Song Bible `clmv-song-bible-line-v2`及びScene `clmv-scene-line-v1`の行指向形式である。

完全一致するScene計画は、Unicode NFKC、空白単一化及び前後空白除去後の構図、全ACTION、環境、カメラを署名化して検出する。先のSceneを保持し、後のSceneだけを部分再試行する。重複検出後は未解決Sceneを一件ずつ時間順に再送し、直前の確定Sceneを`previous_scene_tail`へ反映する。既知の重複元と直前Sceneは、本文ではなくSHA-256、Shot数、先頭ACTION数及びcamera typeのcompact fingerprintとして`avoid_duplicate_plans`へ渡す。`duplicate_repair`でfingerprint群にないShot/ACTION件数を推奨し、低temperatureで同一応答が続く場合は再試行ごとに推奨形状を変更する。推奨件数と異なるだけでは拒否せず、完全署名が重複元と異なれば受理する。意味的に似ているだけのSceneや一部フィールドの一致は除外しない。

一つのbatchで正常Sceneが0件の場合は、次の試行から一件ずつの再送へ自動縮退する。各要求には対象Scene自身の診断だけを含める。`CAMERA`へリテラル`type`、`amplitude`又は`speed`を出した場合は、`camera_protocol_repair`で実在する有効値を具体的に指定する。これにより8Bモデルが抽象的な書式説明を値としてコピーしても、同じ6件を`retry_max`まで再送しない。

`camera_guard=warn`は計画本文を変更せず、疑わしいScene、Shot、camera type及び記述を黄色のWARNINGへ出し、`planner_json`とdebug metadataへ保存する。`strict`は該当Sceneだけを部分再試行する。一般ACTIONの「押す」「引く」からカメラ意図を推定せず、カメラ本文を自動修正しない。

`vocal_guard=warn`はTimelineと矛盾する発声cueを黄色のWARNINGへ出して計画を維持する。`strict`は該当Sceneだけを部分再試行する。`voiced` Sceneの`歌う`、`歌い`、`歌唱`及び`口パク`は既存Source Vocalへの視覚同期なのでguard対象外とする。`silent` Sceneの歌唱、又は`voiced` Sceneであっても追加の叫び、囁き、うめき若しくは語りを示すcueはguard対象とする。

全Sceneの検証とMarkdown/JSON生成が完了した場合、出力を返す直前にstatus要約をANSIシアン色の`[cl_mv_prompt_planner] success:`ログとして記録する。リクエスト中、再試行中又は一部Sceneだけ確定した状態を成功として記録してはならない。

## 5. ワークフロー接続

```text
CL Vocal to Prompt Segments.prompt_text
                 │
                 ▼
CL MV Prompt Planner (GGUF).prompt_segments
Planning Markdown ──────────────► planning_markdown
                 │
                 ▼
CL Japanese to JSON (GGUF).plain_text
                 │
                 ▼
MiniMax H3 Contex-Loop Plan.plan_json_input
```

Vocalノードの`segments_json`は診断用のまま維持し、プランナーの必須入力にはしない。必要な楽曲セクション、歌詞及び時間情報はコメントを含む`prompt_text`から決定論的に取得する。
