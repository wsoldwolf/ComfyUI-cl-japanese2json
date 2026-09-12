# CL MV Prompt Planner (GGUF) ComfyUIノード仕様

本書は`clmv-line-v5`ノード契約を記述する。Song Bible、役割別PlanningBrief、直前Scene最終状態、Scene及び付加映像の完全重複検出、`camera_guard`及び`vocal_guard`の詳細は`docs/cl_mv_prompt_planner_song_bible_spec.md`、視覚拡張プロファイルは`docs/cl_mv_prompt_visual_profiles_spec.md`を正本とする。

## 1. ノード契約

- 登録名: `CLMVPromptPlannerGGUF`
- 表示名: `CL MV Prompt Planner (GGUF)`
- カテゴリ: `MiniMax H3/Prompt Tools`
- 実装: `node_mv_prompt_planner/node.py`
- 関数: `plan_mv_prompt`

## 2. 入力

| 名前 | 型 | 既定値 | 用途 |
|---|---|---:|---|
| `prompt_segments` | STRING socket | - | `CL Vocal to Prompt Segments.prompt_text`、又はその出力を`CL Scene Limiter`で切り出した連続部分 |
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
| `visual_enrichment_profile` | COMBO（optional） | `performance_only` | 人物演技のみ、8B向け軽量歌詞映像又は大型モデル向け歌詞映像を選ぶ。候補はプロファイルディレクトリから発見する |
| `model_name_override` | STRING socket（optional） | 未接続 | 空でない外部文字列で`model_name`のCOMBO選択を上書きする |
| `visual_enrichment_profile_override` | STRING socket（optional） | 未接続 | 空でない外部文字列で`visual_enrichment_profile`のCOMBO選択を上書きする |

2個のoverrideは接続専用の`forceInput`ソケットとする。前後空白を除いた外部文字列が空でなければ対応するCOMBOより優先し、未接続又は空文字列ならCOMBOへフォールバックする。`model_name_override`はモデルCOMBOに表示される、モデルルートからの相対検出IDと一致させる。外部STRINGノードとの接続性のため、前後空白、先頭の`/`または`\\`及びWindows形式の`\\`区切りは、ファイルシステムパスとして解釈せず相対検出IDの`/`区切りへ正規化する。正規化後に存在しない値なら停止エラーとする。`visual_enrichment_profile_override`はインストール済みプロファイルIDとの完全一致を要求し、未知値なら停止エラーとする。実効値は8B警告判定、モデルロード、プロファイルロード、デバッグmanifest、status及びComfyUIキャッシュ判定で一貫して使用する。

既存のCOMBOをSTRINGソケットへ置換してはならない。overrideソケットはoptional入力の末尾へ追加し、既存ワークフローの`widgets_values`位置と手動選択UIを維持する。

`chat_format=auto`では`llama-cpp-python`へchat formatを明示せずGGUF metadata/templateへ委ねる。Qwen系、Gemma系及び将来の互換モデルを同じモデル選択欄で切り替えられる。明示値はメタデータ不備のGGUF用である。

`model_name`に独立した`8B`トークンを検出し、`visual_enrichment_profile=lyric_visuals_full`を同時に選択している場合だけ、Plannerは実行開始時に上下を`#`で囲んだ3行の黄色い重大WARNINGを記録する。8B向け`lyric_visuals_light_8b`及び補助映像を要求しない`performance_only`は正常な対応設定なので警告しない。警告は`lyric_visuals_light_8b`への変更又は14B以上の使用を案内するだけで、ユーザーの選択を自動変更しない。`18B`、`27B`及び`80B`の一部を`8B`として誤検出してはならない。

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

Song bibleとScene計画は別リクエストにする。さらに選択プロファイルの`lyric_action_preplan=true`では、各Scene batchの直前に歌詞動作だけを分解する小さい独立リクエストを実行する。要求上限はプロファイルの`lyric_action_scenes_per_request`に従い、8B向けは常に一Sceneずつ送る。preplanにはSubject定義と保持分析だけを渡し、共通プロンプの画材、背景及び演出語彙は後段まで隔離する。LLM応答はJSONではなく、タブ区切りの固定フィールドからなる行指向プロトコルとする。Scene計画と歌詞動作preplanは正常Sceneを保持して未解決Sceneだけを再送する。要求対象IDと件数を毎回明示し、再試行には前回の構文・検証エラーも含める。`retry_max=0`は再試行なし。各再試行のseedは`seed + attempt`を32-bit非ゼロ範囲へ正規化する。

全LLM要求は開始から10秒ごとに、要求ラベル、経過秒、受信済みstream chunk数、最後のchunkからの経過秒及び進捗callback実行状態をINFOへ記録する。このハートビートはchunk到着に依存しない。最初のchunkが120秒間到着しない場合、又は出力開始後45秒間新しいchunkが到着しない場合はstalled inferenceとして現在の要求を中断し、通常の個別retry予算へ移す。複数Sceneの本計画がstallした場合は、次回から一Sceneずつの要求へ自動縮退する。これらのtimeoutはモデル応答内容の検証とは独立し、黙ったまま無期限に待機しないための死活監視である。

Song Bibleの正規形は`END_SONG_BIBLE`で終了する。小型モデルがこの終端行だけを省略した場合、その他の必須フィールド、順序、参照及び全セクションモチーフが完全に正常な応答に限りPythonが終端を補完する。補完時は黄色のWARNINGを一度記録し、Song Bible全体の再推論を省く。未知行又は実データ欠落は従来どおり再試行する。

Scene応答は`SCENE`、`SCENE_INTENT`、`LYRIC_RESPONSE`、`SHOT`、`COMPOSITION`、1～8個の連番`ACTION`、選択プロファイルが許可する`AUX_VISUAL`、`ENVIRONMENT`、`CAMERA`、`END_SHOT`及び`END_SCENE`を固定順で持つ。各Sceneレコードを独立して厳密検証する。複数ACTIONの番号順は実行時系列であり、Pythonが同じ順序の別バレットへ変換して時間接続語を付与する。正規形では各ShotのACTIONを1から振り直す。LLMが複数ShotをScene全体の連続ACTION番号にした場合も、番号と物理行順が連続して一意なら安全に受理し、Shot別順序へ正規化する。欠番、重複及び逆行は該当Sceneの再試行対象とする。Scene入力の`camera_choreography_contract`は、移動カメラへ開始視点、通過軌道、終了視点及び前景・背景視差を要求する。長尺歌詞Sceneの`lyric_visuals_light_8b`及び`lyric_visuals_full`では、Scene番号から循環選択した`required_camera_sequence`も渡し、実Shot数分のtype、amplitude及びspeedを厳密検証する。広いarcは概ね半周して人物の複数面を見せ、同じ正面寄りを反復しない。

正規形の`AUX_VISUAL`はShot内の最後の`ACTION`直後かつ`ENVIRONMENT`前に置く。小型モデルが完全な`AUX_VISUAL`行を`END_SHOT`直後へ置いた場合だけ、Pythonはそれを直前Shotへ決定論的に戻して警告付きで受理する。同一Scene内でkindと本文が完全一致する重複行は最初の1件へ畳む。未知kind、異なる複数行又は内容欠損は補完しない。長尺SceneのShot数説明は選択プロファイルと同じ範囲を使用し、8B向けの正確に2 Shotという契約と2～3 Shotという一般説明を同時に渡してはならない。

ComfyUIのキャッシュ判定には、選択GGUFの実パス・サイズ・更新時刻、`prompts/core/`の4個のplannerプロトコルコア及び全視覚拡張プロファイルファイルのサイズ・更新時刻・SHA-256を含める。モデル、共通コア又はプロファイル固有ポリシーを変更した場合は、入力値が同じでも再実行する。合成system promptには選択したプロファイルの`song_bible.txt`又は`scene_plan.txt`だけを連結し、他プロファイルの創作ポリシーを混在させない。`lyric_action_system_prompt.txt`は有効化した小型モデル用の意味分解、`auxiliary_visual_repair_system_prompt.txt`は正確に一個を循環割当するプロファイルで重複した一行だけを修復する独立コアである。

`save_debug_output=True`では、実行ごとのディレクトリを`ComfyUI/output/cl_mv_prompt_planner_debug/`へ作る。入力`prompt_segments.md`と`planning_markdown.md`、設定manifest、各推論のsystem prompt、送信要求JSON、行指向の生LLM応答、Pythonが解析した検証済みデータ及び検証metadataを保存する。`final_state.json`には、最後に正常確定したSong BibleとScene、実行中batch/attempt、回収済みScene ID、未解決Scene ID及び最終エラーを保存する。成功時は`planned_markdown.md`と`planner.json`、失敗時は`error.txt`も保存する。入力や歌詞を含むため共有前に内容を確認する。

Plannerは会話履歴を累積しない。Song Bibleと各Scene batchはそれぞれsystem promptとuser JSONから成る独立リクエストである。Scene間の必要情報はSong Bible、Scene別`active_section_motifs`及び、直前に検証済みのSceneから作る構造化`previous_scene_tail`として明示的に渡す。ただし逐語複写を防ぐため、直前Sceneの`AUX_VISUAL`原文はtailへ含めない。同一応答内の後続Sceneには直前レコードを継続情報として扱わせる。従ってdebug bundleの各eventが、その推論時点の完全なチャット入力と出力である。

PythonからLLMへの入力だけを浅いJSONとし、`PlanningBrief`は`planning_brief.subject_identity`、`retention_constraints`及び`global_visual_direction`の配列へ分解する。歌詞はSong Bible用の`section_sources`とScene用の1始まり`lyric_lines`へ一意にまとめる。LLM出力はJSONではなく、Song Bible `clmv-song-bible-line-v4`及びScene `clmv-scene-line-v4`の行指向形式である。歌詞があるSceneは`LYRIC_RESPONSE`で一行と応答方式を宣言し、具体的な身体動詞を実行可能なら人物の直接動作として優先する。`lyric_response_contract.semantic_fidelity`は主体、動作、具体的対象及び可視結果の維持を要求する。`inscription_fallback`は筆記・彫刻の物理動作を維持しながら、結果を長さ、向き、曲率及び間隔が不規則な孤立した非言語的な短い傷又は溝へ限定する。`camera_choreography_contract`はScene長に応じた空間軌道、視差、追従対象及び長尺SceneのShot方針を定義する。歌詞がないSceneは`0`と`instrumental_continuity`を宣言する。選択プロファイルとScene別`auxiliary_visual_contract`もJSON入力へ明示し、LLMに暗黙推定させない。

プランナーは生成Markdownの`# 共通プロンプト`へ画面内文字の抑制規則を決定論的に追加する。PlanningBriefはユーザー入力を正本として保持し、`文字らしい`、`文字のような`又は`文字風`という部分文字列だけを理由に拒否、置換又は警告しない。これにより禁止文等の文脈を単純な語句検出で誤判定しない。歌詞そのものは入力データとして保持するが、Blueprint又はSceneのLLM生成H3可視フィールドに`文字`、`名前`、`単語`、`字形`、`字幕`、`ロゴ`等の画面内文字を誘発する語が返された場合、そのSceneだけを未解決として再試行する。ユーザーがPlanningBriefで擬似文字を明示的に許可した場合、その原文と自動追加される画面内文字抑制規則が意味的に競合し、H3が不定な描画を行う可能性はあるが、プランナーはユーザー文を隠れて改変しない。

`performance_only`は補助映像を禁止する。`lyric_visuals_light_8b`はPythonがScene番号から必須kindを循環決定し、各Sceneへ正確に一つだけ要求するとともに、`clmv-lyric-action-line-v1`で歌詞動作を先に固定する。preplanが参照凡例に存在する正確な`<Subject N>`を生タグで返した場合は、既知Subjectに限りPythonが保護トークンへ戻してWARNING付きで受理する。未知Subject、他種の生参照及び本Scene計画の生参照は正規化しない。`lyric_visuals_full`は許可kindから一～三個をモデルに選択させ、歌詞動作preplanを使用しない。個数又はkind違反は該当Sceneだけを部分再試行する。

完全一致するScene計画は、Unicode NFKC、空白単一化及び前後空白除去後の構図、全ACTION、環境、カメラを署名化して検出する。先のSceneを保持し、後のSceneだけを部分再試行する。重複検出後は未解決Sceneを一件ずつ時間順に再送し、直前の確定Sceneを`previous_scene_tail`へ反映する。既知の重複元と直前Sceneは、本文ではなくSHA-256、Shot数、先頭ACTION数及びcamera typeのcompact fingerprintとして`avoid_duplicate_plans`へ渡す。`duplicate_repair`でfingerprint群にないShot/ACTION件数を推奨し、低temperatureで同一応答が続く場合は再試行ごとに推奨形状を変更する。推奨件数と異なるだけでは拒否せず、完全署名が重複元と異なれば受理する。意味的に似ているだけのSceneや一部フィールドの一致は除外しない。

正確に一個の`AUX_VISUAL`を循環割当するプロファイルで付加映像文だけが過去Sceneと完全一致した場合は、元の検証済みSceneを保持し、`clmv-auxiliary-visual-repair-line-v1`による一行修復を最大2回行う。修復成功時は元のShot内の一行だけを置換する。修復不能時はWARNINGを出して元のSceneを採用し、構造を崩しやすい全Scene再試行を反復しない。各限定修復要求と応答も通常のdebug eventとして保存する。

一つのbatchで正常Sceneが0件の場合は、次の試行から一件ずつの再送へ自動縮退する。各要求には対象Scene自身の診断だけを含める。`CAMERA`へリテラル`type`、`amplitude`又は`speed`を出した場合は、`required_camera_sequence`のないSceneに限り`camera_protocol_repair`で実在する有効値を具体的に指定する。binding系列があるSceneでは、同じcamera typeを全Shotへ要求するrepairが系列と矛盾するため使用せず、Scene入力と累積retry reminderにShot別の正確なtype、amplitude及びspeedを再掲する。

`camera_guard=warn`は計画本文を変更せず、疑わしいScene、Shot、camera type及び記述を黄色のWARNINGへ出し、`planner_json`とdebug metadataへ保存する。`strict`は該当Sceneだけを部分再試行する。一般ACTIONの「押す」「引く」からカメラ意図を推定せず、カメラ本文を自動修正しない。

`vocal_guard=warn`はTimelineと矛盾する発声cueを黄色のWARNINGへ出して計画を維持する。`strict`は該当Sceneだけを部分再試行する。`voiced` Sceneの`歌う`、`歌い`、`歌唱`及び`口パク`は既存Source Vocalへの視覚同期なのでguard対象外とする。`silent` Sceneの歌唱、又は`voiced` Sceneであっても追加の叫び、囁き、うめき若しくは語りを示すcueはguard対象とする。

全Sceneの検証とMarkdown/JSON生成が完了した場合、出力を返す直前にstatus要約をANSIシアン色の`[cl_mv_prompt_planner] success:`ログとして記録する。リクエスト中、再試行中又は一部Sceneだけ確定した状態を成功として記録してはならない。

## 5. ワークフロー接続

```text
CL Vocal to Prompt Segments.prompt_text
                 │
                 ▼
CL Scene Limiter（任意。省略時はVocal出力を直接接続）
                 │
                 ▼
CL MV Prompt Planner (GGUF).prompt_segments
Planning Markdown ──────────────► planning_markdown
CL String Combo（任意）─────────► model_name_override
CL String Combo（任意）─────────► visual_enrichment_profile_override
                 │
                 ▼
CL Japanese to JSON (GGUF).plain_text
                 │
                 ▼
MiniMax H3 Contex-Loop Plan.plan_json_input
```

Vocalノードの`segments_json`は診断用のまま維持し、プランナーの必須入力にはしない。必要な楽曲セクション、歌詞及び時間情報はコメントを含む`prompt_text`から決定論的に取得する。

Scene Limiterを挟む場合、Plannerは部分入力の先頭が`// シーン 1`でなくても、元番号から連続するScene IDとソース音声上の絶対範囲を受理する。元Scene 2以降の先頭に残る`継続`も許可する。番号の欠落・逆行、ソース範囲の隙間・重複及び範囲長とScene秒数の不一致は、部分入力でも停止エラーとする。
