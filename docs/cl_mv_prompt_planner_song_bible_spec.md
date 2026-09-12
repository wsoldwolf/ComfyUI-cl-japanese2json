# CL MV Prompt Planner Song Bible仕様

## 1. 状態と目的

本書は`CL MV Prompt Planner (GGUF)`が内部で使用するSong Bible v4の正本仕様である。v4は固定条件を役割別PlanningBriefへ分離し、媒体非依存の歌詞解釈、カメラと主要イベントの結合及び付加映像の完全反復防止を加える。

Song BibleはMiniMax H3公式Full-Reference出力の一部ではない。本プロジェクト独自の全曲共通クリエイティブ計画であり、独立したSceneバッチへ次を一貫して渡すために使用する。

- 曲全体の視覚的な展開。
- 楽曲セクションごとの再利用可能な視覚モチーフ。
- 曲全体で変化させるカメラ展開方針。

Song Bibleを最終縮小Markdown又はH3 Full-Reference 6セクションへ直接出力してはならない。最終出力へ現れるのは、Song Bibleを参照して生成し、Python検証を通過したScene別Shotだけである。

## 2. 権限分離

プランナーの情報は次の優先順位で扱う。

1. Pythonが保持する`PlanningBrief`のSubject、Retention及びCommon。
2. Pythonが保持するTimelineのScene番号、秒数、ソース範囲、voiced/silent、歌詞、リップシンク及び音響。
3. Pythonが検証した視覚拡張プロファイル及びScene別補助映像契約。
4. Song Bibleのクリエイティブ計画。
5. 現在Sceneの歌詞から得る映像的着想。
6. 直前Sceneの最終状態。

上位の情報を下位の情報が変更、緩和、再解釈又は上書きしてはならない。

`PlanningBrief`は確定条件であり、Song Bibleの生成対象ではない。特にSubject数、外観、位置、移動可否、環境、小道具、時間帯、音響及び禁止事項をLLMに`CONTINUITY_RULE`として言い換えさせてはならない。確定条件を再生成すると、元の指示にない人物、動作、小道具又は音声が追加されるためである。

## 3. データモデル

`SongBible`は次のフィールドだけを持つ。

```python
@dataclass(frozen=True)
class SongBible:
    visual_arc: str
    camera_strategy: tuple[str, ...]
    visual_enrichment_strategy: str = ""
    section_motifs: tuple[SectionMotif, ...] = ()
```

LLM生成の`continuity_rules`は廃止する。確定した継続条件は既存`PlanningBrief`を正本として参照する。

`SectionMotif`は次を維持する。

```python
@dataclass(frozen=True)
class SectionMotif:
    section: str
    motif: str
```

`visual_arc`、`camera_strategy`、`visual_enrichment_strategy`及び`section_motifs`は全てソフトなクリエイティブガイドであり、PlanningBrief、Timeline又は選択プロファイルと矛盾する場合は無効である。

## 4. LLM入力

### 4.1 JSONを使用する境界

PythonからLLMへ渡す入力はUTF-8 JSONとする。JSONはPythonが生成するため構文失敗がなく、LLMにJSONを出力させる場合とは負荷と失敗特性が異なる。Qwen 8Bへ要求する出力は引き続きタブ区切りの行指向プロトコルとし、JSONの引用符、エスケープ、配列及び波括弧を生成させない。

入力JSONは浅く型を明確にし、同じ情報をMarkdown文字列とJSONの両方で重複送信してはならない。

### 4.2 PlanningBrief

LLMへはMarkdown文字列ではなく、Pythonで既に解析済みの配列として渡す。

```json
{
  "planning_brief": {
    "subject_identity": ["..."],
    "retention_constraints": ["..."],
    "global_visual_direction": ["..."]
  }
}
```

各文字列内の参照タグは既存`ReferenceProtector`で保護する。配列順は入力Markdownのバレット順を維持する。LLMはこれらの値を出力し直さない。

`subject_identity`は許可された人物、`retention_constraints`は不変の外観・解剖・衣装・参照、`global_visual_direction`は画材、画風、世界、照明、連続性及び禁止事項を表す。共通プロンプトに含まれる媒体や背景運動をSceneイベント候補として機械的にコピーしてはならない。媒体は歌詞由来イベントの描画方法であり、イベント自体は現在Sceneの歌詞関係から計画する。

### 4.3 楽曲セクションと歌詞

Song Bible入力では、歌詞をセクション単位へまとめる。同じセクションラベルが連続する歌詞行ごとに重複した`sections`要素を作ってはならない。

```json
{
  "section_sources": [
    {
      "label": "[Verse 1]",
      "kind": "verse",
      "lyrics": ["line 1", "line 2"]
    },
    {
      "label": "[Chorus]",
      "kind": "chorus",
      "lyrics": ["line 3", "line 4"]
    }
  ]
}
```

全曲中で同じラベルが再登場した場合、Song Bible用の`section_sources`はラベルごとに一つへ統合し、歌詞は出現順を維持して格納する。完全一致する反復歌詞はSong Bible入力に限り一つへ省略してよい。Timeline及び最終コメントの歌詞は省略又は変更してはならない。

Scene計画入力では、各歌詞行を出現順の1始まり`index`付き`lyric_lines`として一意に渡す。セクション境界は各行の`section`と`kind`へ保持し、同一文の反復も削除しない。

```json
{
  "lyric_lines": [
    {
      "index": 1,
      "section": "[Verse 1]",
      "kind": "verse",
      "text": "line 1"
    },
    {
      "index": 2,
      "section": "[Verse 1]",
      "kind": "verse",
      "text": "line 2"
    },
    {
      "index": 3,
      "section": "[Pre-Chorus]",
      "kind": "pre_chorus",
      "text": "line 3"
    }
  ]
}
```

同じScene入力には`lyric_response_contract`を付ける。歌詞がある場合、LLMは主体、物理的な中心動詞及び制約と両立する対象を持つ最も早い完全な述語、該当しなければindex 1を選び、`direct_subject_action`、`direct_object_action`又は`spatial_metaphor`を宣言する。独立した可視動詞を持たない後続の反復句、強調句、大文字句又は短い断片は先行する完全な述語より優先しない。制約に反しない具体的な身体動詞は`direct_subject_action`を第一候補とし、共通画材、背景運動、照明、表情又はカメラだけへ置き換えない。さらに`semantic_fidelity`で、選択歌詞の主体、中心動詞が表す物理操作、認識可能な対象物及び動作後の可視結果を一組として維持する。同じ対象へ別種の操作を行うことを、中心動詞の代替として認めない。`interaction_choreography`は、選択歌詞から必要な身体部位又は道具、接触点、方向と軌道、反復又は拍、力加減、抵抗、蓄積する対象変化及び離脱を導出し、楽曲セクションの強度又はモチーフより先に物理動作を確定する契約である。共通画材はそれらを様式化できるが代替できない。`inscription_fallback`は、筆記、命名又は彫刻を扱う場合も物理動作を維持し、結果を長さ、向き、曲率及び間隔が不規則な孤立した非言語的な短い傷又は溝へ限定する。基準線、横一列又は縦一列の配列、反復字形、字間、単語間隔、鏡文字及び反射文字は作らない。可読文字禁止だけを理由に、このfallbackで表現できる先行述語を不可能と判定しない。選ばれなかった行は補助的詳細に利用してよいが、アンカーを押しのけない。歌詞がない場合はindex 0と`instrumental_continuity`を使用する。これにより8Bモデルが歌詞配列との対応を曖昧にせず、Pythonが選択の有無と方式を検証できる。

同じScene入力の`retention_execution_contract`は、保持分析に列挙された人物の身体、解剖、衣装、被覆及び外観を全フィールドで不変とする。これらを歌詞由来又は付加映像由来の除去、露出、剥離、破壊、溶解、侵食若しくは再形成の対象にせず、衝突する変化は許可された外部物体又は非人物レイヤーへ移す。

同じScene入力の`camera_choreography_contract`は、プロファイル別Shot数、開始視点、通過軌道、終了視点、前景・背景視差、追従対象及び静止・シェイク制限を構造化して渡す。`recent_scene_patterns.camera_motion_signatures`には直近Sceneのtype、amplitude及びspeedを格納し、同じ弱い運動強度の反復を避ける。10秒以上の歌詞Sceneでは`lyric_visuals_light_8b`が正確に2 Shot、`lyric_visuals_full`が2～3 Shotを要求する。両プロファイルにはScene番号で循環する`required_camera_sequence`を追加し、実Shot数分のtype、amplitude及びspeedをPythonでも検証する。系列は平行・垂直・前後移動と複数面を見せる広い部分周回を交互に配置し、固定の例文を複製させない。

`lyric_action_preplan=true`のプロファイルでは、Song Bible又は直前Sceneを歌詞述語選択へ混入させる前に`clmv-lyric-action-line-v1`を実行する。入力はPlanningBriefのSubject定義と保持分析、対象Sceneの`lyric_lines`及び参照凡例に限定し、共通プロンプの画材・背景は除外する。一回の要求Scene数は`lyric_action_scenes_per_request`で決定し、8B向けは1とする。歌詞アンカー、可視応答方式、必要対象を置く構図、二～四個の物理ACTION及び完了結果を行指向で返す。各Sceneを独立検証し、正常レコードを保持して未解決Sceneだけを再試行する。既知の内部Subjectトークンが正確な`<Subject N>`へ復号された場合は、凡例に存在するSubjectだけを再保護して受理し、同じ表層エラーによる全retry消費を避ける。Subjectを記載するBlueprintでは、この段階で意図的な身体動作数も検証する。ただし構文、参照及び歌詞アンカーが有効で、欠陥が身体段階の不足だけである場合、モデル生成済みの歌詞分解を保持したまま媒体・歌詞非依存の全身移動を決定論的に補い、LLM再試行を行わない。検証済みBlueprintのACTIONは後段で固定されるため、固定後のScene生成を身体動作不足だけで再試行してはならない。再生成しても固ACTIONは変わらず、retry上限を消費するだけだからである。

検証済み値は`locked_lyric_action_blueprint`としてScene入力末尾へ置き、`active_section_motifs`及び`previous_scene_tail`より優先する。Scene LLMはカメラ、環境及び付加映像をblueprintへ適合させる。PythonはScene解析後のACTIONをblueprintで置換してShot順へ分配し、構図要件を各COMPOSITIONへ保持する。この決定論的適用は語彙辞書で歌詞意味を推測する処理ではなく、先行LLMが生成して検証した意味計画を後段LLMの複写偏重から保護する処理である。Blueprintのないsilent又はinstrumental SceneでScene LLMがSubject周辺の効果しか生成しなかった場合は、元のSceneを捨てず、Subject全体の移動を表す汎用ACTIONだけをPythonが不足数だけ追加する。これは歌詞解釈、物体又は画材を補完する処理ではない。

## 5. LLM出力プロトコル

プロトコル名は`clmv-song-bible-line-v4`とする。

```text
SONG_BIBLE
VISUAL_ARC<TAB>全体の視覚的な弧
VISUAL_ENRICHMENT_STRATEGY<TAB>選択した視覚拡張プロファイルに従う全体方針
CAMERA_STRATEGY<TAB>カメラ展開方針
SECTION_MOTIF<TAB>[Chorus]<TAB>セクションモチーフ
END_SONG_BIBLE
```

規則は次のとおりである。

- `VISUAL_ARC`は正確に1行。
- `VISUAL_ENRICHMENT_STRATEGY`は正確に1行。
- `CAMERA_STRATEGY`は1～16行。
- `SECTION_MOTIF`は、Pythonが渡した一意なセクションラベルごとに正確に1行。
- `SECTION_MOTIF`の順序はセクションラベルの初出順。
- `CONTINUITY_RULE`は出力しない。
- 正規形では最終物理行を`END_SONG_BIBLE`とする。8B等がこの終端マーカーだけを省略した場合は、先頭マーカー、全必須フィールド、順序、セクション集合及び全値がそれ以外すべて正常なときに限り、Pythonが終端を補完して受理する。未知行、途中の終端、必須行欠落又はセクション不一致があれば補完しない。
- 8B等が先頭の`VISUAL_ARC`だけを省略し、`VISUAL_ENRICHMENT_STRATEGY`以降の必須フィールド、順序、セクション集合、値及び終端がすべて正常な場合は、同じ全体要求を再試行しない。Pythonは新しい物体、動作、画風又は人物状態を追加しない連続性専用の保守的な`VISUAL_ARC`を補完し、WARNINGを出して受理する。`VISUAL_ARC`以外の欠落、未知行、順序違反又は残余部分の不備は補完対象にしない。
- 値は簡潔で自然な日本語とし、タブ、改行、ディレクティブ、コメント又は引用符を含めない。
- 歌詞を逐語表示、台詞、歌唱指示、字幕又は画面内文字へ変換しない。
- PlanningBriefで許可されていない人物、動物、音声又は解剖を追加しない。選択プロファイルが明示的に許可した非人物の象徴物、抽象物、空間的比喩、トランジション又は環境エフェクトだけは、そのScene契約内で追加できる。
- 歌詞中の具体的な身体動詞は、PlanningBriefと衝突せず実行可能なら人物の直接動作として優先する。衝突する場合だけ、許可された物体動作又は空間的比喩へフォールバックする。Song Bibleのモチーフはその可視応答を補助し、置き換えない。
- 複数人物が許可されていない場合、「二人」「旅人たち」等を生成しない。

## 6. Scene計画への適用

### 6.1 Active motif

各Sceneへは、そのSceneの`lyric_lines`に現れるセクションと一致する`SECTION_MOTIF`だけを`active_section_motifs`として渡す。Song Bibleの全モチーフを全Sceneへ渡してはならない。

`lyric_visuals_light_8b`では、小型モデルがモチーフをSceneの主動作として複写しないよう、`SECTION_MOTIF`を外部の非人物状態遷移へ限定する。Subjectトークン、人物動作、姿勢、身体状態、消失、溶解、変形又は接触操作をモチーフへ記述しない。Sceneでは選択した歌詞述語を先に完結させ、その後に限りモチーフを補助として使用する。

```json
{
  "active_section_motifs": [
    {
      "section": "[Verse 1]",
      "motif": "..."
    }
  ]
}
```

複数セクションを含むSceneは初出順に複数のactive motifを持つ。silent Scene又はセクションコメントのないSceneでは空配列とする。active motifは着想の補助であり、PlanningBrief及び固定Timelineより優先してはならない。

### 6.2 直前Sceneの最終状態

次バッチの先頭Scene及び部分再試行Sceneには、直前に確定したSceneの最終状態をScene入力内の`previous_scene_tail`として渡す。

```json
{
  "previous_scene_tail": {
    "scene_id": 6,
    "scene_intent": "...",
    "final_composition": "...",
    "final_action": "...",
    "environment": "...",
    "camera": {
      "type": "arc",
      "amplitude": "medium",
      "speed": "slow",
      "description": "..."
    }
  }
}
```

最終Shotの最後のACTIONを`final_action`とする。Scene全体又は過去の全Sceneを再送してはならない。最終状態だけを送ることで、人物姿勢、構図、照明、カメラ位置及び小道具状態の連続性を改善しながらコンテキスト増加を抑える。直前の`AUX_VISUAL`原文は小型モデルによる逐語複写を誘発するため送らず、その結果として継続必須の状態は`final_composition`、`final_action`又は`environment`へ保持する。

初回batchのScene 1は`null`とする。同じbatch内でまだ直前Sceneが検証済みでないSceneの値も`null`とし、応答内で直前に書いたSceneレコードを先行状態として扱うようsystem promptで指示する。`previous_scene_tail.scene_id`は内部対応付け専用であり、LLMは番号を創作文へ転記しない。継承する物体、姿勢、環境及び位置関係を、現在Sceneで直接観察可能な状態として書く。PythonはH3へ渡る`COMPOSITION`、`ACTION`、`AUX_VISUAL`、`ENVIRONMENT`及び`CAMERA`内の番号付きScene・Shot参照を拒否し、該当Sceneだけを再試行する。部分再試行時は、そのSceneより前で最も近い検証済みSceneから`previous_scene_tail`を再計算する。完全重複を検出した後は未解決Sceneを番号順に一件ずつ再送し、一件を確定するたび次Sceneの`previous_scene_tail`を更新する。

### 6.3 直近Sceneの選択傾向

各Scene要求は、そのSceneより前で検証済みの最大4 Sceneから`recent_scene_patterns`を作る。内容はScene ID、camera type列及び`AUX_VISUAL` kind列だけであり、創作文は再送しない。

```json
{
  "recent_scene_patterns": [
    {
      "scene_id": 6,
      "camera_types": ["truck"],
      "auxiliary_visual_kinds": ["spatial_trajectory"]
    }
  ]
}
```

これはカメラ又は付加映像kindの再利用を禁止する契約ではない。LLMが直近の選択偏重を把握し、現在の主要イベントに適する場合だけ再利用するための低コストな文脈である。同一batch内の後続Sceneについては、同じ応答内で既に書いたcamera typeと展開を追跡させる。

## 7. 重複Sceneの排除

重複Scene検出は必須とする。Pythonは各`PlannedScene`について、次を順序付きtupleとして署名化する。

```text
各Shotのstart_ms
composition
ACTION全文と順序
environment
camera.type
camera.amplitude
camera.speed
camera.description
```

署名化前にUnicode NFKC正規化、行内空白の単一化及び前後空白除去だけを行う。語句の意味推定、類義語展開又は編集距離による近似判定は行わない。

異なるScene IDの署名が完全一致した場合は、番号の若い既存Sceneを保持し、後のSceneだけを未解決として部分再試行する。診断には重複元Scene IDを含める。

```text
Scene 6 duplicates the complete visual plan of Scene 2
```

Scene intentだけの一致、同じカメラ種別の再利用、同じ環境の継続又は一部ACTIONの一致は重複エラーにしない。再試行上限まで完全重複が続いた場合は通常の未解決Sceneエラーとして停止する。

重複検出後に複数の未解決Sceneを同じ要求へ残すと、各Sceneが同じ`previous_scene_tail`から同一案を再生成しやすい。このため、そのbatchは時間順の一件再送へ切り替える。再送Sceneには重複元に加え、直前に検証済みのSceneを`avoid_duplicate_plans`として渡す。ただし、禁止例の本文はLLMがそのまま模倣する強いプライミングになるため再送しない。Scene ID、完全署名のSHA-256、Shot数、先頭ShotのACTION数及びcamera type列だけをfingerprintとして渡す。

Pythonはfingerprint群に存在しない`required_shot_count`と`required_first_shot_action_count`の組を`duplicate_repair`として決定論的に選び、LLMへ推奨修復形状として渡す。これは低temperatureで同じ文を返すモデルへ構造差を作らせる誘導であり、件数自体を独立した受理条件にはしない。件数が推奨値と異なっても、通常の構文・意味検証を満たし、完全なvisual-plan署名が重複元と異なる場合は受理する。直前Sceneの最終状態は`previous_scene_tail`から継承するが、構図、ACTION列、環境展開又はカメラ経路の少なくとも一つも実質的に変更させる。句読点、空白、言い換え又はScene intentだけの変更は回避として認めない。同じ`scene_id`と`duplicate_of`の診断は一回だけ記録する。

全Sceneが同じ構文エラーで不正になった場合も、大きなbatchをそのまま再送し続けてはならない。未解決Sceneを番号順の一件再送へ切り替え、Scene別の診断だけを`retry_feedback`へ渡す。`CAMERA`の`type`、`amplitude`又は`speed`へ書式説明語をそのまま出した場合は、Pythonが実在するcamera type、amplitude、speed及び意味の一致する説明例を`camera_protocol_repair`として付ける。再試行しても不正な場合は別の有効な組へ切り替える。

Scene全体が異なっても、正規化後の`AUX_VISUAL.description`が以前のScene又は同一Scene内で完全一致する場合は反復として扱う。kindだけを変えた同一文も反復である。正確に一個を循環割当するプロファイルでは、検証済みSceneを保持して重複した一行だけを専用プロトコルへ送り、禁止する完全一致文と`reveal_or_occlude`、`extend_or_contract`等の媒体非依存な変化操作を一つ渡す。最大2回で解消しなければWARNING付きで元の有効Sceneを採用し、全Sceneを繰り返し生成しない。その他のプロファイルでは後のSceneを部分再試行し、従来の`auxiliary_visual_repair`を渡す。Pythonは近似意味を推測せず、完全一致だけを判定する。

## 8. カメラ意味ガード

### 8.1 方針

カメラ意味検査は、一般的な日本語動詞又はユーザー入力中の「押す」「引く」等からカメラ意図を推測してはならない。検査対象はLLMが構造化して返した`CAMERA.type`と同じ`CAMERA`フィールドの`description`だけとする。

自動的にcamera type又はdescriptionを書き換えるfallbackは使用しない。例えば`pull`を`push`へ黙って変更すると、どちらがユーザー意図か確定できず隠れた処理になるためである。

### 8.2 UI

`CL MV Prompt Planner (GGUF)`へ次の入力を追加する。

| 名前 | 型 | 既定 | 候補 | 意味 |
| --- | --- | --- | --- | --- |
| `camera_guard` | COMBO | `warn` | `warn`, `strict` | 高確度のカメラ意味矛盾を警告又はScene再試行にする |
| `vocal_guard` | COMBO | `warn` | `warn`, `strict` | Timelineと矛盾する発声cueを警告又はScene再試行にする |

### 8.3 warn

`warn`では元の計画を変更せず受理し、ComfyUIコンソールへ黄色のWARNINGを出す。警告にはScene、Shot、camera type及び疑わしい記述を含める。

```text
[WARNING] [cl_mv_prompt_planner] Scene 11 Shot 1 camera type 'pull' may conflict with description containing '近づく'; continuing because camera_guard=warn
```

`planner_json`及びdebug metadataにも警告を記録する。

### 8.4 strict

`strict`では該当Sceneだけを未解決として保持せず、診断を`retry_feedback`へ入れて部分再試行する。他の正常Sceneを再送してはならない。再試行上限まで解消しない場合は停止エラーとする。

### 8.5 初期検査対象

初期実装は誤検出を避けるため、高確度の組み合わせだけを対象とする。

- `push`と「後退」「遠ざかる」。
- `pull`と「接近」「近づく」。
- `pan`と、カメラ本体の「平行移動」「横方向へ移動」。
- `truck`と、固定位置からレンズだけを「振る」「旋回する」。
- `static`と、カメラが「接近」「後退」「移動」「回り込む」。
- `arc`以外のcamera typeと「周回」「回り込む」「背後を通る」等の周回軌道。
- `roll`で回転方向が一切指定されていない。
- ACTION内にカメラ運動が書かれ、別の`CAMERA`フィールドも存在する。

単語の追加だけで意味が変わる場合があるため、検査語彙はテスト付きで限定的に拡張する。

## 9. 一般自然言語の意味矛盾

一般的な日本語文に含まれる動詞の組み合わせから、人物又は物体の意図をPythonが推定する処理は未定義かつ本仕様の対象外とする。

例えば次は文脈なしに安全な自動判定ができない。

- 「静止した姿勢から歩き始める」
- 「静止した姿勢で歩く」
- 「押した後で引く」
- 異なる人物が同時に押す・引く
- 否定、比喩、回想又はカメラ相対運動を含む文

Pythonはこの種の文を黙って書き換えない。一般意味矛盾はsystem promptで抑制し、将来導入する場合も独立した明示的オプション、警告及びテストを必要とする。H3へ直接英文を書く場合にも同じ曖昧性が存在するため、コンパイラ固有の構文エラーとして扱わない。

## 10. Song Bible system prompt要件

Song Bibleのsystem promptは、全プロファイル共通のプロトコルコアと、選択したプロファイル固有の創作ポリシーを実行時に連結して構成する。共通コアは権限、安全境界、固定入力、行プロトコル及び参照保護だけを規定し、歌詞の具現化密度、人物演技、付加映像数又は対象モデル別の推論手順を含めない。

共通コアは少なくとも次を明示する。

```text
You are the global creative-planning stage of a deterministic MiniMax H3 music-video planner.

planning_brief and the locked timeline are authoritative and immutable.
Your output is only a soft creative guide. It must never replace, reinterpret,
weaken, or contradict any planning brief or locked timeline value.

Do not introduce people, animals, voices, readable text, or anatomy not permitted
by planning_brief. The selected visual_enrichment_profile may authorize bounded
non-character auxiliary visuals; it never overrides planning_brief.
Do not create dialogue, singing instructions, laughter, subtitles, typography,
or on-screen lyrics.

Return only this tab-separated protocol:
SONG_BIBLE
VISUAL_ARC<TAB>one feasible global visual arc
VISUAL_ENRICHMENT_STRATEGY<TAB>one concise rule matching the selected profile
CAMERA_STRATEGY<TAB>one camera-development rule
SECTION_MOTIF<TAB>exact supplied section label<TAB>one reusable visual motif
END_SONG_BIBLE
```

さらに共通コアは次を要求する。

- `VISUAL_ARC`は固定位置及び移動禁止条件を破らず実現可能であること。
- `SECTION_MOTIF`は対応するセクションだけから着想し、別セクションの歌詞を使用しないこと。
- 固定条件にない複数人物を暗黙に追加しないこと。物体又は抽象映像は選択プロファイルが許可するScene契約内だけで追加すること。
- Song Bible自身に具体的なScene番号、Shot番号又は秒数を生成しないこと。
- 出力件数、順序、ラベル及び終端を厳守すること。

歌詞の直接動作化、空間的比喩、人物演技、モチーフの多様性及び視覚密度はプロファイル固有の`song_bible.txt`だけで定義する。`lyric_visuals_full`は大型モデル向けに複数の視覚層と柔軟な全体弧を許可し、`lyric_visuals_light_8b`は8B向けに外部非人物変化を一つへ絞り、`performance_only`は人物演技と既存環境だけへ限定する。他プロファイルのポリシーを同じsystem promptへ混在させてはならない。

## 11. Scene system prompt要件

Sceneのsystem promptも、全プロファイル共通のプロトコルコアと、選択したプロファイル固有の創作ポリシーを連結して構成する。共通コアは権限順位、固定Timeline、行プロトコル、フィールド分離、カメラ語彙、参照保護及び音声ロックを明示する。

```text
Authority order:
1. planning_brief subject identity and retention constraints
2. planning_brief global visual direction and prohibitions
3. locked Scene timing, vocal state, lip-sync, and soundscape
4. visual_enrichment_profile and each auxiliary_visual_contract
5. current Scene lyric_lines and lyric_response_contract
6. active_section_motifs
7. previous_scene_tail

Use only active_section_motifs supplied for the current Scene.
Never select or copy a motif from another section.

Every Scene must have a distinct visual purpose. Do not repeat another Scene's
complete composition, action sequence, environment, and camera plan.

COMPOSITION describes framing and subject placement.
ACTION describes subject or object action only.
AUX_VISUAL describes only a non-character animation layer authorized by its contract.
CAMERA is the only field that describes camera movement.

Every lyric Scene emits exactly one structurally valid LYRIC_RESPONSE before its Shots.
The selected profile policy defines how the lyric line and response mode are chosen.

Continue naturally from previous_scene_tail without repeating its final action.
planning_brief overrides Song Bible, lyrics, and previous_scene_tail.
```

同じbatch内では、後続Sceneが直前に出力したSceneレコードを継続情報として参照するよう要求する。ただし、前のSceneの全文コピーを許可してはならない。

創作判断はプロファイル固有の`scene_plan.txt`へ隔離する。`lyric_visuals_full`は大型モデル向けに歌詞の述語役割を保ちながら柔軟な微細動作、一～三層の付加映像及び長尺Sceneの二～三Shotを許可する。`lyric_visuals_light_8b`は`LYRIC FIRST`、`ROLE LOCK`、人物動作、移動カメラ、補助映像の順で判断させ、長尺歌詞Sceneを正確に二Shot、一個の`AUX_VISUAL`へ限定する。`performance_only`は`AUX_VISUAL`を出力せず、人物演技、既存物体及び既存環境だけで構成する。この分離により8B向けの強い拘束を修正しても、大型モデル向けの柔軟性を破壊しない。

## 12. エラー、警告及びdebug

次はSong Bible停止エラー又は再試行対象とする。

- 行プロトコル、フィールド順又は件数の不正。
- 未知、欠落、重複又は順序違反のセクションラベル。
- 未知又は破損した参照プレースホルダ。
- 台詞、歌唱、字幕又は画面内歌詞の生成。
- PlanningBriefにない参照ラベルの生成。

Scene完全重複は該当Sceneの部分再試行対象とする。`AUX_VISUAL.description`完全反復は、正確に一個を循環割当するプロファイルでは一行だけの限定修復、その他では該当Sceneの部分再試行対象とする。カメラ意味矛盾は`camera_guard`に従う。一般自然言語の意味矛盾は第9章のとおり未定義である。

Scene入力には、プロファイルと時間からPythonが決定した`line_protocol_shape_contract`を含める。最低・最大・正確なShot block数、最初の開始時刻、後続開始時刻の範囲、`END_SCENE`を置ける位置、許可されたASCIIの`LYRIC_RESPONSE` mode及びShot別`AUX_VISUAL`個数を、自然言語だけでなく数値又は配列として渡す。再試行時は最新の検証エラーに加えて、このレコード形状と`LYRIC_RESPONSE`の列構造・許可enumを必ず再掲する。モデルが既知のmodeを日本語へ直訳した場合は固定対応で正規化する。曖昧な`直接動作`、又は当該Sceneの`auxiliary_visual_contract.required_kind`を誤ってmode欄へ置いた場合は、検証済みACTIONにSubject参照があれば`direct_subject_action`、なければ`direct_object_action`とし、WARNINGへ記録する。これは創作内容の補完ではなく、既に出力された動作主体に基づくプロトコル復元である。正確に一個の`AUX_VISUAL`を要求するプロファイルで各Shotへ複製した場合は、必須kindと一致する最後の一個だけを保持してWARNINGへ記録する。ゼロ個を要求するプロファイル、必須kind不一致又は不足は従来どおりエラーとする。`SCENE_INTENT`は最終Markdownへ出力されない内部メタデータであるため、同フィールドだけに現れた`「」`及び`"`は除去して継続する。Shot、ACTION、環境又はカメラ本文の禁止引用符は従来どおり停止エラーとする。

Timelineが`voiced`として固定したSceneでは、`歌う`、`歌い`、`歌唱`及び`口パク`を、既存Source Vocalへ同期する視覚演技として許容する。これらは新しい発声イベント又は別音声の生成許可として扱わない。system promptは原則として「Source Vocalに合わせて口元と表情を動かす」と記述させ、前記語を避ける。

Timelineが`silent`のSceneでは前記語を含むすべての発声cueを`vocal_guard`の対象とする。有声Sceneでも`口ずさむ`、`叫ぶ`、`囁く`、`うめく`、`語る`等、Source Vocalとは別の発声を示すcueを対象とする。`warn`は元のSceneを変更せずWARNING及びdebug metadataへ記録する。`strict`は該当Sceneだけを再試行し、上限まで解消しない場合は停止する。

debug bundleには少なくとも次を保存する。

- 役割分離された`planning_brief`。
- `section_sources`及びScene別`lyric_lines`と`lyric_response_contract`。
- Song Bibleの生応答及び検証済み値。
- Scene別`active_section_motifs`。
- `previous_scene_tail`。
- `recent_scene_patterns`。
- 重複署名の比較対象Scene ID。
- compactな`avoid_duplicate_plans` fingerprintと、受理条件から独立した`duplicate_repair`推奨形状。
- `AUX_VISUAL`完全反復診断と`auxiliary_visual_repair`。
- Scene別`camera_protocol_repair`と使用した具体的camera値。
- `camera_guard`警告又はstrictエラー。
- `vocal_guard`警告又はstrictエラー。
- 選択した`visual_enrichment_profile`とScene別`auxiliary_visual_contract`。

## 13. テスト要件

実GGUFなしで少なくとも次を自動テストする。

- `CONTINUITY_RULE`を次期Song Bible応答として拒否する。
- PlanningBriefを`subject_identity`、`retention_constraints`及び`global_visual_direction`の構造配列として送る。
- 同一セクションの連続重複を一つのgroupへまとめる。
- セクションをまたぐ歌詞順序を維持する。
- Sceneへ該当するactive motifだけを渡す。
- 初回Sceneの`previous_scene_tail=None`。
- batch境界及び部分再試行で直前の検証済み最終状態を渡す。
- 完全一致するScene署名では後のSceneだけを再試行する。
- 完全一致する`AUX_VISUAL`記述では後のSceneだけを再試行する。
- 一部だけ同じSceneを重複と誤判定しない。
- `camera_guard=warn`で元計画を維持して警告する。
- `camera_guard=strict`で該当Sceneだけを再試行する。
- 一般ACTION中の「押す」「引く」をcamera type判定へ使用しない。
- 非`arc` cameraの周回記述を`camera_guard`で報告する。
- 同梱プロファイルを発見し、manifestとプロファイル固有ポリシーを検証する。
- 共通コアにプロファイル固有の創作指示が混入せず、三つの合成system prompt間で`ROLE LOCK`、大型モデル向け方針及び人物演技限定方針が漏洩しないことを検証する。
- `performance_only`で`AUX_VISUAL`を拒否する。
- `lyric_visuals_light_8b`でSceneごとの必須kindと正確に一個の`AUX_VISUAL`を検証する。
- `lyric_visuals_full`で許可kindと一～三個の`AUX_VISUAL`を検証する。
- 最終MarkdownのScene数、時間、歌詞、リップシンク及び音響が入力と完全一致する。

## 14. 実装済み移行項目

v0.2.0では次を実装した。

1. 入力JSONの`hard_requirements`及び`lyric_groups`化。
2. Song BibleからLLM生成`continuity_rules`を削除。
3. `active_section_motifs`によるScene別フィルタ。
4. `previous_scene_tail`の導入。
5. 完全重複Scene署名と部分再試行。
6. `camera_guard`のUI、警告、strict再試行及びdebug記録。
7. system promptを`clmv-song-bible-line-v3`及び`clmv-scene-line-v2`へ変更。
8. manifest駆動の視覚拡張プロファイルと`AUX_VISUAL`検証を追加。
9. 全体自動回帰テスト。Qwen 8B実モデルデバッグはローカル生成テストで実施する。

Song Bible v4及びScene v3では次を追加した。

1. `hard_requirements`を役割別`planning_brief`へ置換する。
2. 共通プロンプトを媒体・世界・禁止事項として扱い、Sceneイベントの反復元にしない。
3. 媒体非依存の意味関係と可視状態変化による歌詞映像化を要求する。
4. `recent_scene_patterns`により直近のcamera typeと付加映像kindを通知する。
5. `AUX_VISUAL.description`完全反復の部分再試行と`auxiliary_visual_repair`を追加する。
6. CAMERAを主要イベントへ結合し、非`arc`の周回記述をカメラ意味ガードへ追加する。

Scene v4及び全体契約`clmv-line-v5`では次を追加する。

1. Scene入力を1始まり`lyric_lines`へ変更する。
2. `LYRIC_RESPONSE`で各歌詞Sceneのアンカー行と`direct_subject_action`、`direct_object_action`又は`spatial_metaphor`を明示する。
3. 実行可能な具体的身体動詞を人物直接動作として優先し、媒体、背景運動、照明、感情又はカメラだけへの抽象化を禁止する。
4. 歌詞のないSceneは`0`と`instrumental_continuity`を明示する。

既存`planner_json`はPythonが生成する検証用出力なので維持する。LLMに最終JSONを生成させてはならない。
