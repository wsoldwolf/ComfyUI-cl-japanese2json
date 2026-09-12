# CL MV Prompt Planner コア仕様

## 1. 目的

`CL MV Prompt Planner (GGUF)`は、`CL Vocal to Prompt Segments`が生成した時間固定の縮小Markdownと、ユーザーが既存文法で記述した人物・保持・世界観の指示から、MiniMax H3向けMVの人物動作、情景及びカメラワークを計画する。

出力は日本語縮小Markdownであり、既存の`CL Japanese to JSON (GGUF)`へ渡してContex-Loop Plan JSONへ変換する。プランナー自身はH3 Full-Referenceの6セクション又は最終JSONを生成しない。

設計根拠はMiniMax公式`VIDEO_PROMPT_WRITING_GUIDE_base_en.md`及び`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`である。特に、`detailed_description`を筋書きの要約ではなく、構図、人物位置、環境・照明、時間順の動作、状態変化、カメラ運動及び現在音響へ分解する規則を採用する。

## 2. 処理境界

Pythonが決定するもの:

- Scene番号、Scene順序、Scene秒数及び`継続`の有無
- ソース音声上の開始・終了時刻
- VADの`silent`又は`voiced`
- `// 歌詞:`と`// 楽曲セクション:`の所属
- Source Vocalリップシンク行
- `## 音響`とソース音声維持指定
- Subject、Picture、Video、Audio参照の復元
- Shot開始時刻の範囲・単調増加検証
- 最終縮小Markdownの構文
- PlanningBriefの役割別`planning_brief`構造化
- Sunoセクションの`section_sources`及びScene別`lyric_lines`
- Sceneごとの歌詞アンカー番号及び可視応答方式の検証
- Scene別active motifの選択
- 直前に検証済みのSceneから作る`previous_scene_tail`
- 完全一致する重複Sceneの検出
- 選択した視覚拡張プロファイルとScene別`AUX_VISUAL`契約

LLMが計画するもの:

- 楽曲全体の視覚的な弧と反復モチーフ
- Sceneごとの視覚的意図
- 構図、人物動作、環境変化及び連続性
- 公式H3カメラ種別、移動量、速度及び自然言語の運動説明
- 歌詞を逐語表示せず、具体的な身体動作、物体動作又は空間的比喩へ変換する可視応答
- 選択プロファイルが許可した範囲内の付加映像

LLMは固定タイムライン、歌詞、発声許可、参照番号又はディレクティブを追加、削除、移動、変更しない。

Plannerのsystem promptは`node_mv_prompt_planner/prompts/core/`の共通プロトコルコアと、`node_mv_prompt_planner/prompts/profiles/<profile_id>/`の創作ポリシーから合成する。共通コアは行書式、固定Timeline、保持分析、参照、H3フィールド分離、カメラ型及び音声ロックだけを規定する。歌詞の具現化方法、人物演技、付加映像密度及び長尺SceneのShot方針は選択プロファイルだけが定義し、他プロファイルの指示を混在させない。これにより、8B向けの限定的な推論手順と大型モデル向けの柔軟な創作方針を独立して改善できる。

## 3. 入力

### 3.1 `prompt_segments`

`CL Vocal to Prompt Segments.prompt_text`を入力する。次のコメントを構造データとして読む。

歌詞を映像計画へ使用する場合、上流の`include_lyrics_comments`を`True`にする。`False`でも時間固定のインストゥルメンタル計画は可能だが、プランナーにはSunoセクション及び歌詞が渡らない。

```markdown
// シーン 1
# シーン 10秒
// 検出状態: voiced。ソース範囲 00:12.000-00:22.000。
// 楽曲セクション: [Verse 1]
// 歌詞: 最初の歌詞
// 歌詞: 次の歌詞
## ショット
* <Subject 1>はソースボーカルの抑揚に合わせて自然に演技する。
* リップシンク: <Subject 1> <- ソースボーカル
## 音響
* 発声: ソースボーカルのみ
* ソース音声: 完全維持
```

全文入力では`// シーン N`は1から連続し、ソース範囲も0から連続する。`CL Scene Limiter (Reduced Markdown)`で切り出した部分入力では、最初の番号を任意の正整数Nとし、元のScene番号及びソース音声上の絶対範囲を維持した連続サブタイムラインを許可する。後続番号とソース範囲は欠落なく連続し、各範囲長はScene秒数と一致しなければならない。Scene 1だけは`継続`を許可しないが、Scene 2以降から始まる部分入力の先頭では元の`継続`を維持できる。既存Shot本文は計画対象として置換するが、リップシンクと音響は固定要素として再利用する。

### 3.2 Sunoセクション

Suno形式の全行見出し`[... ]`は、次の歌詞行へ所属するセクションとして保持する。既知の種類は次のとおりである。

- `intro`
- `verse`
- `pre_chorus`
- `chorus`
- `post_chorus`
- `hook`
- `refrain`
- `bridge`
- `breakdown`
- `interlude`
- `instrumental`
- `solo`
- `final_chorus`
- `outro`

番号付き見出しを許容する。未知の見出しも`custom`として原文を保持し、黙って削除しない。

### 3.3 `planning_markdown`

ユーザーは新しい文法を学ばず、既存縮小Markdownの次のサブセットを使う。

```markdown
# サブジェクト
* 成人の中性的な無毛の人型。左右非対称の顔と平坦な体格を持ち、不透明な黒い長衣を着る。

# 保持分析
* <Subject 1> 完全に保持: 同一の頭部、顔、体格、衣装、手足及び各手五本の指を維持する。頭髪、裸体及び余分な手足を生成しない。

# 共通プロンプト
* 全編を荒い多数の線による立体的な抽象映像として描き、滑らかな3DCGにしない。
* 画面内に字幕、歌詞、文字、ロゴ及び透かしを表示しない。
```

認識するトップレベルディレクティブは`# サブジェクト`、`# 保持分析`、`# 共通プロンプト`だけである。順序はこの通り、各1回までとする。Scene、Shot、音響又は自由本文はエラーにする。サブジェクトは必須、他は任意である。

人間入力は「誰を描くか」「何を変えないか」「どの画風・世界・禁止事項を共有するか」だけを簡潔に記述する。Sceneごとの具体的な人物動作、歌詞の映像化手順及びカメラ軌道を列挙しない。それらを`# 共通プロンプト`へ列挙すると、LLMが全Sceneで再利用する安全なテンプレートになり多様性を損なうためである。カメラ又はモーションの希望を書く場合も、閉じた手順列ではなく「固定撮影を避ける」「静かな演技を優先する」のような全体傾向を一つのバレットで示す。

`# 共通プロンプト`は全編の画材、画風、世界、照明、連続性及び禁止事項を定める。ここに記載された媒体、背景運動、ポーズ例又はカメラ例は、それ自体を各Sceneの主要イベントとして反復する指示ではない。ただし「全Scene」「常に」「決して～しない」等として明示した普遍条件は文字どおり全Sceneへ適用する。

## 4. 中間表現

プランナーは次の3層を分離する。

1. `PlanningBrief`: ユーザーのSubject、Retention、Commonを原文のまま保持し、LLM入力では`subject_identity`、`retention_constraints`、`global_visual_direction`へ役割分離する。
2. `TimelineDocument`: Scene、時間範囲、VAD状態、Sunoセクション、歌詞、固定リップシンク及び音響を保持する。
3. `LyricActionBlueprint`: 有効化したプロファイルで、Scene別の歌詞アンカー、構図要件、中心動詞を実行する時系列動作及び可視結果を保持する。
4. `MVPlan`: song bibleとScene別のShot計画を保持する。

参照タグはLLM送信前に型付きプレースホルダへ置換し、応答検証後にPythonが原文へ復元する。LLMが未知のプレースホルダを生成した場合又は必要な参照を破損した場合は、そのSceneだけを未解決とする。

## 5. 階層型LLM計画

### 5.1 Song bible

最初の1回で、全歌詞セクションとユーザー指示から次を固定フィールドの行指向形式で生成する。LLMへJSON構文を生成させない。

- 全体の視覚的な弧
- セクション別の色、形、照明及び抽象モチーフ
- カメラワークの展開方針

固定条件はPythonが解析した`PlanningBrief`だけを正本とし、Song Bibleへ再生成させない。Song Bible v4、確定条件と創作情報の分離、構造化JSON入力、active motif、直前Sceneの最終状態、重複Scene及び付加映像反復の排除、カメラ意味ガードは`docs/cl_mv_prompt_planner_song_bible_spec.md`を正本とする。視覚拡張プロファイル、`AUX_VISUAL`及び拡張方法は`docs/cl_mv_prompt_visual_profiles_spec.md`を正本とする。

### 5.2 Sceneバッチ

`lyric_action_preplan=true`のプロファイルでは、各Sceneバッチの本計画より前に歌詞Sceneだけを`clmv-lyric-action-line-v1`へ渡す。一回の要求数はプロファイルの`lyric_action_scenes_per_request`で決定し、8B用は1件とする。この小さい行指向段階はカメラ、Song Bible、section motif、直前Scene及び共通プロンプの画材・背景を扱わず、Subject定義と保持分析の下で歌詞の主体、中心動詞、対象、接触経路及び結果だけを二～四個のACTIONへ分解する。正常レコードを保持して不正又は欠落Sceneだけを再試行する。小型モデルが既知の内部Subjectトークンを正確なユーザー表記`<Subject N>`へ復号して返した場合だけ、参照凡例との完全一致を確認して保護トークンへ戻し、WARNING付きで受理する。未定義Subject、`Picture`、`Video`、`Audio`等の生タグ及び一般Scene応答の生タグは従来どおり停止エラーとする。

preplan結果は`locked_lyric_action_blueprint`として本計画へ渡す。本計画は構図、環境、カメラ及び付加映像をblueprintへ適合させ、Pythonは解析後のACTION列を検証済みblueprintで決定論的に固定する。複数Shotでは時系列を維持して分配し、必要対象を各COMPOSITIONへ保持する。したがって8Bが共通画材又は直前Sceneを複写しても、歌詞由来の中心動詞は最終Markdownから消失しない。この段階を無効にしたプロファイルの要求回数と生成経路は変えない。

Sceneを`scenes_per_batch`件ずつ計画する。各Sceneは安定した`scene_id`を持つ。要求データには`requested_scene_ids`と`requested_scene_count`を明記する。LLM応答は`SCENE`から`END_SCENE`までの独立した行指向レコードとする。正常なSceneは即時確定し、不正又は欠落したSceneだけを新しいseedで再送する。`retry_max`はバッチ全体ではなく各Sceneの個別上限とし、先に修復するSceneが後続Sceneの再試行回数を消費してはならない。全Sceneを再送してはならない。再試行時は前回の構文又は検証エラーを`retry_feedback`として明示し、同じ誤りをseedだけ変えて反復させない。

各LLM要求はchunk数の増加とは独立した10秒周期のハートビートを持つ。INFOログへ要求名、総経過時間、受信chunk数、最終chunkからの経過時間及び進捗callback状態を出す。初回chunk待ちは120秒、出力開始後の無進行は45秒を上限とし、超過時は`PlannerInferenceStallError`でその要求を打ち切って再試行する。複数Scene要求のstallでは次回を一Sceneずつへ縮退し、どのScene又は入力規模で停止するかを分離できるようにする。

各batchは独立したチャット要求とし、暗黙の会話履歴へ依存しない。継続性に必要な情報はSong Bible、Sceneに該当する`active_section_motifs`及び、そのSceneより前で最も近い検証済みSceneの最終状態を表す`previous_scene_tail`として毎回明示する。同一応答内の後続Sceneは直前のSceneレコードを継続情報として使用する。`previous_scene_tail.scene_id`はLLM入力を対応付ける内部識別子に限り、`COMPOSITION`、`ACTION`、`AUX_VISUAL`、`ENVIRONMENT`又は`CAMERA`へ`Scene 10`、`シーン10`等の番号参照を出力してはならない。継承物、姿勢、環境及び空間関係は、現在見えている具体的状態として再記述する。直前の`AUX_VISUAL`原文は小型モデルがそのまま複写する強いプライミングになるため`previous_scene_tail`へ含めず、継続に必要な可視状態は最終構図、最終ACTION及び環境へ保持する。部分再試行時はSceneごとに`previous_scene_tail`を再計算する。完全重複を検出したbatchは一件ずつの再送へ切り替える。既知の重複元と直前Sceneは本文を再掲せずcompact fingerprintとして`avoid_duplicate_plans`へ渡し、`duplicate_repair`で未使用のShot/ACTION件数を推奨して構造差を促す。

batch応答から正常Sceneを一件も回収できない場合も、次回から番号順の一件再送へ切り替える。Scene固有のエラーだけを再送し、camera schema値の不正には具体的な`camera_protocol_repair`を添える。

行指向応答はJSONの引用符、エスケープ、配列及び波括弧を使用しない。PythonはSceneレコードを個別に抽出し、固定フィールド順、必須フィールド、Scene ID、Shot時刻、ACTION連番、参照及びカメラ値を検証する。ACTION番号は各Shotで1から振り直す形式を正規形とする。ただし複数ShotでLLMがScene全体の通し連番を返した場合、物理行順と番号が連続して一意であれば同じ時系列として受理し、内部のShot別ACTION順へ正規化する。欠番、重複又は逆行は推測補修せず該当Sceneを再試行する。小型モデルが複数Shotの途中で`END_SCENE`を早期出力し、同じScene IDを再掲して残りの`SHOT`ブロックだけを続けた場合は、最初の断片だけが`SCENE_INTENT`及び`LYRIC_RESPONSE`を持ち、後続断片が`SHOT`から始まり、各断片が構造的に閉じている場合に限り、Pythonが一つのSceneへ再結合して通常検証を行う。複数断片がそれぞれSceneメタデータを持つ完全重複又は競合応答は再結合せず、従来どおり該当Sceneを再試行する。`CAMERA<TAB>CAMERA<TAB>type<TAB>amplitude<TAB>speed<TAB>description`のようにフィールド名だけが一度重複し、後続のtype、amplitude、speed及びdescriptionがすべて一意かつ有効な場合は、重複した二番目の`CAMERA`だけを除去して通常検証する。それ以外の列ずれ、未知値又は複数候補を推測補修してはならない。

### 5.3 行指向プロトコル

Song bible `clmv-song-bible-line-v4`は次の順序とする。`<TAB>`は実際のタブ文字を表す。

```text
SONG_BIBLE
VISUAL_ARC<TAB>全体の視覚的な弧
VISUAL_ENRICHMENT_STRATEGY<TAB>選択プロファイルに従う付加映像方針
CAMERA_STRATEGY<TAB>カメラ展開方針
SECTION_MOTIF<TAB>[Chorus]<TAB>セクションモチーフ
END_SONG_BIBLE
```

`END_SONG_BIBLE`は正規形では必須である。ただし小型モデルが最終の終端行だけを省略し、そこまでの先頭、必須フィールド、順序、セクション数・ラベル及び値が完全に検証できる場合、Pythonは終端だけを決定論的に補完してSong Bibleを受理する。本文欠落、未知行、途中に置かれた終端又はセクション不一致は補完対象にしない。

小型モデルが先頭の`VISUAL_ARC`だけを省略して`VISUAL_ENRICHMENT_STRATEGY`から開始した場合は、後続の全フィールド、順序、セクション集合、値及び終端が正常なときに限り、Pythonが新しい演出要素を含まない連続性専用の保守的な`VISUAL_ARC`を補完してWARNING付きで受理する。他の必須フィールド欠落又は後続不正は従来どおり失敗とする。

歌詞動作preplan `clmv-lyric-action-line-v1`は次の順序とする。

```text
LYRIC_SCENE<TAB>1
LYRIC_RESPONSE<TAB>1<TAB>direct_subject_action
COMPOSITION_REQUIREMENT<TAB>接触前に必要な対象を認識できる構図
ACTION<TAB>1<TAB>予備動作
ACTION<TAB>2<TAB>中心動詞を対象へ実行する動作
VISIBLE_RESULT<TAB>同じ対象へ残る完了結果と離脱
END_LYRIC_SCENE
```

`ACTION`は1から始まる二～四行とする。`COMPOSITION_REQUIREMENT`、全ACTION及び`VISIBLE_RESULT`は参照保護、禁止記号、内部Scene・Shot番号参照及びSubject結合を検証する。個別の物体名や画材をPython辞書で推測せず、意味分解は選択LLMへ委ねる。

Scene `clmv-scene-line-v4`は次の順序とする。

```text
SCENE<TAB>1
SCENE_INTENT<TAB>映像意図
LYRIC_RESPONSE<TAB>1<TAB>direct_subject_action
SHOT<TAB>0
COMPOSITION<TAB>構図
ACTION<TAB>1<TAB>最初の動作
ACTION<TAB>2<TAB>次の動作
AUX_VISUAL<TAB>symbolic_object<TAB>歌詞を映像化する具体的な補助アニメーション
ENVIRONMENT<TAB>背景と照明
CAMERA<TAB>arc<TAB>medium<TAB>moderate<TAB>被写体との関係を含むカメラ記述
END_SHOT
END_SCENE
```

SceneはShotの前に正確に一つの`LYRIC_RESPONSE`を持つ。歌詞があるSceneでは、`lyric_lines`の一つを1始まりの番号で選び、方式を`direct_subject_action`、`direct_object_action`又は`spatial_metaphor`から指定する。選択順は、明示又は含意された主体、物理的な中心動詞及び制約と両立する対象を持つ、最も早い完全な述語を優先する。独立した可視動詞を持たない後続の反復句、強調句、大文字句又は短い断片を、先行する完全な述語より優先してはならない。画面内文字の禁止だけを理由に、物理的な筆記又は彫刻動作と非言語的な傷・溝へ変換できる先行述語を不可能と判定しない。歌詞がないSceneは`0`と`instrumental_continuity`を指定する。`direct_subject_action`では少なくとも一つのACTIONが`<Subject N>`を明示しなければならない。

選択歌詞は単なる感情又は動詞ではなく、映像化可能な主体、物理動作、具体的な対象物及び動作後の可視結果からなる述語フレームとして扱う。明示された具体物は、接触より前の`COMPOSITION`で種類、形、尺度、向き及び位置を認識可能に配置し、予備動作、接触及び結果を通して同じ対象名と空間位置を維持する。接触時だけ突然出現させたり、無名の面、共通画材、粒子、光又は無関係な象徴へ置換したりしない。共通画材は具体物を描画、被覆、侵食又は変形する表現媒体として使用できるが、具体物そのものの代替にはしない。主要ACTIONは選択歌詞の中心動詞が表す物理操作そのものを、適合する身体部位又は道具によって対象へ実行する。同じ対象に触れる、押す、叩く、潰す、運ぶ又は移動するだけでは、中心動詞が別の操作を指定している場合の代替にならない。対象へ接触する物理動作は、歌詞及び明示制約から必要な身体部位又は道具、正確な接触点、方向と軌道、反復又は拍、力加減、抵抗、蓄積する対象変化及び離脱を導出し、必要な二～四個の連続ACTIONへ分解する。まず歌詞の動作機構を確定し、楽曲セクションの強度やモチーフはその速度、振幅又はカメラ強度だけを調整する。繊細又は漸進的な動詞を、激しい楽曲であることだけを理由に大振りや単発衝撃へ置換しない。カメラは重要な接触区間で身体部位又は道具、接触点及び蓄積する結果を確認可能に保つ。対象へ変化を与える他動的動作では、対象との接触と変化後の状態を同じScene内の時系列ACTIONで示す。結果が通常は言語情報を伴う場合も、歌詞本文、名前、単語又は有効な字形列を表示せず、対象へ付着した長さ、向き、曲率及び間隔が不規則な孤立した非言語的な短い傷又は溝へ抽象化する。基準線、横一列又は縦一列の配列、反復字形、字間、単語間隔、鏡文字及び反射文字は作らない。その痕跡まで明示的に禁止されている場合は別の実行可能な歌詞を選択する。歌詞が具体物を明示又は明確に含意しない場合、LLMがこの規則だけを理由に対象物を追加してはならない。

Blueprint及びScene応答のH3可視フィールドへ`文字`、`名前`、`歌詞`、`字幕`、`文章`、`単語`、`字形`、`記号`、`標識`、`ロゴ`、`透かし`、`タイポグラフィ`、`キャプション`又は`テキスト`等の画面内文字を誘発する語を含めてはならない。英語の同等語も禁止する。PythonはこれをScene単位で検証し、違反Sceneだけを未解決として再試行する。歌詞コメント及び非表示の内部`SCENE_INTENT`は検査対象外とする。Rendererは最終Markdownの`# 共通プロンプト`へ、文字列、配列、鏡像及び反射文字を抑制する決定論的な規則を一度だけ追加する。

`retention_execution_contract`は保持分析に列挙された身体、解剖、衣装、被覆及び外観を物理的な不変対象として扱う。ACTION、AUX_VISUAL、ENVIRONMENT又はCAMERAは、保持対象を除去、露出、開放、剥離、亀裂、破断、溶解、侵食又は再形成の変形元・変形先にしてはならない。歌詞由来の破壊又は変形が保持条件と衝突する場合は、許可された外部物体又は非人物レイヤーへ適用する。遮蔽は一時的な前後関係だけとし、その下の身体と衣装を欠落させない。

`ACTION`にSubjectが現れ、PlanningBriefが全身の完全静止を明示していない場合、少なくとも一つの`ACTION`は当該`<Subject N>`を明示し、ワールド位置、重心、胴体の向き、身体レベル、姿勢又は手足の構成を可視的に変化させる。受動的な浮遊、上下動、揺れ、呼吸、衣装、髪、粒子、表面エフェクト、表情、口、視線又はカメラだけの運動を身体動作として数えない。10秒以上にわたりSubjectを動作として記載するSceneでは、異なるACTION行による少なくとも二つの意図的な身体段階へ配分する。保持対象の顔部品、眼窩、身体輪郭又は衣装層を移動・変形する記述は身体動作として数えない。Pythonは`subject_motion_contract`をScene入力へ渡す。`lyric_action_preplan`を使用する場合は、構文、参照及び歌詞アンカーが有効なBlueprintを保持し、身体段階だけが不足する場合は、現在の形状と外観を維持した全身移動を最大二段階まで決定論的に補う。この不足だけを理由としてBlueprintを再推論してはならない。検証済みBlueprintでSceneのACTIONを固定した後に同じ動作条件を理由としてScene全体を再試行してはならない。Blueprintを使用しないSceneで不足した場合も、LLMが作成した構図、ACTION、環境及びカメラを保持し、現在の形状と外観を維持したままSubject全体を移動させる媒体・歌詞・解剖非依存のACTIONを不足数だけ決定論的に追加する。補正内容はWARNINGへ記録し、後者は`planner_json.metadata.motion_repairs`にも記録する。物体だけのScene及び人物を持たない抽象cutawayは、ACTIONにSubjectを記載しない限りこの検査の対象外とする。

人物の画風を荒く又は抽象的に指定しても、人物構造そのものを安価な平面記号へ簡略化しない。`subject_rendering_contract`は顔・頭部の固有構造、頭部・頸部・胸郭・骨盤・四肢、衣装層、重なり、短縮遠近、輪郭の回り込み及び画風に適合する明暗面を維持させる。これは写実化を強制せず、指定画材を立体構造へ適用する品質下限である。回り込み中も同じ立体ランドマーク、同一性、解剖及び衣装を維持する。

各Shotは次を持つ。

- `start_ms`: Scene相対時刻。最初は0、以後は厳密に増加し、Scene長未満
- `composition`: 画角、人物・物体の位置及び視線
- 1～8個の`ACTION`: 1から始まる連番。実行時系列と優先順位の昇順に並べる
- 選択プロファイルのScene契約を満たす`AUX_VISUAL`: 人物動作及びカメラとは独立した付加映像
- `environment`: 背景、照明及び状態変化
- `camera.type`: 公式カメラ種別
- `camera.amplitude`: 移動時は`small`、`medium`又は`large`、`static`だけは`none`
- `camera.speed`: 移動時は`slow`、`moderate`又は`fast`、`static`だけは`none`
- `camera.description`: 被写体との関係が分かる日本語の具体的運動

Scene要求には`camera_choreography_contract`を付ける。`shot_count`は選択プロファイルの長尺歌詞Scene範囲を明示し、Pythonも応答のShot数を同じ範囲で検証する。`lyric_visuals_light_8b`及び`lyric_visuals_full`では、イントロ、無歌詞、短尺及び歌詞Sceneのすべてへ、Scene番号から決定論的に選ぶ`required_camera_sequence`を渡す。10秒以上の歌詞Sceneは従来どおりプロファイル所定の複数Shot、それ以外は一つの連続Shotを使用する。各要素はShot番号、camera type、amplitude、speed及び媒体・歌詞・背景に依存しない`spatial_goal`を持つ。LLMは種別・移動量・速度を変更せず、当該Sceneの動作、対象、前景及び背景に適合する日本語の軌道だけを創作する。Pythonは実Shot数に対応する要素との一致を検証する。`arc`のtype、amplitude及びspeedが一致している一方で、自然言語の経路だけが前・側・後の複数領域を十分に表していない場合、Scene全体を再推論せず、そのCAMERA記述だけを同じ系列に対応する媒体非依存の決定論的な半周経路へ置換する。これを創作内容、ACTION、構図、環境又は他のカメラ値の修正には使用しない。

系列はpush、pull、truck、pedestal及びarcを6種類の組合せで循環し、長尺の複数Shotだけでなく短尺・無歌詞Sceneにも移動カメラを供給する。同じ構文例を全Sceneへ複製する方式は使用しない。arcは全周固定ではなく、前方斜めから側面を経て後方斜め、又はその逆等、概ね半周の複数面を見せる軌道とする。必須arcの日本語記述には周回経路に加え、前方・側面・後方のうち最低二領域を明記する。単に「周囲を移動する」と記す一視点の擬似arcは構造エラーとする。これにより被写体を立体的に見せつつ、毎Scene同じ周回になること及び全周移動による人物同一性の崩れを避ける。

移動カメラは開始視点、物理的通過軌道、異なる終了視点、前景及び背景の奥行き基準を記述し、相対運動による視差を成立させる。カメラ運動はScene最後の主要動作まで展開し、序盤で寄り又は引きを完了した後に同一構図を保持しない。`tracking`は人物又は主要物体がワールド座標上を移動する場合だけ使用し、手、顔、視線又は局所ジェスチャーだけを一定距離で追わない。`shake`は短い衝撃補助であり、長いSceneの唯一の主運動にしない。

10秒以上の歌詞Sceneは、選択プロファイルが指定するShot数範囲を使用する。`lyric_visuals_light_8b`は正確に2 Shot、`lyric_visuals_full`は2～3 Shotとし、予備動作、接触及び結果の意味的遷移へ境界を置く。後続Shotは、物理的に不可欠な場合を除きカメラ種別を変え、角度、距離、奥行き関係又は提示情報も変化させる。均等秒数による機械的分割は禁止する。視点変更時も同じSubject、身体、衣装及び物体を維持し、カメラ移動を変身又は複製として扱わない。

公式カメラ種別は`zoom`、`push`、`pull`、`pan`、`truck`、`tilt`、`pedestal`、`arc`、`tracking`、`static`、`shake`、`pov`及び`roll`とする。レンズズームとカメラ移動、panとtruck、tiltとpedestalを混同しない。

LLMが`camera.description`を`カメラは`又は`カメラが`で開始した場合、その接頭辞だけを決定論的に除去して受理する。意味のある本文が残らない場合又はそれ以外の`カメラ...`接頭辞は不正とする。`static`へ移動用速度が返された場合も、動きは存在しないため`none`へ正規化する。これらの意味を変えない表層差だけを理由にScene全体を再生成しない。

`camera_guard=warn`では、構造化された`CAMERA.type`と同じCAMERAフィールドの記述に高確度の矛盾がある場合、計画を変更せずWARNINGを出す。`strict`では該当Sceneだけを再試行する。対象はpush/pullの移動方向、pan/truckの回転・平行移動、staticの移動、rollの方向欠落及びACTIONへ明示されたカメラ運動である。人物又は物体の一般動作に含まれる「押す」「引く」は検査しない。意味の推測による自動書き換えは行わない。

`vocal_guard=warn`では、Timelineの発声状態と矛盾する発声cueをWARNINGとして報告し、Sceneを変更せず受理する。`strict`では該当Sceneだけを再試行する。ただし`voiced` Sceneの`歌う`、`歌い`、`歌唱`及び`口パク`は、固定済みSource Vocalへ同期する視覚演技として常に許可する。`silent` Sceneの歌唱、又は状態を問わず新しい叫び、囁き、うめき、語り等を示すcueを検査対象とする。

連続する動作は必ず別の`ACTION`行へ分け、`ACTION 1`を最初、以後を番号順に実行する。同時動作だけは同じ`ACTION`行へ記述する。反応を原因より前へ置いてはならない。Pythonはこの順序を変えず、複数ACTIONを縮小Markdownへ出す際に先頭へ`最初に、`、中間へ`次に、`、末尾へ`最後に、`を決定論的に付ける。これによりバレット順を単なる重要度ではなくH3へ渡す明示的な時間順として固定する。

## 6. H3向け計画規則

- カメラ運動は「種別 + 意味のある移動量 + 速度 + 被写体との関係」で記述する。
- 微小な見え方の変化に新Shotを増やさず、カメラ運動として記述する。
- Cutは新しい人物、空間、状態、視点又は時間情報を提示するときだけ使う。
- 動作はScene時間内で実行可能にし、同一人物へ同時に矛盾する姿勢を与えない。
- 人体の一貫性、手足の本数、顔、衣装及び左右関係を維持する。
- `voiced`では顔と口元を必要に応じて見せるが、すべてのSceneを正面クローズアップにしない。
- `silent`では歌唱、口パク、台詞、掛け声又は人声を計画しない。違反時の扱いは`vocal_guard`に従う。
- `voiced`ではSource Vocalに合わせた口元と表情の視覚演技を許可するが、固定音響にない別の発声を追加しない。
- 歌詞があるSceneは一行を可視応答のアンカーとして選ぶ。制約に反しない具体的な身体動詞は人物動作を第一候補とし、例えばfall/downは支持を失う下方移動、reachは対象へ腕や身体を伸ばす動作として表す。物体動作、空間的比喩の順にフォールバックする。
- 共通の画材、背景運動、照明、感情又はカメラ運動だけで、実行可能な歌詞由来の人物動作を置き換えない。
- 歌詞は画面上の文字、字幕又は逐語的な発話として新規生成しない。
- `CL Japanese to JSON`が最終的に英訳と6セクション化を行うため、出力本文は自然で明確な日本語にする。

## 7. 決定論的レンダリング

出力はPlanningBriefの3ブロックを先頭へ置き、TimelineのScene順を変えずに再構成する。各Sceneの直前へ`// シーン N`を置き、Sunoセクション及び歌詞コメントを保持する。LLM計画を`## ショット`へ変換し、その後に固定リップシンク行と固定`## 音響`を出力する。

生成後は既存`lex_japanese_markdown()`で構文検査し、Scene数、秒数、継続、歌詞、ソース範囲、リップシンク及び音響が入力と完全一致することを再検証する。

## 8. エラー

次は停止エラーである。

- PlanningBriefの未知又は重複ディレクティブ
- Timelineの欠落、重複、非連続又は範囲不一致
- voiced/silentと固定リップシンク・発声指定の矛盾
- LLM応答の行プロトコル不正、Scene ID不正、Shot時刻不正、ACTION連番不正、未知カメラ種別、`required_camera_sequence`との不一致又はH3可視本文内の番号付きScene・Shot参照
- retry上限後も残る未解決Scene
- stall中断を含むLLM要求失敗が個別retry上限まで解消しない場合
- 完全一致する別Scene計画がretry上限まで解消しない場合
- 循環割当でないプロファイルにおいて、完全一致する`AUX_VISUAL`記述がretry上限まで解消しない場合
- `camera_guard=strict`の矛盾がretry上限まで解消しない場合
- `vocal_guard=strict`の矛盾がretry上限まで解消しない場合
- 参照プレースホルダの破損又は未知トークン
- 最終縮小Markdownの構文又は固定要素の差異

診断出力を有効にした場合、失敗を投げる前に全チャット要求と行指向の生応答、各応答から回収したScene、未解決Scene及び最後の部分計画を保存する。行プロトコルとして解析できない応答も、その文字列を失わない。

## 9. 非対象

- Whisper文字起こし又はVADそのもの
- 音声、動画又は画像の生成
- H3 Full-Reference 6セクションの直接生成
- Contex-Loop Plan JSONの直接生成
- 歌詞タイミング、Scene長又は音響許可のLLMによる変更

## 10. PlanningBrief責務分離と映像多様性

人間が書く縮小Markdownを増やさず、Pythonは既存3ディレクティブを次のJSONへ変換する。

```json
{
  "planning_brief": {
    "subject_identity": ["..."],
    "retention_constraints": ["..."],
    "global_visual_direction": ["..."]
  }
}
```

`subject_identity`と`retention_constraints`は人物及び参照の不変条件である。`global_visual_direction`は画材、画風、世界、照明、連続性及び禁止事項であり、Sceneイベントの候補一覧ではない。LLMは媒体を「出来事がどう見えるか」へ使用し、歌詞から「何がどう変化するか」を別に計画する。

歌詞の解釈には、方向と距離、収束と分岐、蓄積と侵食、圧力と解放、分解と再構成、隠蔽と露出、規模又は奥行きの変化等の媒体非依存な関係を使用できる。これらは内部の推論カテゴリであり、対象Sceneの歌詞に適合するものだけを選び、カテゴリ名や特定の物体を画面へ文字として表示しない。

Scene要求は直近4 Sceneのcamera typeと`AUX_VISUAL` kindを`recent_scene_patterns`として持つ。過去本文を再送せず、同一カメラの機械的反復をLLMへ認識させる。CAMERAは人物の周回を既定にせず、そのSceneの主要な人物動作、空間軌道、環境変化、奥行き又は衝撃を追跡・提示する。`arc`以外のcamera descriptionへ周回、回り込み又は背後通過を書いた場合は`camera_guard`で報告する。

Pythonは全Scene間及び同一Scene内で、Unicode NFKC正規化と空白単一化後の`AUX_VISUAL.description`完全一致を検出する。正確に一個を循環割当する軽量プロファイルでは、構造的に有効なScene全体を破棄せず、重複した一行だけを`clmv-auxiliary-visual-repair-line-v1`で最大2回再生成する。修復要求は現在Sceneの歌詞アンカー、構図、ACTION、環境、必須kind、媒体非依存な変化操作及び禁止する完全一致文を持つ。成功時は同じShot位置の一行だけを置換し、Shot数と他フィールドを維持する。二回とも失敗した場合はWARNINGを記録して元の有効Sceneを採用し、全Scene再試行ループへ入らない。その他のプロファイルでは後のSceneだけを未解決にし、従来の`auxiliary_visual_repair`を渡して再試行する。kind変更又は言い換えだけでは修復とみなさない。近似意味判定は行わず、同じモチーフを異なる展開・結果で再利用する余地を残す。
