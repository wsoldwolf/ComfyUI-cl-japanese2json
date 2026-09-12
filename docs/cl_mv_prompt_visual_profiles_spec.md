# CL MV Prompt Planner 視覚拡張プロファイル仕様

## 1. 目的

`CL MV Prompt Planner (GGUF)`の視覚計画を、人物演技中心の保守的な計画から、歌詞由来の象徴物、空間的比喩、前景トランジション及び抽象カットまで段階的に拡張する。

プロファイルはLLMの自由度を単に上げる設定ではない。PythonがSceneごとの`AUX_VISUAL`数と許可種別を契約として固定し、選択したプロファイル固有の創作ポリシーと同じ契約でLLM応答を検証する。Subject、保持分析、共通プロンプト、固定Timeline、発声及び参照制約は常にプロファイルより優先する。

## 2. UI契約

ノードの任意入力`visual_enrichment_profile`は、インストール済みプロファイルIDをCOMBOとして表示する。既定値は`performance_only`であり、既存の保守的な計画を維持する。

接続専用の任意`STRING`入力`visual_enrichment_profile_override`へ空でないプロファイルIDが渡された場合、その値をCOMBOより優先する。未接続又は空文字列の場合はCOMBOを使用する。overrideにも本書の発見済みプロファイルID検証を適用し、未知値を暗黙の既定値へ置換しない。

同梱プロファイルは次の3個である。

| ID | 対象 | Scene契約 | 用途 |
|---|---|---|---|
| `performance_only` | 全モデル | `AUX_VISUAL`を0個。長尺歌詞Sceneは1～6 Shot | 人物演技、既存環境、照明、構図及びカメラだけで展開する |
| `lyric_visuals_light_8b` | 8B級 | Sceneごとに正確に1個。長尺歌詞Sceneは正確に2 Shot。歌詞動作preplanを使用 | Pythonが種類を循環指定し、歌詞述語を小さい独立推論で固定してからSceneを組み立てる |
| `lyric_visuals_full` | 14B以上を推奨 | Sceneごとに1～3個。長尺歌詞Sceneは2～3 Shot | 歌詞由来の象徴物、空間軌道、状態変化、前景遷移及び抽象カットを媒体非依存に許可する |

プロファイルはモデル名から自動推定しない。8Bでもfullを、27Bでもperformance_onlyを明示選択できる。これによりモデル交換だけで演出意図が黙って変わることを防ぐ。ただしモデル名に独立した`8B`トークンを検出し、同時に`lyric_visuals_full`を選択した場合は、再試行枯渇・失敗リスクを示す3行の重大WARNINGを記録する。8B向け`lyric_visuals_light_8b`及び`performance_only`では警告しない。これは注意喚起だけであり、選択済みプロファイルを暗黙に変更しない。

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
  "schema_version": 4,
  "profile_id": "lyric_visuals_light_8b",
  "display_name": "Lyric Visuals Light (8B)",
  "description": "Sceneごとに一つの補助映像を決定論的に割り当てる。",
  "ui_order": 20,
  "minimum_aux_visuals_per_scene": 1,
  "maximum_aux_visuals_per_scene": 1,
  "long_lyric_scene_minimum_shots": 2,
  "long_lyric_scene_maximum_shots": 2,
  "assignment_mode": "cycle",
  "lyric_action_preplan": true,
  "lyric_action_scenes_per_request": 1,
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
- 10秒以上かつ歌詞を持つSceneのShot数は`1 <= minimum <= maximum <= 6`とする。範囲はsystem promptだけでなくPython検証にも使用する。
- `assignment_mode`は`none`、`cycle`又は`model`とする。
- `lyric_action_preplan`はbooleanとする。`true`では歌詞Sceneごとに後述の小型意味分解段階を実行し、検証済み動作をScene計画へ固定する。
- `lyric_action_scenes_per_request`は0から16のintegerとする。`lyric_action_preplan=true`では1以上、`false`では0とする。8B用は1に固定し、異なるSceneの歌詞と動作が一回の応答内で混線することを防ぐ。
- 最大数0では`none`かつ空の`allowed_kinds`だけを許可する。
- 最大数1以上では、空でない`allowed_kinds`と`none`以外の割当方式を要求する。
- `cycle`は最小数・最大数とも1とし、Scene番号から必須種別をPythonが決定する。
- 未知フィールド、重複kind又は空のプロファイル固有ポリシーは停止エラーにする。

`display_name`と`description`は人間向けmetadataである。保存済みワークフロー及び検証契約は安定した`profile_id`を使用する。

## 5. 共通コアとプロファイル固有ポリシー

`node_mv_prompt_planner/prompts/core/`の4ファイルは、行指向プロトコル、安全境界、固定Timeline、保持分析、参照プレースホルダ、H3フィールド分離、カメラ型、発声ロック及び再試行契約を定義する。`lyric_action_system_prompt.txt`は小型モデル用の媒体非依存な歌詞述語分解、`auxiliary_visual_repair_system_prompt.txt`は構造的に有効なSceneを保持して重複した付加映像一行だけを修復する限定プロトコルである。個別の歌詞、物体、画材又は背景例を共通コアへ記載してはならない。

各プロファイルの`song_bible.txt`はSong Bible生成用、`scene_plan.txt`はScene計画用の独立した創作ポリシーである。ローダーは共通コアの末尾へ、選択プロファイルIDと該当段階のポリシー一つだけを連結する。別プロファイルのポリシーは連結しない。

プロファイル固有ポリシーは次を行ってはならない。

- Subject、保持分析、共通プロンプト又はTimelineの上書き。
- 人物、解剖、発声、字幕、可読文字又は参照の追加。
- 共通コアが定義する行指向文法の変更。
- manifestの数、割当方式又は許可kindと異なる要求。

責務分離は次のとおりとする。

- `performance_only`: 既存人物、既存物体、既存環境、照明及びカメラによる演技だけを扱い、`AUX_VISUAL`と歌詞由来の新規物体を禁止する。
- `lyric_visuals_light_8b`: 8B向けの短い優先順位、意味役割固定、正確に2 Shot及び正確に1個の`AUX_VISUAL`を定義する。`ROLE LOCK`等の小型モデル向け強制規則はこのポリシーだけに置く。
- `lyric_visuals_full`: 14B以上向けに歌詞述語、対象、微細動作、1～3個の`AUX_VISUAL`及び2～3 Shotを柔軟に計画する。8B専用の段階名や固定手順を含めない。

10秒以上の歌詞Sceneでは、JSON入力の`line_protocol_shape_contract`に`minimum_shot_blocks=2`、`maximum_shot_blocks=2`及び`exact_shot_blocks=2`を数値で渡す。8B用Sceneポリシーは、第一Shotの`END_SHOT`後に第二Shotを置き、その後だけ`END_SCENE`を許可する物理レコード順を明示する。短尺又は無歌詞Sceneは正確に一つのShotとする。両者とも`required_camera_sequence`を順番どおり使用し、camera type、amplitude及びspeedをそのまま複写する。系列は6パターンを循環し、広い部分周回を定期的に要求する。部分周回は同じ人物の前方斜め、側面及び後方斜め等から最低二領域を連続して見せ、変身、再描画又は複製を起こさない。

正確に一個の補助映像は`auxiliary_visual_lines_per_shot=[0, 1]`として第2 Shotだけへ置き、各Shotへ一個ずつ複製させない。モデルがそれでも両Shotへ必須kindを出した場合は、Pythonが最後の一個だけを保持する。`allowed_lyric_response_mode_tokens`はASCIIの固定enumとして渡し、翻訳を禁止する。必須AUX kindをmode欄へコピーした場合は、ACTIONのSubject結合から直接人物動作又は直接物体動作へ復元する。いずれも創作文を追加せず、WARNINGを残す。再試行では直近エラーだけでなく、これらの必須レコード形状と`LYRIC_RESPONSE`列構造を毎回累積して通知する。これにより、引用符又はenumエラーを修正した次の試行でShot数制約を忘れる往復を防ぐ。

共通コア又は一つのプロファイルを変更しても、他プロファイルの創作ポリシー本文は変化しない。テストは3つの合成system promptを個別検査し、`ROLE LOCK`、`high-capacity creative policy`及び`character-performance creative policy`が意図しないプロファイルへ混入しないことを確認する。

## 6. 行指向プロトコル

Song Bibleは`clmv-song-bible-line-v4`を使用し、次の必須行を追加する。

```text
VISUAL_ENRICHMENT_STRATEGY<TAB>選択プロファイルに従う全体方針
```

Scene計画は`clmv-scene-line-v4`を使用する。補助映像は対象Shotの最後の`ACTION`後、`ENVIRONMENT`前へ次の形式で置く。

```text
AUX_VISUAL<TAB>kind<TAB>画面内で起きる一つの具体的な日本語アニメーション記述
```

`AUX_VISUAL`は人物動作、カメラ命令、感情ラベル、音響、字幕又は環境本文の代替ではない。特に、`LYRIC_RESPONSE`が要求する実行可能な人物直接動作、歌詞中の認識可能な具体的対象及び動作後の可視結果を、媒体の変化や抽象効果だけへ置き換えてはならない。人物の身体、解剖、衣装、被覆又は保持対象は付加映像の変形元・変形先にせず、除去、露出、開放、剥離、亀裂、破断、溶解、侵食又は再形成を指示しない。変形は許可された外部物体又は非人物レイヤーへ適用する。筆記、命名又は彫刻を映像化する場合も、結果は対象面に付着した不規則で孤立した非言語的な短い傷又は溝だけとし、文字列又は疑似タイポグラフィとして整列させない。PythonはScene全Shotを合算して個数、許可kind及び必須kindを検証する。記述は初期状態、可視運動及び結果を一つの簡潔な文へ含める。

`lyric_visuals_light_8b`のSceneポリシーは、小型モデルが後続の短い反復句へアンカーを移すことを抑えるため、Shot計画より先に最も早い完全な述語を選ぶ。主要ACTIONは選択述語の中心動詞そのものを明示対象へ実行し、同じ対象に対する別種の物理操作で代用しない。対象は接触前の`COMPOSITION`へ置き、接触動作は`PREPARE`、`CONTACT POINT`、`PATH OR REPETITION`、`RESULT with RELEASE`に相当する二～四個の連続ACTIONへ簡潔に分解する。意図的な痕跡を作る動作では胴体と上腕を安定させ、手、手首又は指で不均一な複数軌道を描く。接触点、軌道、反復、力加減及び結果を歌詞から先に決め、楽曲の強度は速度又は尺度だけへ適用する。10秒以上の歌詞Sceneは正確に2 Shotとし、全体構図から別種の移動カメラによる斜め又は奥行き方向の接触提示へ移る。8B専用の歌詞、物体、画材又は背景例を追加しない。

同プロファイルでは`lyric_action_preplan=true`及び`lyric_action_scenes_per_request=1`とし、Scene本体より先に`clmv-lyric-action-line-v1`を歌詞Scene一件ずつ実行する。この入力にはPlanningBriefのSubject定義と保持分析、及び対象Sceneの`lyric_lines`だけを渡す。共通プロンプトの画材・背景、Song Bible、section motif、直前Scene本文、カメラ及びShot構造は渡さない。まず歌詞の主体、中心動詞、具体的対象、接触経路及び完了結果を固定し、画風と背景は後段で適用する。応答は歌詞アンカー、方式、接触前の構図要件、二～四個の時系列ACTION及び可視結果だけを持つ。Pythonは各レコードを独立検証し、不正又は欠落Sceneだけを再試行する。8Bが内部Subjectトークンを既知の`<Subject N>`へ正確に戻した場合に限り、参照凡例を使って再保護し、未知Subject又は他種参照は拒否する。

検証済みpreplanは`locked_lyric_action_blueprint`としてScene要求の末尾へ渡す。Scene LLMは構図、環境、カメラ及び付加映像をこの対象・接触・結果へ適合させる。Pythonは解析後、Scene LLMが返したACTIONをblueprintの時系列ACTIONと可視結果で置換し、複数Shotへ順序を保って分配する。また対象が接触前から認識できるよう各ShotのCOMPOSITIONへ構図要件を保持する。これにより、共通画材、section motif又は`previous_scene_tail`の複写が歌詞の中心動詞を消すことを防ぐ。27B向け`lyric_visuals_full`及び`performance_only`は`lyric_action_preplan=false`で従来の単一Scene計画を維持する。

軽量プロファイルでは、後段の短い優先順位を`歌詞述語`、`観察可能な動作機構`、`移動カメラ`、`付加映像`の順に固定する。書字、彫刻、描画等の痕跡生成述語は特定の材質や歌詞を例示せず、認識可能な別個の対象面、許可された指先又は道具、接触点、複数の不均一な局所軌道、蓄積する痕跡及び離脱後の結果として一般化する。生成内容又は痕跡と、それを受ける既存の対象物・対象面は別々の意味役割として維持する。対象名を「対象のような形」という形容や比喩へ変換せず、対象そのものの輪郭を空間又は反復媒体へ描く動作へ置換しない。対象は接触前から`COMPOSITION`に存在し、操作に適した面、厚み、縁、向き及び奥行きを持つ立体物又は対象面として記述する。対象物にも`global_visual_direction`の非写実的な画材、線質及び表面処理を直接適用し、現実の物体名から写実、実写又は滑らかな3D表現へ回帰させない。`ACTION`は指先又は道具がその表面へ接触して結果を表面上又は内部へ蓄積する過程を示す。痕跡は対象面の範囲と遠近に従う、長さ、向き、曲率及び間隔が不規則な孤立した短い傷又は溝とし、対象全体より従属的に表示する。基準線、横一列又は縦一列の配列、反復字形、字間、単語間隔、鏡文字、反射文字、可読文字、浮遊文字又は独立した字形物体にしない。打撃、踏み付け、圧壊又は大振りな一撃へ置換しない。長尺歌詞Sceneの第1 Shotは人物、対象及び前景・背景の奥行きを移動カメラで確立し、第2 Shotは異なるカメラ種別で接触を斜め又は奥行き方向から横断して、接触点、蓄積結果及び不変の人物を同時に示す。単に接近、後退又は追従するとだけ記述したCAMERAは不十分とする。

同プロファイルのSong Bibleは人物振付から分離する。`SECTION_MOTIF`はSubjectトークン、人物動作、姿勢、身体状態、消失、溶解、変形又は接触操作を含めず、外部の非人物レイヤーにおける一つの状態遷移だけを定義する。これによりScene固有の歌詞述語がモチーフへ置換されることを抑える。

軽量プロファイルの`AUX_VISUAL`はScene全体で正確に1行とし、いずれか一つのShot内で最後の`ACTION`直後かつ`ENVIRONMENT`前に置く。8Bがこの完全な行だけを`END_SHOT`直後へ移した場合は、Pythonが直前Shotへ戻して受理する。同一kind・同一本文を複数Shotへ反復した場合は最初の1件へ畳む。この正規化は位置又は完全重複だけを扱い、内容やkindを推測生成しない。

`lyric_visuals_full`では、直接的な接触述語を、身体と把持の準備、正確な接触点、動詞固有の軌道又は反復、対象の抵抗と蓄積変化、離脱又は復帰へ展開する。意図的な痕跡を作る場合は、胴体と上腕を安定させ、尺度に合った手、手首又は指の局所運動と軌道間の位置変更を明示する。細かな制御を含む動詞は、楽曲が激しくても大振りな衝撃動作へ置換しない。10秒以上の歌詞Sceneはプロファイル契約により2～3 Shotを必須とし、実Shot数と同じ数の`required_camera_sequence`先頭要素を使用する。Shot境界は予備動作、接触及び結果の意味的遷移へ置き、カメラ種別、角度、距離又は奥行き関係を変える。カメラは前景と背景の相対運動を伴い、重要な接触と変化を隠さず、付加映像又は手元へ序盤で寄った後に同じ構図を保持しない。

同梱プロファイルポリシーはインク、道路、雪、炎等の特定媒体又は特定歌詞に依存してはならない。`planning_brief.global_visual_direction`を「出来事がどう見えるか」、現在Sceneの歌詞関係を「何がどう変化するか」として分離する。方向と距離、収束と分岐、蓄積と侵食、圧力と解放、分解と再構成、隠蔽と露出、規模と奥行き等を汎用的な変化関係として扱い、歌詞に適合する場合だけ使用する。

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

さらにPythonはkindに関係なく、正規化後の`AUX_VISUAL.description`完全一致を全Scene間及び同一Scene内で検出する。正確に一個を循環割当するプロファイルでは、構図、ACTION、環境、カメラ及びShot数を保持し、重複文と一般化された変化操作を専用一行プロトコルへ渡して最大2回だけ修復する。成功時は当該一行だけを置換する。失敗時は全Scene再試行ループへ移らず、WARNINGを記録して元の構造的に有効なSceneを採用する。その他のプロファイルでは該当Sceneだけを従来どおり再試行する。意味類似の推測又は媒体固有語による辞書判定は行わない。

プロファイル違反は該当Sceneだけを未解決として再試行する。Pythonが不足した補助映像を創作補完したり、未知kindを既知kindへ黙って置換したりはしない。

## 8. 拡張手順

将来プロファイルを追加する場合は次だけを行う。

1. 新しい安定した`profile_id`のディレクトリを作る。
2. schema version 4の`profile.json`を作り、補助映像数、長尺歌詞SceneのShot数範囲、`lyric_action_preplan`及び`lyric_action_scenes_per_request`を指定する。
3. manifest契約と一致する2個のプロファイル固有ポリシーを作る。
4. profile発見、Song Bible、Scene解析、個数・kind検証及びレンダリングのテストを追加する。
5. 実8B及び対象大型モデルで、同一Sceneの反復、参照破損、人物追加及び過剰な可読文字がないことを確認する。

既存`profile_id`の密度と対象モデルは維持する。プロファイル固有ポリシーから媒体固有の例を除く改善、又は許可kindを同じ密度の媒体非依存な名称へ置換する改善は、汎用性を高める互換的な修正として許可する。大きく異なる自由度や演出方針は新しいIDとして追加する。
