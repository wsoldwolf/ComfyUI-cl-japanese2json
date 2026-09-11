# CL MV Prompt Planner Song Bible仕様

## 1. 状態と目的

本書は`CL MV Prompt Planner (GGUF)`が内部で使用するSong Bible v3の正本仕様である。v3はv0.2.0の固定条件分離を維持し、選択式の視覚拡張方針を加える。

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
  "hard_requirements": {
    "subjects": ["..."],
    "retention": ["..."],
    "common": ["..."]
  }
}
```

各文字列内の参照タグは既存`ReferenceProtector`で保護する。配列順は入力Markdownのバレット順を維持する。LLMはこれらの値を出力し直さない。

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

Scene計画入力では、同一Scene内で連続する同じセクションを一つの`lyric_group`へまとめる。別セクションを挟んで同じラベルが再登場する場合は、セクション遷移を保持するため別groupとする。

```json
{
  "lyric_groups": [
    {
      "section": "[Verse 1]",
      "kind": "verse",
      "lyrics": ["line 1", "line 2"]
    },
    {
      "section": "[Pre-Chorus]",
      "kind": "pre_chorus",
      "lyrics": ["line 3"]
    }
  ]
}
```

これにより、8Bモデルがセクション配列と歌詞配列の対応を推測する必要をなくす。

## 5. LLM出力プロトコル

プロトコル名は`clmv-song-bible-line-v3`とする。

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
- 値は簡潔で自然な日本語とし、タブ、改行、ディレクティブ、コメント又は引用符を含めない。
- 歌詞を逐語表示、台詞、歌唱指示、字幕又は画面内文字へ変換しない。
- PlanningBriefで許可されていない人物、動物、音声又は解剖を追加しない。選択プロファイルが明示的に許可した非人物の象徴物、抽象物、空間的比喩、トランジション又は環境エフェクトだけは、そのScene契約内で追加できる。
- 歌詞中の名詞又は動詞を無条件に物理動作へ変換しない。確定条件と衝突する場合は、許可された表情、姿勢、照明、構図又は抽象表現へ置き換える。
- 複数人物が許可されていない場合、「二人」「旅人たち」等を生成しない。

## 6. Scene計画への適用

### 6.1 Active motif

各Sceneへは、そのSceneの`lyric_groups`に現れるセクションと一致する`SECTION_MOTIF`だけを`active_section_motifs`として渡す。Song Bibleの全モチーフを全Sceneへ渡してはならない。

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

最終Shotの最後のACTIONを`final_action`とする。Scene全体又は過去の全Sceneを再送してはならない。最終状態だけを送ることで、人物姿勢、構図、照明、カメラ位置及び小道具状態の連続性を改善しながらコンテキスト増加を抑える。

初回batchのScene 1は`null`とする。同じbatch内でまだ直前Sceneが検証済みでないSceneの値も`null`とし、応答内で直前に書いたSceneレコードを先行状態として扱うようsystem promptで指示する。部分再試行時は、そのSceneより前で最も近い検証済みSceneから`previous_scene_tail`を再計算する。完全重複を検出した後は未解決Sceneを番号順に一件ずつ再送し、一件を確定するたび次Sceneの`previous_scene_tail`を更新する。

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

system promptは少なくとも次を明示する。

```text
You are the global creative-planning stage of a deterministic MiniMax H3 music-video planner.

hard_requirements and the locked timeline are authoritative and immutable.
Your output is only a soft creative guide. It must never replace, reinterpret,
weaken, or contradict any hard requirement or locked timeline value.

Lyrics are visual inspiration only. Do not treat every lyric noun or verb as a
literal visible action. If a lyric conflicts with hard requirements, express its
emotion through permitted pose, framing, lighting, or abstract imagery.

Do not introduce people, animals, voices, readable text, or anatomy not permitted
by hard_requirements. The selected visual_enrichment_profile may authorize bounded
non-character auxiliary visuals; it never overrides hard requirements.
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

さらに次を要求する。

- `VISUAL_ARC`は固定位置及び移動禁止条件を破らず実現可能であること。
- `SECTION_MOTIF`は対応するセクションだけから着想し、別セクションの歌詞を使用しないこと。
- 固定条件にない複数人物を暗黙に追加しないこと。物体又は抽象映像は選択プロファイルが許可するScene契約内だけで追加すること。
- Song Bible自身に具体的なScene番号、Shot番号又は秒数を生成しないこと。
- 全モチーフを同じ構図又は同じ人物動作へ縮退させないこと。

## 11. Scene system prompt要件

Scene system promptは権限順位とフィールド分離を明示する。

```text
Authority order:
1. hard_requirements
2. locked Scene timing, vocal state, lip-sync, and soundscape
3. visual_enrichment_profile and each auxiliary_visual_contract
4. active_section_motifs
5. current Scene lyrics
6. previous_scene_tail

Use only active_section_motifs supplied for the current Scene.
Never select or copy a motif from another section.

Every Scene must have a distinct visual purpose. Do not repeat another Scene's
complete composition, action sequence, environment, and camera plan.

COMPOSITION describes framing and subject placement.
ACTION describes subject or object action only.
AUX_VISUAL describes only a non-character animation layer authorized by its contract.
CAMERA is the only field that describes camera movement.

Continue naturally from previous_scene_tail without repeating its final action.
Hard requirements override Song Bible, lyrics, and previous_scene_tail.
```

同じbatch内では、後続Sceneが直前に出力したSceneレコードを継続情報として参照するよう要求する。ただし、前のSceneの全文コピーを許可してはならない。

## 12. エラー、警告及びdebug

次はSong Bible停止エラー又は再試行対象とする。

- 行プロトコル、フィールド順又は件数の不正。
- 未知、欠落、重複又は順序違反のセクションラベル。
- 未知又は破損した参照プレースホルダ。
- 台詞、歌唱、字幕又は画面内歌詞の生成。
- PlanningBriefにない参照ラベルの生成。

Scene完全重複は必ず該当Sceneの部分再試行対象とする。カメラ意味矛盾は`camera_guard`に従う。一般自然言語の意味矛盾は第9章のとおり未定義である。

Timelineが`voiced`として固定したSceneでは、`歌う`、`歌い`、`歌唱`及び`口パク`を、既存Source Vocalへ同期する視覚演技として許容する。これらは新しい発声イベント又は別音声の生成許可として扱わない。system promptは原則として「Source Vocalに合わせて口元と表情を動かす」と記述させ、前記語を避ける。

Timelineが`silent`のSceneでは前記語を含むすべての発声cueを`vocal_guard`の対象とする。有声Sceneでも`口ずさむ`、`叫ぶ`、`囁く`、`うめく`、`語る`等、Source Vocalとは別の発声を示すcueを対象とする。`warn`は元のSceneを変更せずWARNING及びdebug metadataへ記録する。`strict`は該当Sceneだけを再試行し、上限まで解消しない場合は停止する。

debug bundleには少なくとも次を保存する。

- 構造化された`hard_requirements`。
- `section_sources`及びScene別`lyric_groups`。
- Song Bibleの生応答及び検証済み値。
- Scene別`active_section_motifs`。
- `previous_scene_tail`。
- 重複署名の比較対象Scene ID。
- compactな`avoid_duplicate_plans` fingerprintと、受理条件から独立した`duplicate_repair`推奨形状。
- Scene別`camera_protocol_repair`と使用した具体的camera値。
- `camera_guard`警告又はstrictエラー。
- `vocal_guard`警告又はstrictエラー。
- 選択した`visual_enrichment_profile`とScene別`auxiliary_visual_contract`。

## 13. テスト要件

実GGUFなしで少なくとも次を自動テストする。

- `CONTINUITY_RULE`を次期Song Bible応答として拒否する。
- PlanningBriefをsubjects、retention、commonの構造配列として送る。
- 同一セクションの連続重複を一つのgroupへまとめる。
- セクションをまたぐ歌詞順序を維持する。
- Sceneへ該当するactive motifだけを渡す。
- 初回Sceneの`previous_scene_tail=None`。
- batch境界及び部分再試行で直前の検証済み最終状態を渡す。
- 完全一致するScene署名では後のSceneだけを再試行する。
- 一部だけ同じSceneを重複と誤判定しない。
- `camera_guard=warn`で元計画を維持して警告する。
- `camera_guard=strict`で該当Sceneだけを再試行する。
- 一般ACTION中の「押す」「引く」をcamera type判定へ使用しない。
- 同梱プロファイルを発見し、manifestとsystem prompt断片を検証する。
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

既存`planner_json`はPythonが生成する検証用出力なので維持する。LLMに最終JSONを生成させてはならない。
