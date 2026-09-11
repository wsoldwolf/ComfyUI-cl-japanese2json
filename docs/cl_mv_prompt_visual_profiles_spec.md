# CL MV Prompt Planner 視覚拡張プロファイル仕様

## 1. 目的

`CL MV Prompt Planner (GGUF)`の視覚計画を、人物演技中心の保守的な計画から、歌詞由来の象徴物、空間的比喩、前景トランジション及び抽象カットまで段階的に拡張する。

プロファイルはLLMの自由度を単に上げる設定ではない。PythonがSceneごとの`AUX_VISUAL`数と許可種別を契約として固定し、選択したsystem prompt断片と同じ契約でLLM応答を検証する。Subject、保持分析、共通プロンプト、固定Timeline、発声及び参照制約は常にプロファイルより優先する。

## 2. UI契約

ノードの任意入力`visual_enrichment_profile`は、インストール済みプロファイルIDをCOMBOとして表示する。既定値は`performance_only`であり、既存の保守的な計画を維持する。

同梱プロファイルは次の3個である。

| ID | 対象 | Scene契約 | 用途 |
|---|---|---|---|
| `performance_only` | 全モデル | `AUX_VISUAL`を0個 | 人物演技、既存環境、照明、構図及びカメラだけで展開する |
| `lyric_visuals_light_8b` | 8B級 | Sceneごとに正確に1個 | Pythonが種類を循環指定し、小型モデルの選択負荷と出力分岐を抑える |
| `lyric_visuals_full` | 14B以上を推奨 | Sceneごとに1～3個 | 歌詞由来の象徴物、抽象的な道、空間変化、前景ワイプ及び抽象カットを広く許可する |

プロファイルはモデル名から自動推定しない。8Bでもfullを、27Bでもperformance_onlyを明示選択できる。これによりモデル交換だけで演出意図が黙って変わることを防ぐ。

## 3. 配置と発見

プロファイルは次の独立ディレクトリを1単位とする。

```text
node_mv_prompt_planner/prompts/profiles/<profile_id>/
  profile.json
  song_bible.txt
  scene_plan.txt
```

起動時の`INPUT_TYPES`生成では上記ディレクトリを走査し、`ui_order`、`profile_id`の順にCOMBO候補を並べる。新しいプロファイルの追加にコアPythonの条件分岐追加は不要である。

各プロファイルファイルのサイズ、更新時刻及びSHA-256はComfyUIのキャッシュfingerprintへ含める。プロファイル内容を変更した場合は、他の入力値が同じでもPlannerを再実行する。

## 4. マニフェスト

`profile.json`はUTF-8 JSON objectであり、次のフィールドを過不足なく持つ。

```json
{
  "schema_version": 1,
  "profile_id": "lyric_visuals_light_8b",
  "display_name": "Lyric Visuals Light (8B)",
  "description": "Sceneごとに一つの補助映像を決定論的に割り当てる。",
  "ui_order": 20,
  "minimum_aux_visuals_per_scene": 1,
  "maximum_aux_visuals_per_scene": 1,
  "assignment_mode": "cycle",
  "allowed_kinds": [
    "symbolic_object",
    "spatial_metaphor",
    "light_shadow"
  ]
}
```

制約は次のとおりである。

- `profile_id`は小文字英数字とunderscoreからなり、ディレクトリ名と一致する。
- 補助映像数は`0 <= minimum <= maximum <= 3`とする。
- `assignment_mode`は`none`、`cycle`又は`model`とする。
- 最大数0では`none`かつ空の`allowed_kinds`だけを許可する。
- 最大数1以上では、空でない`allowed_kinds`と`none`以外の割当方式を要求する。
- `cycle`は最小数・最大数とも1とし、Scene番号から必須種別をPythonが決定する。
- 未知フィールド、重複kind又は空のprompt断片は停止エラーにする。

`display_name`と`description`は人間向けmetadataである。保存済みワークフロー及び検証契約は安定した`profile_id`を使用する。

## 5. system prompt断片

`song_bible.txt`はSong Bible生成用、`scene_plan.txt`はScene計画用の追加命令である。共通system promptの末尾へ選択プロファイルIDとともに連結する。

断片は次を行ってはならない。

- Subject、保持分析、共通プロンプト又はTimelineの上書き。
- 人物、解剖、発声、字幕、可読文字又は参照の追加。
- `AUX_VISUAL`以外の行指向文法の変更。
- manifestの数、割当方式又は許可kindと異なる要求。

## 6. 行指向プロトコル

Song Bibleは`clmv-song-bible-line-v3`を使用し、次の必須行を追加する。

```text
VISUAL_ENRICHMENT_STRATEGY<TAB>選択プロファイルに従う全体方針
```

Scene計画は`clmv-scene-line-v2`を使用する。補助映像は対象Shotの最後の`ACTION`後、`ENVIRONMENT`前へ次の形式で置く。

```text
AUX_VISUAL<TAB>kind<TAB>画面内で起きる一つの具体的な日本語アニメーション記述
```

`AUX_VISUAL`は人物動作、カメラ命令、感情ラベル、音響、字幕又は環境本文の代替ではない。PythonはScene全Shotを合算して個数、許可kind及び必須kindを検証する。

`cycle`では次の式でSceneごとの必須kindを選ぶ。

```text
allowed_kinds[(scene_id - 1) % len(allowed_kinds)]
```

この契約は`auxiliary_visual_contract`としてScene要求JSONへ明示する。LLMに循環規則を推測させない。

## 7. レンダリングと検証

検証済み補助映像は、固定Timelineを変えず、対象Shot内で人物ACTIONの後、環境記述の前へ次のバレットとして出力する。

```markdown
* 付加映像として、黒い破片が奥行き方向へ連なり、崩れた道の輪郭へ変わる。
```

完全重複Sceneの署名にはkindとdescriptionを含める。`camera_guard`及び`vocal_guard`も補助映像本文を検査するため、`AUX_VISUAL`へカメラ運動又は発声cueを紛れ込ませても既存guardを迂回できない。

プロファイル違反は該当Sceneだけを未解決として再試行する。Pythonが不足した補助映像を創作補完したり、未知kindを既知kindへ黙って置換したりはしない。

## 8. 拡張手順

将来プロファイルを追加する場合は次だけを行う。

1. 新しい安定した`profile_id`のディレクトリを作る。
2. schema version 1の`profile.json`を作る。
3. manifest契約と一致する2個のprompt断片を作る。
4. profile発見、Song Bible、Scene解析、個数・kind検証及びレンダリングのテストを追加する。
5. 実8B及び対象大型モデルで、同一Sceneの反復、参照破損、人物追加及び過剰な可読文字がないことを確認する。

既存`profile_id`の意味を互換性なく変更しない。大きく異なる自由度や演出方針は新しいIDとして追加する。
