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
- PlanningBriefの構造化`hard_requirements`
- Sunoセクションの`section_sources`及びScene別`lyric_groups`
- Scene別active motifの選択
- 直前に検証済みのSceneから作る`previous_scene_tail`
- 完全一致する重複Sceneの検出

LLMが計画するもの:

- 楽曲全体の視覚的な弧と反復モチーフ
- Sceneごとの視覚的意図
- 構図、人物動作、環境変化及び連続性
- 公式H3カメラ種別、移動量、速度及び自然言語の運動説明
- 歌詞を逐語表示せず映像へ変換する比喩表現

LLMは固定タイムライン、歌詞、発声許可、参照番号又はディレクティブを追加、削除、移動、変更しない。

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

`// シーン N`は1から連続する。ソース範囲も0から連続し、範囲長はScene秒数と一致しなければならない。既存Shot本文は計画対象として置換するが、リップシンクと音響は固定要素として再利用する。

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
* <Subject 1>として描く、髪のない中性的な人物。

# 保持分析
* <Subject 1> 完全に保持: 顔、頭部、体格、衣装及び手指を維持する。

# 共通プロンプト
* 背景は透明な水へインクが拡散する抽象空間。
* 荒い多数の線による立体的なラフスケッチとして描く。
* カメラは正面固定を避け、人物の周囲を多様な角度から撮影する。
```

認識するトップレベルディレクティブは`# サブジェクト`、`# 保持分析`、`# 共通プロンプト`だけである。順序はこの通り、各1回までとする。Scene、Shot、音響又は自由本文はエラーにする。サブジェクトは必須、他は任意である。

## 4. 中間表現

プランナーは次の3層を分離する。

1. `PlanningBrief`: ユーザーのSubject、Retention、Commonを原文のまま保持する。
2. `TimelineDocument`: Scene、時間範囲、VAD状態、Sunoセクション、歌詞、固定リップシンク及び音響を保持する。
3. `MVPlan`: song bibleとScene別のShot計画を保持する。

参照タグはLLM送信前に型付きプレースホルダへ置換し、応答検証後にPythonが原文へ復元する。LLMが未知のプレースホルダを生成した場合又は必要な参照を破損した場合は、そのSceneだけを未解決とする。

## 5. 階層型LLM計画

### 5.1 Song bible

最初の1回で、全歌詞セクションとユーザー指示から次を固定フィールドの行指向形式で生成する。LLMへJSON構文を生成させない。

- 全体の視覚的な弧
- セクション別の色、形、照明及び抽象モチーフ
- カメラワークの展開方針

固定条件はPythonが解析した`PlanningBrief`だけを正本とし、Song Bibleへ再生成させない。Song Bible v2、確定条件と創作情報の分離、構造化JSON入力、active motif、直前Sceneの最終状態、重複Scene排除及びカメラ意味ガードは`docs/cl_mv_prompt_planner_song_bible_spec.md`を正本とする。

### 5.2 Sceneバッチ

Sceneを`scenes_per_batch`件ずつ計画する。各Sceneは安定した`scene_id`を持つ。要求データには`requested_scene_ids`と`requested_scene_count`を明記する。LLM応答は`SCENE`から`END_SCENE`までの独立した行指向レコードとする。正常なSceneは即時確定し、不正又は欠落したSceneだけを新しいseedで再送する。`retry_max`はバッチ全体ではなく各Sceneの個別上限とし、先に修復するSceneが後続Sceneの再試行回数を消費してはならない。全Sceneを再送してはならない。再試行時は前回の構文又は検証エラーを`retry_feedback`として明示し、同じ誤りをseedだけ変えて反復させない。

各batchは独立したチャット要求とし、暗黙の会話履歴へ依存しない。継続性に必要な情報はSong Bible、Sceneに該当する`active_section_motifs`及び、そのSceneより前で最も近い検証済みSceneの最終状態を表す`previous_scene_tail`として毎回明示する。同一応答内の後続Sceneは直前のSceneレコードを継続情報として使用する。部分再試行時はSceneごとに`previous_scene_tail`を再計算する。完全重複を検出したbatchは一件ずつの再送へ切り替える。既知の重複元と直前Sceneは本文を再掲せずcompact fingerprintとして`avoid_duplicate_plans`へ渡し、`duplicate_repair`で未使用のShot/ACTION件数を推奨して構造差を促す。

batch応答から正常Sceneを一件も回収できない場合も、次回から番号順の一件再送へ切り替える。Scene固有のエラーだけを再送し、camera schema値の不正には具体的な`camera_protocol_repair`を添える。

行指向応答はJSONの引用符、エスケープ、配列及び波括弧を使用しない。PythonはSceneレコードを個別に抽出し、固定フィールド順、必須フィールド、Scene ID、Shot時刻、ACTION連番、参照及びカメラ値を検証する。壊れたレコードを推測補修してはならない。

### 5.3 行指向プロトコル

Song bibleは次の順序とする。`<TAB>`は実際のタブ文字を表す。

```text
SONG_BIBLE
VISUAL_ARC<TAB>全体の視覚的な弧
CAMERA_STRATEGY<TAB>カメラ展開方針
SECTION_MOTIF<TAB>[Chorus]<TAB>セクションモチーフ
END_SONG_BIBLE
```

Sceneは次の順序とする。

```text
SCENE<TAB>1
SCENE_INTENT<TAB>映像意図
SHOT<TAB>0
COMPOSITION<TAB>構図
ACTION<TAB>1<TAB>最初の動作
ACTION<TAB>2<TAB>次の動作
ENVIRONMENT<TAB>背景と照明
CAMERA<TAB>arc<TAB>medium<TAB>moderate<TAB>被写体との関係を含むカメラ記述
END_SHOT
END_SCENE
```

各Shotは次を持つ。

- `start_ms`: Scene相対時刻。最初は0、以後は厳密に増加し、Scene長未満
- `composition`: 画角、人物・物体の位置及び視線
- 1～8個の`ACTION`: 1から始まる連番。実行時系列と優先順位の昇順に並べる
- `environment`: 背景、照明及び状態変化
- `camera.type`: 公式カメラ種別
- `camera.amplitude`: 移動時は`small`、`medium`又は`large`、`static`だけは`none`
- `camera.speed`: 移動時は`slow`、`moderate`又は`fast`、`static`だけは`none`
- `camera.description`: 被写体との関係が分かる日本語の具体的運動

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
- 歌詞は映像着想として解釈し、画面上の文字、字幕又は逐語的な発話を新規生成しない。
- `CL Japanese to JSON`が最終的に英訳と6セクション化を行うため、出力本文は自然で明確な日本語にする。

## 7. 決定論的レンダリング

出力はPlanningBriefの3ブロックを先頭へ置き、TimelineのScene順を変えずに再構成する。各Sceneの直前へ`// シーン N`を置き、Sunoセクション及び歌詞コメントを保持する。LLM計画を`## ショット`へ変換し、その後に固定リップシンク行と固定`## 音響`を出力する。

生成後は既存`lex_japanese_markdown()`で構文検査し、Scene数、秒数、継続、歌詞、ソース範囲、リップシンク及び音響が入力と完全一致することを再検証する。

## 8. エラー

次は停止エラーである。

- PlanningBriefの未知又は重複ディレクティブ
- Timelineの欠落、重複、非連続又は範囲不一致
- voiced/silentと固定リップシンク・発声指定の矛盾
- LLM応答の行プロトコル不正、Scene ID不正、Shot時刻不正、ACTION連番不正又は未知カメラ種別
- retry上限後も残る未解決Scene
- 完全一致する別Scene計画がretry上限まで解消しない場合
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
