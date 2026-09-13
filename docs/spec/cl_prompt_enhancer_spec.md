# CL Prompt Enhancer（GGUF）仕様

## 1. 目的

`CL Prompt Enhancer (GGUF)`は、`CL Prompt Merger (Reduced Markdown)`等が出力したグローバル縮小Markdownへ、選択式の画風、背景密度、人物動作及びカメラワークを適用するComfyUIノードである。

本ノードは完成MarkdownをLLMへ自由生成させない。Pythonが入力構造、Subject、保持分析、Commonの順序及び最終組み立てを所有し、LLMは次の二つだけを担当する。

- source側Commonバレットを`keep`、`style`、`background`又は`anchor`へ分類する。
- 選択した背景密度の範囲内で、既存事実を補足する日本語Common文を生成する。

画風、人物動作及びカメラワークの文は外部プロファイルから決定的に挿入する。別入力`user_prompt`はLLMへ変更対象として渡さず、source側の拡張後に既存Prompt Mergerで機械的に統合する。これにより、ユーザーが明示したSubject、保持条件、世界設定及び禁止事項を保護しながら、プリセットだけを切り替えられるようにする。

## 2. ノード契約

| 項目 | 値 |
|---|---|
| ノード型名 | `CLPromptEnhancerGGUF` |
| 表示名 | `CL Prompt Enhancer (GGUF)` |
| カテゴリ | `MiniMax H3/Prompt Tools` |
| 関数 | `enhance_prompt` |
| 出力ノード | False |

### 2.1 入力

| 入力 | 型 | 既定値 | 用途 |
|---|---|---:|---|
| `source_markdown` | STRING socket | - | 拡張対象。通常はPrompt Mergerの`merged_markdown` |
| `model_name` | COMBO | 検出先頭 | テキストGGUF |
| `chat_format` | COMBO | `auto` | `auto`、`qwen`又は`gemma` |
| `style_profile` | COMBO | `passthrough` | 外部画風プロファイル |
| `background_detail` | COMBO | `passthrough` | 外部背景密度プロファイル |
| `max_tokens` | INT | `2048` | 1回の最大生成token数 |
| `temperature` | FLOAT | `0.1` | llama.cpp sampling temperature |
| `top_p` | FLOAT | `0.9` | llama.cpp top-p |
| `repetition_penalty` | FLOAT | `1.05` | llama.cpp repetition penalty |
| `gpu_layers` | INT | `-1` | GPUへ配置するlayer数 |
| `n_batch` | INT | `256` | llama.cpp batch size |
| `n_ctx` | INT | `0` | context size。`0`はモデル既定 |
| `flash_attn` | BOOLEAN | `True` | Flash Attention |
| `kv_cache_type` | COMBO | `q8_0` | `q8_0`又は`f16` |
| `op_offload` | BOOLEAN | `True` | 演算offload |
| `keep_model_loaded` | BOOLEAN | `False` | 実行後もモデルを保持するか |
| `seed` | INT | `1` | 初回seed |
| `retry_max` | INT | `2` | 初回以外の最大再試行数 |

任意入力は次のとおりである。

| 入力 | 型 | 既定値 | 用途 |
|---|---|---:|---|
| `user_prompt` | STRING socket | 空 | LLMによる書換え対象外のユーザー指示 |
| `additional_instruction` | STRING | 空 | 背景補足の文脈ヒント。結果へそのまま複写しない |
| `model_name_override` | STRING socket | 空 | 非空なら`model_name`を上書き |
| `style_profile_override` | STRING socket | 空 | 非空なら`style_profile`を上書き |
| `background_detail_override` | STRING socket | 空 | 非空なら`background_detail`を上書き |
| `save_debug_output` | BOOLEAN | `False` | ComfyUI output以下へ診断bundleを保存 |
| `semantic_guard` | BOOLEAN | `False` | 実験的な情景の意味監査。8Bで誤検出があるため既定OFF。原文anchor保持は常時有効 |
| `motion_profile` | COMBO | `passthrough` | 外部人物動作プロファイル |
| `camera_profile` | COMBO | `passthrough` | 外部カメラワークプロファイル |
| `motion_profile_override` | STRING socket | 空 | 非空なら`motion_profile`を上書き |
| `camera_profile_override` | STRING socket | 空 | 非空なら`camera_profile`を上書き |

五つのoverrideは前後空白を除去した非空文字列だけを採用する。未知のモデル又はプロファイルは暗黙に先頭項目へ置換せず停止する。

各overrideは`connected_combo_source`入力メタデータで、順に`model_name`、`style_profile`、`background_detail`、`motion_profile`及び`camera_profile`を列挙元として公開する。`CL Connected Combo`を接続すると、サブグラフ境界を含む配線から対応する候補を自動取得できる。Enhancer自身のCOMBO及びoverride優先順位は変更しない。

`motion_profile`及び`camera_profile`は既存WFのwidget位置を崩さないため任意入力の末尾へ追加する。未保存又は旧WFから欠落している場合は`passthrough`として扱う。

### 2.2 出力

| 出力 | 型 | 内容 |
|---|---|---|
| `enhanced_markdown` | STRING | 検証済みグローバル縮小Markdown |
| `enhancement_report` | STRING | 分類、追加、除外、retry等を含むUTF-8 JSON |
| `status` | STRING | 実効プロファイルと処理件数の短い状態文 |

正常終了時だけ、ComfyUIコンソールへ`[cl_prompt_enhancer] success: ...`をANSIシアン色で出力する。

## 3. 入力文法の境界

`source_markdown`と`user_prompt`が扱える見出しは次の三つだけであり、すべて省略可能である。

```markdown
# サブジェクト
* ...

# 保持分析
* ...

# 共通プロンプト
* ...
```

Scene、Shot、音響又は未知のディレクティブを含む入力は停止エラーとする。既存コア構文のコメントは保持する。構文検証と最終ユーザー統合には`node_prompt_merger`の確定済みパーサ及びマージ処理を再利用する。

source側Commonバレットには出現順で`C001`、`C002`の一時IDを割り当てる。このIDはLLMとの分類protocol専用で、最終Markdownには出力しない。

## 4. ユーザー指示の保護

`user_prompt`はsourceと異なるprovenanceを持つ。保護契約は次のとおりである。

1. LLM要求には参考文脈として含めてよいが、分類対象IDを付けない。
2. LLM応答からSubject、Picture、保持分析又はユーザーCommonを生成、削除若しくは置換しない。
3. source側を拡張した後、`merge_reduced_markdown(enhanced_source, user_prompt)`で統合する。
4. user側Commonはsource側Commonより前へ置く。
5. user側Subject及び保持分析はPrompt Mergerの既存ルールで同一Subjectへ統合する。
6. 統合後、user側の意味本文が一つでも欠落した場合は停止エラーとする。
7. user側Commonから`night`、`day`、`dawn`又は`dusk`のいずれか一つだけを明示的に検出できた場合、その時間帯を自動背景に対する権威値とする。複数の時間帯が同時に現れる場合は、時間遷移又はユーザー自身の矛盾を一意に判定できないため権威値を設定しない。
8. 権威値と明白に異なるsource側Commonは、人物、参照タグ、カメラ、動作又は音響を含まない背景専用行に限って決定論的に除外する。複合行を部分的に書き換えたり、保護された人物指示を削除したりしてはならない。
9. LLMが生成した背景補足に権威値と異なる時間帯が残った場合、その補足行だけを決定論的に除外してWARNINGとreportへ記録する。ユーザー行自体は変更しない。

したがって保護は意味本文の完全保持を保証する。Subject又は保持分析の見出し、selector及び関係表記は、既存Prompt Mergerの正規化により統合表示へ変わり得るため、入力全体のbyte一致は保証しない。

`background_detail=reduce`を含む全プロファイルはsource側の自動背景だけを除外対象にする。別入力`user_prompt`に書かれた背景、時間帯、天候、照明及び禁止事項を削減してはならない。例えばuser側が夜間を指定し、Vision等の自動sourceが太陽、青空又は日中を記述した場合、純粋な自動背景行と矛盾する生成背景行を除外し、夜間指定を最終Common先頭へ残す。

## 5. 処理順序

```text
source_markdown ──> 構文検証・Common採番 ──> LLM分類/背景差分 ──> source Common再構成 ┐
                                                                                  ├─> Prompt Merger ──> enhanced_markdown
user_prompt ──────> 独立検証・変更禁止 ───────────────────────────────────────────┘
```

処理は次の順序で固定する。

1. 入力型、NUL、数値範囲及びoverrideを検証する。
2. 実効画風及び背景プロファイルを外部manifestから読み込む。
3. sourceとuserを個別にグローバル縮小Markdownとして検証する。
4. user Commonから一意な時間帯権威値を抽出し、矛盾するsource背景専用行を決定論的除外候補とする。
5. 必要な場合だけLLMへsource Common分類と背景差分を要求する。時間帯権威値がある場合は`authoritative_environment.time_of_day`として構造化して渡す。
6. `style`と分類されたsource Commonを、画風変更時だけ除外する。
7. `background`と分類されたsource Commonを、背景変更時だけ除外する。`anchor`はユーザーと矛盾しない物理的情景の元文を固定要素バレットへコピーする。時間帯変更を含む複合背景文では物体自体を新BACKGROUNDへ引き継ぐ。
8. LLM生成背景から時間帯権威値と矛盾する行を除外する。
9. 選択画風、人物動作及びカメラワークの固定directive、固定要素バレット、次に受理したLLM生成背景文をsource Common先頭へ追加する。semantic_guard有効時は、この候補と元Common・ユーザー権威を照合してから採用する。
10. Common本文をUnicode NFKC、空白除去及び末尾句点除去で正規化し、完全一致する行だけを機械的に一件へ縮約する。user Commonを最優先とし、user本文は削除しない。
11. userを既存Prompt Mergerで最後に統合する。
12. 最終Markdownを再検証し、user意味本文の存在を確認する。

一文に画風と人物、背景と普遍的な人物動作等が混在する場合、LLMは`keep`を返さなければならない。分類を安全側へ倒し、複合指示の一部だけを暗黙に破棄しない。ただし、入力画像で一時的に観測された静止ポーズ、人物配置、画角、Shotサイズ及び視点はSubjectの同一性でも普遍的動作でもないため、背景変更時に置換できる`background`として扱う。純粋な画風だけを禁止する文は`style`、純粋な背景条件だけを禁止する文は`background`であり、禁止表現であるという理由だけで旧画風又は旧背景を残さない。`画風は...とする`、`作画は...とする`、`舞台は...とする`、`背景は...とする`、`時間帯は...とする`、`天候は...とする`、`照明は...とする`及び`構図は...とする`の単一責務文を、小型モデル向けの意味アンカーとしてシステムプロンプトへ明記する。

LLMが返す`BACKGROUND`は環境専用であり、人物、キャラクター、被写体、Subject、身体、ポーズ、身振り、人物動作、リップシンク、音声、カメラ、Shotサイズ、視点、フレーミング、画面内配置又は構図を含めてはならない。Pythonもこれらの混入を停止エラーとして検出し、許可された回数内でLLMへ当該行の再生成を要求する。これによりVisionの静止画構図を背景拡張が再注入し、後段Plannerの人物動作を固定することを防ぐ。

## 6. 画風プロファイル

画風は`node_prompt_enhancer/prompts/styles/<profile_id>/profile.json`から自動検出する。初期同梱IDは次のとおりである。

- `passthrough`
- `anime_2020s`
- `anime_2010s`
- `anime_2000s`
- `anime_1990s`
- `anime_1980s`
- `cinematic_live_action`
- `photographic_live_action`
- `rough_sketch`
- `aggressive_sketch`
- `watercolor`
- `illustration`
- `masterpiece`

各manifestのschemaは次である。

```json
{
  "schema_version": 1,
  "profile_id": "anime_2020s",
  "display_name": "2020年代アニメ",
  "description": "説明",
  "ui_order": 10,
  "directives": ["共通プロンプトへ追加する一文。"],
  "system_instruction": "LLM分類時にだけ加える制約"
}
```

`directives`はMarkdown記号を含まない一行の日本語Common本文である。画風の実体はLLMに再生成させず、この配列をそのままバレット化する。`passthrough`だけは空配列を要求する。

`anime_2020s`から`anime_1980s`までの全アニメ年代プロファイルは、キャラクターの動作を明確で大きなキーポーズ、ポーズ・トゥ・ポーズ、二コマ又は三コマ打ち及び限定的な中割りによる手描きリミテッドアニメーションとして記述する。望まない媒体・動作の長い禁止リストの代わりに、各年代の線・色面・影と、実現したい作画単位及び動作の進行を肯定形で指定する。これはプロンプト表現の改善方針であり、H3の否定文が常に逆効果になることを仕様上の前提にはしない。

アニメ年代別スタイル、motionの`limited_anime`及び`mv_anime_emotional`は、次の4行を同一の本文で持つ。単体選択時にも必要な条件を維持し、併用時は既存の完全一致によるCommon重複除去で一度だけ追加する。

1. 明確なキーポーズと二コマ／三コマ打ちによる手描きリミテッドアニメーション。
2. キーポーズの短い保持から次の動作への進行と、カメラ移動中も維持する人物・髪・衣装・身体付属物の作画タイミング。
3. Source Vocalリップシンクが指定されたSceneに限り、身体のコマ打ちとは独立した唇・顎の動きと音素・休止への口形同期。
4. 地上を歩く又は走るSceneに限り、足裏の接地・踏み出し・重心移動・蹴り出し・着地と、接地中の足の位置を保った胴体の移動。

具体的な本文はmanifestを正とし、Pythonへ動作語彙や否定語の置換処理を追加しない。人物作画のタイミングは映画的なカメラ移動と分けて扱い、歩行条件を飛行など別の動作へ一律に適用しない。

新しい画風は新規ディレクトリとmanifestを追加するだけでUIへ現れる。Pythonの条件分岐追加を必要としない。

## 7. 人物動作プロファイル

人物動作は`node_prompt_enhancer/prompts/motions/<profile_id>/profile.json`から自動検出する。初期同梱IDは次のとおりである。

- `passthrough`
- `subtle`
- `natural`
- `dynamic`
- `music_video`
- `mv_anime_emotional`
- `limited_anime`

`subtle`は小さな意図的動作、`natural`は接地と自然な重心移動、`dynamic`は大きな全身動作、`music_video`は歌詞・楽曲強度・Source Vocalリップシンク、`mv_anime_emotional`は歌詞へ反応する手描きアニメの全身演技と追従運動、`limited_anime`はキーポーズとコマ打ちを担当する。個別のScene動作を固定せず、後段Plannerが歌詞と尺に合わせて選択できる動作文法をCommonへ与える。

`mv_anime_emotional`は、歌詞・楽曲強度に合う演技を候補から選び、予備動作、主動作、反動及び姿勢の立て直しへ展開する。身体付属物がある場合は付け根の接続と胴体への追従を記述し、周囲の情景変化は人物演技の補助として位置づける。特定のキャラクター、耳・尾の種別、衣装又は舞台を固定しない。

manifestは画風プロファイルと同じ`schema_version`、`profile_id`、`display_name`、`description`、`ui_order`、`directives`及び`system_instruction`を持つ。`passthrough`だけは空の`directives`を要求する。新規ディレクトリとmanifestの追加だけでUI候補へ現れる。

## 8. カメラワークプロファイル

カメラワークは`node_prompt_enhancer/prompts/cameras/<profile_id>/profile.json`から自動検出する。初期同梱IDは次のとおりである。

- `passthrough`
- `stable`
- `cinematic`
- `dynamic`
- `orbit_subject`
- `mv_anime_emotional`
- `music_video`

`stable`は読みやすい抑制された撮影、`cinematic`は奥行きと視差、`dynamic`は大きな軌道変化、`orbit_subject`は被写体周囲の半円アーク、`mv_anime_emotional`は人物の感情と全身演技を追跡する映画的MV撮影、`music_video`は楽曲構造に応じた撮影強度をCommonへ追加する。プロファイルは個別Sceneのcamera enumを直接生成せず、後段Plannerへ撮影の選択肢と変化要件を伝える。

cameraの`mv_anime_emotional`は、一つのShotに主となる移動を一つ選び、Scene間で視点と軌道を使い分ける。広いアーク移動、開始視点・軌道・終了視点及び強い前景視差を保つ。顔が見える区間の表情・口元と、側面・後方で見せる身体演技を区別し、全角度から顔が同時に見えることを要求しない。外観の連続性は再登場時にも引き継ぐ具体的な特徴として肯定形で記述する。

manifest schemaと拡張規則は人物動作プロファイルと同じである。

## 9. Common重複除去

機械的な重複除去は常時有効で、次の範囲だけを扱う。

- user Commonと同一のsource Commonはsource側だけを除去する。
- 残存source又はuser Commonと同一のプロファイル行は追加しない。
- 複数プロファイル又は生成背景が同一行を追加した場合は最初の一件だけを採用する。
- 大文字小文字、表記又は意味が異なる行を類似度だけで削除しない。
- Subject及び保持分析はこの処理の対象にしない。

削除件数と本文はreportの`duplicate_common_lines_removed`及び`duplicate_common_lines`へ記録する。意味的に近い行のLLM統合、要約及び矛盾解決は、誤ってユーザー制約を弱める可能性があるため本段階では行わない。

プロファイルの肯定形への改訂は、source又はuserに既にある禁止文の自動削除・書き換えを意味しない。旧プロファイルの出力を入力へ貼り付けた場合、その旧文は完全一致しない限り残る。改訂版を検証するときは古い出力の再投入を避け、ユーザー自身が確定した目・眉・配色などの条件は別途見直す。

manifest本文の変更は`enhancer_prompts_fingerprint()`及びノードの`IS_CHANGED`へ反映する。既に生成されたMarkdown・Plan・動画は書き換えない。変更が実行環境へ配置された後のエンハンサ再実行から改訂文を生成し、後段Planner・コンパイラもその出力から再実行する。作業リポジトリと実行環境が別コピーの場合は、実行中の検証を終えてから改訂manifestを実行環境へ配置する。

## 10. 背景密度プロファイル

背景密度は`node_prompt_enhancer/prompts/backgrounds/<profile_id>/profile.json`から自動検出する。初期同梱IDは次のとおりである。

| ID | 表示 | 生成行数 |
|---|---|---:|
| `passthrough` | パススルー | 0 |
| `reduce` | 削減 | 1 |
| `low` | 少 | 1～2 |
| `medium` | 中 | 2～4 |
| `high` | 高 | 4～6 |
| `ultra` | 極高 | 6～8 |

manifest schemaは次である。

```json
{
  "schema_version": 1,
  "profile_id": "medium",
  "display_name": "中",
  "description": "説明",
  "ui_order": 30,
  "minimum_lines": 2,
  "maximum_lines": 4,
  "system_instruction": "背景生成時にだけ加える制約"
}
```

生成行数は`0 <= minimum_lines <= maximum_lines <= 12`とする。`passthrough`は両方0、それ以外は最低1行を要求する。生成背景は入力の場所、時間、天候、色、光源、連続性及び禁止事項を保持し、新しい人物、物語上の出来事、台詞又は可読文字を導入してはならない。LLM応答から人物、ポーズ、構図、カメラ又は発声を含む`BACKGROUND`を除外した結果、環境だけを記述した行が一行以上残る場合は、最低行数を下回っても安全な部分結果として採用し、WARNINGへ記録する。安全な背景行が一行も残らない場合は応答を不合格として再試行する。その後、ユーザー時間帯との矛盾行を安全側で除外した結果が最低行数を下回っても、密度よりユーザー権威を優先して処理を継続し、除外数をWARNINGとreportへ記録する。

## 11. LLM protocol

LLM入力は内部JSONであり、sourceのSubject、保持分析、採番済みCommon、変更禁止のuser prompt、実効プロファイル、追加ヒント、任意の`authoritative_environment.time_of_day`及び前回検証エラーを構造化して渡す。これは入力理解を安定させるための内部形式であり、LLMへJSON出力を要求しない。

出力は次のTAB区切りprotocolだけを受理する。システムプロンプトでは、固定IDと固定classを対応付けた完成例を提示しない。小型モデルが例のclass配列を実入力へコピーすることを防ぎ、各source文の意味から分類させる。

```text
ENHANCEMENT_V1
SOURCE	C001	keep
SOURCE	C002	style
SOURCE	C003	background
BACKGROUND	背景を補足する日本語の一文
END_ENHANCEMENT
```

- source Commonごとに、同じ順序で正確に一件の`SOURCE`が必要である。
- classは`keep`、`style`、`background`、`anchor`だけである。

固定要素の保全、追加監査プロトコル及び最大2回の意味修復は[意味・情景保全仕様](prompt_semantic_preservation_spec.md)で定義する。監査は外部システムプロンプトに従う同一ロード済みLLMが行うため、誤検出と見逃しの可能性は残る。
- `BACKGROUND`件数は選択背景プロファイルの範囲内でなければならない。
- Markdown、JSON、コードフェンス、説明、直接話法、`<Subject N>`、`<Picture N>`又は内部保護tokenを応答へ含めてはならない。
- 正規区切りはU+0009の実TABである。小型モデルが区切り記号を文字列`<TAB>`又は`\t`として出した場合だけ、Pythonが実TABへ正規化してWARNINGを記録する。
- `BACKGROUND`の余分なTAB fieldは自然文の一部として読点で結合し、WARNINGを記録する。
- 先頭、全`SOURCE` ID、class、全`BACKGROUND`本文及び背景行数が正常で、固定終端`END_ENHANCEMENT`だけが欠落した場合は、Pythonが終端一行だけを追加してWARNINGを記録する。途中に終端がある、末尾レコードが壊れている、ID又は行数が不足する等の不完全応答は補完しない。
- `BACKGROUND`へ人物、ポーズ、構図、カメラ又は発声が混入した場合、その行を除外しても選択プロファイルの最低背景行数を満たすときだけ、当該行を破棄してWARNINGを記録する。最低行数を下回る場合は元の検証エラーとして再試行し、Pythonが代替背景を創作しない。
- 欠落ID、余分なID、重複ID、未知class及び規定外行は再試行対象である。

参照タグはLLM要求内で一時tokenへ保護する。LLMは新しい参照タグを導入できず、最終構造へタグを復元する処理もPythonが所有する。

## 12. パススルーとモデルライフサイクル

次の場合はGGUFを解決又はロードしない。

- 画風と背景がともに`passthrough`。人物動作又はカメラだけが有効な場合も、それらは決定的に挿入できるためGGUFをロードしない。
- 背景が`passthrough`、画風だけが有効、かつsourceにCommonバレットがない。

前者でも`user_prompt`があれば決定論的なPrompt Mergerだけを実行する。`keep_model_loaded=False`では成功又は失敗にかかわらずfinallyでモデルを解放する。これを既定とし、後段のMiniMax H3がComfyUI管理外GGUFのVRAMを引き継がないようにする。

## 13. 再試行、停止及び割込み

初回を含む最大要求数は`retry_max + 1`であり、無限再試行を行わない。各再試行では検証エラーを次の要求へ含め、seedを一つ進める。

llama.cppのstreaming応答は10秒ごとにheartbeatを出す。最初のchunkが90秒以内に到着しない場合、又は一度出力された後60秒間新しいchunkがない場合はstallとして停止する。ComfyUI interruptはchunk境界とheartbeat監視中に反映する。

`finish_reason=length`、空応答、protocol不正及び背景行数不正は検証失敗とする。規定回数で解決しない場合は最後の理由を含む`PromptEnhancerError`で停止し、部分的なMarkdownを正常出力しない。

## 14. デバッグ出力

`save_debug_output=True`では`ComfyUI/output/cl_prompt_enhancer_debug/`へ、入力、実効設定、各要求、保護済みLLM入力、生応答、validation結果、最終Markdown、report又は例外を保存する。ユーザーのSubject、保持情報及び世界設定を含むため、共有前に内容を確認する。

## 15. 推奨接続

```text
Vision等の自動Brief ─┐
                      ├─> CL Prompt Merger.merged_markdown ─> CL Prompt Enhancer.source_markdown
基礎PlanningBrief ────┘                                      │
                                                             ├─> enhanced_markdown ─> CL MV Prompt Planner.planning_markdown
ユーザー最終指示 ─────────────────────────────────────────────> user_prompt
CL Connected Combo ──────────────────────────────────────────> 各profile override
```

自動生成されたBriefは`source_markdown`へ、変更を避けたい最終的な人間の指示は`user_prompt`へ接続する。既にPrompt Mergerへ含めた同じユーザー断片を再度`user_prompt`へ接続すると重複するため、provenanceを二重投入しない。

## 16. テスト要件

- 全profileの検出順、schema及び未知IDエラー。
- 選択profileだけをsystem promptへ合成すること。
- protocolのID完全性、class、背景行数、参照タグ及び余分なTAB field。
- source Commonだけが狭く除外され、コメントと非対象文が残ること。
- Scene等のグローバル範囲外入力を拒否すること。
- 完全パススルー時にGGUFをロードしないこと。
- `reduce`でもuser背景を保持すること。
- userが一意に夜間を指定した場合、LLMがsource日中行を`keep`にしても最終出力から除外すること。
- user時間帯と矛盾するLLM生成背景だけを除外し、互換する背景とuser本文を維持すること。
- 時間帯を複数指定したuser promptを一つの権威値へ誤って縮約しないこと。
- retryが有限で、エラー文と新seedを次要求へ渡すこと。
- 内容が完全で固定終端だけを欠いた応答を一度で復元し、不完全レコード又は不足IDを復元しないこと。
- 人物又は構図を含む`BACKGROUND`を除外しても最低行数を満たす場合だけ正常行を保持して継続すること。
- 外部model、style及びbackground overrideが実効値へ反映されること。
- ノード登録、入出力順、既定値、シアン成功ログ及びモデル解放。
- Motion及びCamera profileの検出順、外部manifest、既定passthrough及びSTRING override。
- Motion又はCameraだけが有効な場合にGGUFをロードせず、決定的なCommonを追加すること。
- user、source及び各追加profile間の正規化完全一致だけを除去し、user本文を維持すること。

## 17. 非対象

- Scene、Shot、歌詞、camera enum、音響又はH3 JSONの生成。
- Subject identity又は保持分析の創作。
- user prompt内の矛盾解決、要約又は自動削除。一意な時間帯の抽出はuser本文を変更せず、自動source及び生成背景との優先順位付けにだけ使用する。
- 画像解析。
- モデル又は`llama-cpp-python`の自動導入。
- 自由形式のMarkdown全文生成。
- 類似度又はLLMによる意味的なCommon統合。
