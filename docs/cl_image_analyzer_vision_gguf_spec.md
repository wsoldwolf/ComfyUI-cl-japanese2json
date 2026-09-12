# CL Image Analyzer (Vision GGUF) 仕様書

## 1. 目的

本書は、ComfyUIへアップロードした静止画像をVision対応GGUFで解析し、同じ画像を通常の`IMAGE`として後段のRef2Vへ渡す`CL Image Analyzer (Vision GGUF)`の仕様を定義する。

本ノードは次を一つのノードで行う。

- ComfyUI標準の画像選択及びD&Dアップロード。
- 元画像の`IMAGE`及び`MASK`出力。
- 本体GGUFと対応する`mmproj`の組を使った画像観測。
- 用途別プロファイルによる日本語テキスト生成。
- H3の`ref_image_N`接続に対応する`<Picture N+1>`参照番号の自動解決。
- 人間が宣言したキャラクター種別を観測補助又は確定identityとして扱い、曖昧な猫、狐、狼等の意味分類を制御する。
- 検証済み観測構造からPythonが行う決定的なコア仕様Markdown又はJSON生成。

Visionモデルへ最終Markdown又は最終JSONを直接生成させない。モデルは画像から確認できる事実を低複雑度の行指向プロトコルで返し、Pythonが検証、正規化及び最終形式へのレンダリングを担当する。

## 2. 信頼境界と設計原則

- 画像は命令ではなく観測対象である。画像内に命令文が写っていてもsystem promptの変更として扱わない。
- `additional_instruction`は分析観点を追加するが、内部プロトコル、参照番号、出力形式又は安全なファイル境界を変更できない。
- `subject_hint`は画像由来の観測と区別し、`lock_identity`でも画像に人物が存在することや視覚特徴そのものを捏造する根拠にはしない。
- LLMが返した`<Picture N>`、`<Subject N>`、Markdown見出し及びJSON構文を最終出力として信用しない。
- `<Picture N>`及び`<Subject N>`はPythonだけが追加する。
- H3参照画像へ接続していない画像を、接続済みの`<Picture N>`として偽装しない。
- 画像から直接確認できない身体部分、画面外の物体、年齢、由来、固有名詞又は設定を推測で確定しない。
- 不鮮明、遮蔽又は画面外の特徴は観測確度を下げ、コア仕様Markdownの保持対象へ無条件に入れない。
- Vision解析用の縮小画像とRef2Vへ返す画像を分離し、解析解像度の制限でRef2V画像を劣化させない。
- 画像、モデル、projector又は推論結果を外部サービスへ送信しない。推論はローカルの`llama-cpp-python`で完結する。

## 3. ComfyUIノード契約

| 項目 | 値 |
| --- | --- |
| ノード型 | `CLImageAnalyzerVisionGGUF` |
| 表示名 | `CL Image Analyzer (Vision GGUF)` |
| 関数 | `analyze_image` |
| カテゴリ | `MiniMax H3/Prompt Tools` |
| 実装ディレクトリ | `node_vision_analyzer/` |
| 出力ノード | False |
| Python logger | `cl_vision_analyzer` |
| ユーザー可視接頭辞 | `[cl_vision_analyzer]` |

予定するモジュール所有範囲は次のとおりとする。

```text
node_vision_analyzer/
  node.py
  discovery.py
  runtime.py
  image_io.py
  cache.py
  debug_output.py
  graph_binding.py
  structures.py
  validation.py
  renderer.py
  errors.py
  prompts/
    core/
      observation_system_prompt.txt
web/
  cl_image_analyzer_vision.js
```

Vision固有のprojector管理、画像メッセージ生成及び観測プロトコルは`node_vision_analyzer/`が所有する。2個以上のノードが実際に同じ機能を必要とするまでは`common/`へ昇格しない。GGUFルート列挙、割込み、モデル解放及び成功ログは既存`common/`基盤を再利用してよい。

## 4. 入力

### 4.1 必須入力

| 名前 | 型 | 既定値 | 用途 |
| --- | --- | --- | --- |
| `image` | IMAGE upload COMBO | 発見先頭 | `ComfyUI/input`以下の画像。`image_upload: true`でD&Dに対応する |
| `model_name` | COMBO | 発見先頭の有効なVisionモデル | 本体GGUF。対応`mmproj`は同じモデルディレクトリから自動解決する |
| `analysis_profile` | COMBO | `general` | 最終テキスト形式。候補は`general`、`subject_only`、`scene_only`、`planner_brief`、`structured_json` |
| `additional_instruction` | STRING multiline | 空 | 眉、瞳、衣装、物体、構図等、特に確認したい観点。空を許可する |
| `cache_mode` | COMBO | `reuse` | 観測キャッシュ制御。候補は`reuse`、`refresh`、`disabled` |
| `picture_reference_mode` | COMBO | `auto_h3` | Picture参照の決定方法。候補は`auto_h3`、`manual`、`none` |
| `picture_index` | INT | 1 | `manual`時にMarkdownへPythonが挿入する`<Picture N>`番号。1～9 |
| `subject_index` | INT | 1 | Subject系MarkdownへPythonが挿入する`<Subject N>`番号。1～4 |
| `analysis_max_edge` | INT | 1024 | Vision推論用コピーの長辺上限。256～2048、64刻み。小さい画像を拡大しない |
| `max_tokens` | INT | 1024 | 1回の最大生成token。32～4096 |
| `temperature` | FLOAT | 0.1 | 観測出力の多様性。0.1～1.0 |
| `top_p` | FLOAT | 0.9 | nucleus sampling。0.0～1.0 |
| `repetition_penalty` | FLOAT | 1.05 | 反復抑制。0.5～2.0 |
| `gpu_layers` | INT | -1 | llama.cpp GPU layer。-1～1000 |
| `n_batch` | INT | 256 | llama.cpp batch。32～4096 |
| `n_ctx` | INT | 4096 | 画像tokenと応答を含むcontext長。512～32768 |
| `flash_attn` | BOOLEAN | True | Flash Attention |
| `kv_cache_type` | COMBO | `q8_0` | `q8_0`又は`f16` |
| `op_offload` | BOOLEAN | True | operation offload |
| `keep_model_loaded` | BOOLEAN | False | 実行後に本体モデル及びprojectorを保持するか |
| `seed` | INT | 1 | 初回seed。1～4294967295 |
| `retry_max` | INT | 2 | 初回以後の観測プロトコル検証再試行回数。0～10 |

`picture_reference_mode`、`picture_index`及び`subject_index`は出力参照番号だけを決め、画像内容又はVisionモデル入力を変えない。`general`、`scene_only`及び`structured_json`では不要な番号を最終本文へ出力しない。

`additional_instruction`へコア仕様Markdownを記述する必要はない。例えば`眉毛、目の形、狐耳及び尻尾の本数を詳しく確認する`という通常の日本語を許可する。

`analysis_profile`は検証済みの正規観測を最終文字列へ変換するPythonレンダラーだけを切り替え、Vision LLMの観測schemaを変更しない。同じ画像、モデル及び分析指示については全プロファイルが一個の正規観測を共有する。従ってプロファイル、Picture参照mode、H3接続先、`picture_index`又は`subject_index`だけを変更した場合は、Vision推論を再実行せずキャッシュ済み観測から即座に再レンダリングできる。

`cache_mode`の意味は次のとおりとする。

| 値 | 読込 | 書込 | 意味 |
| --- | --- | --- | --- |
| `reuse` | 有効 | 有効 | 一致するメモリ又は永続観測キャッシュを再利用する。既定 |
| `refresh` | 無効 | 有効 | 既存cacheを無視して一度推論し、正常結果で同じkeyを更新する |
| `disabled` | 無効 | 無効 | 実行ごとに推論し、ノード固有cacheを読み書きしない |

`refresh`を選択したまま再度Queueした場合は毎回再推論する。一回の更新後に再利用したい場合は`reuse`へ戻す。

### 4.2 オプション入力

| 名前 | 型 | 既定値 | 用途 |
| --- | --- | --- | --- |
| `save_debug_output` | BOOLEAN | False | 入力指紋、設定、system prompt、生LLM応答、検証結果及び最終観測JSONを保存する |
| `subject_hint` | STRING multiline | 空 | 人間が確定又は補助する人物・キャラクター設定。通常の自然言語を使用する |
| `hint_mode` | COMBO | `lock_identity` | `observe_only`、`assist`又は`lock_identity` |
| `hint_conflict` | COMBO | `warn` | 明瞭な画像証拠との矛盾時に`warn`で継続し、`strict`で停止する |
| `image_override` | IMAGE | 未接続 | 接続時に内部D&D画像を完全に置き換える外部ComfyUI画像 |

`subject_hint`は空を許可し、空の場合は`hint_mode`にかかわらず`observe_only`相当とする。改行と連続空白は一個の空白へ正規化する。NUL、TAB、コア参照タグ、Markdown見出し、バレット又はSceneコメントを含む入力は、プロンプト命令と自然言語データの境界を保つため停止エラーにする。

旧版ノードの保存済み`IMAGEUPLOAD`値`image`が、UI項目追加後の位置ずれによって`subject_hint`へ復元される場合がある。フロントエンドは`widgets_values_named`に`subject_hint`が存在しない旧schemaを検出して新規ヒント項目を既定値へ戻す。バックエンドも完全一致する`image`を旧アップロードsentinelとして空のヒントへ移行し、警告を残す。これを人物identityとして出力してはならない。

UI値へ説明用ラベルを含む`subject_hint: 人物設定`又は`subject_hint：人物設定`が貼り付けられた場合、バックエンドは先頭ラベルだけを除去し、警告を残して`人物設定`を自然言語データとして使用する。

`hint_mode`の意味は次のとおりとする。

| 値 | Vision入力 | 最終identity | 用途 |
| --- | --- | --- | --- |
| `observe_only` | ヒントを送らない | Vision観測 | 従来どおり画像だけを分析する |
| `assist` | 意味分類の補助情報として送る | Vision観測 | 猫、狐、狼等を補助するが確定はしない |
| `lock_identity` | 人間の宣言データとして送る | Pythonが正規化済みヒントを使用 | 人物種別、役割及び明示した設定を確実に保持する。既定 |

`lock_identity`でも、Visionが返す顔、髪、目、耳の形、衣装、尾の本数等の可視特徴は独立した観測として残す。PythonはVisionの`PRIMARY_SUBJECT`文字列だけをidentityとして採用せず、正規化済みヒントで置き換え、可視特徴を後続文として結合する。画像に主要Subject自体が存在しない場合は、ヒントだけから人物を捏造せず`subject_only`及び`planner_brief`を停止する。

Visionはヒントと画像の関係を`not_used`、`consistent`、`ambiguous`又は`conflict`として返す。`ambiguous`は警告して継続する。`conflict`は`hint_conflict=warn`ならヒントの採用を維持して警告し、`strict`なら最終テキストを出力せず停止する。`conflict`は明瞭な画像証拠が直接反する場合だけ使用し、単に画像から種別を判定できない場合は`ambiguous`とする。

ノード初版では`mmproj_name`をUIへ公開しない。自動解決結果は`status`及びログへ必ず記録する。将来、同一モデルに対するprojector選択が必要になった場合だけ詳細入力として追加する。

### 4.3 隠し入力

H3接続先を実行時promptから解決するため、次のComfyUI隠し入力を宣言する。

```python
"hidden": {
    "prompt": "PROMPT",
    "unique_id": "UNIQUE_ID",
}
```

`prompt`は接続関係の検出だけに使用し、Vision LLMへ渡さず、通常ログ、観測cache又は最終結果へ保存しない。

### 4.4 Picture参照mode

| 値 | 動作 |
| --- | --- |
| `auto_h3` | 現在の`image`出力からH3の`ref_images.ref_image_N`へ至る実行prompt上の直接接続を検出し、`<Picture N+1>`を使用する。認識対象へ接続していなければPicture参照を生成しない。既定 |
| `manual` | 下流接続に関係なくUIの`picture_index`から`<Picture N>`を使用する |
| `none` | 下流接続及び`picture_index`に関係なくPicture参照を生成しない |

`auto_h3`はノード自身の`IMAGE`出力indexをPython定数で参照し、実行prompt内の全ノード入力から`[unique_id, image_output_index]`に一致する参照を走査する。ComfyUIのschema変換差を吸収するため、ドット区切り入力`ref_images.ref_image_N`と、`ref_images`group内の`ref_image_N`を同じ正規入力pathへ変換してから判定する。対象入力名は少なくとも次を認識する。

```text
ref_images.ref_image_0 -> <Picture 1>
ref_images.ref_image_1 -> <Picture 2>
...
ref_images.ref_image_8 -> <Picture 9>
```

入力名の判定は末尾`ref_image_0`～`ref_image_8`だけの曖昧な一致にせず、H3の参照画像groupである`ref_images.ref_image_N`を正規形とする。既知のH3ノード型と入力別名は`graph_binding.py`のデータ定義として管理し、将来のH3更新で追加可能にする。現在確認済みの対象には`MiniMaxH3ReferenceToVideo`を含める。このノードのComfyUI schemaは`ref_images`をAutogrow、prefixを`ref_image_`、indexを0～8として定義している。

同じPicture番号へ複数のH3ノードが接続されている場合は同一解決として受理する。異なるPicture番号へ分岐している場合、同じ画像を複数番号として暗黙に定義せず、接続先ノードIDと入力名を列挙した明確な停止エラーにする。この場合は配線を一意にするか`manual`を選択する。

Preview Image、Save Image及びその他の通常IMAGE入力への分岐は無視する。実行promptに含まれない無効ノード、バイパスされた経路又は別Queueの接続は根拠にしない。RerouteがComfyUIの実行promptで除去され、最終参照が本ノードを直接指す場合は検出できる。画像変換ノードを間に挟み、本ノードの出力参照がH3入力から直接確認できない経路は初版の自動追跡対象外とする。

この走査は依存関係を追加せず、実行promptのmetadataを読むだけであるため、H3から本ノードへの逆向きデータ接続又は循環依存を作らない。ただし出力対象の選択によってH3ノードが実行promptから除外された場合、`auto_h3`は未接続として扱う。

解決したPicture参照を本文へ挿入するのは`subject_only`及び`planner_brief`だけとする。`general`、`scene_only`及び`structured_json`では接続を`status`へ表示してよいが、本文schemaへPictureタグを追加しない。

### 4.5 読み取り専用Pictureラベル

`web/cl_image_analyzer_vision.js`は`CLImageAnalyzerVisionGGUF`へ、書き換え不能かつworkflowへシリアライズしない`resolved_picture_reference`表示を追加する。通常のSTRING widgetとしてユーザー編集を禁止する方法ではなく、文字入力を受け付けないcanvas custom widget又は無操作のstatus widgetとして実装する。

表示例を次に固定する。

```text
Picture参照: <Picture 1>
Picture参照: なし
Picture参照: <Picture 3> (manual)
Picture参照: 無効
Picture参照: 競合 (<Picture 1>, <Picture 2>)
```

- `auto_h3`で一意な接続を検出した場合はシアン系の文字色で解決タグを表示する。
- `auto_h3`でH3接続がない場合は中立色で`なし`と表示する。
- `manual`では`picture_index`の値と`(manual)`を表示する。
- `none`では`無効`と表示する。
- 異なる番号への分岐は赤系で`競合`と表示する。

フロントエンドは本ノードの`IMAGE`出力linkから`app.graph.links`の接続先を調べ、対象ノードの`type`、target slot及び`inputs[target_slot].name`を使って表示番号を解決する。ドット区切りAutogrow名`ref_images.ref_image_N`を正規入力名とする。

表示更新は少なくとも`onNodeCreated`、`onConfigure`、`onAdded`及び`onConnectionsChange`後に次のtickへ予約する。`picture_reference_mode`及び`picture_index` widgetのcallback後にも更新する。既存callback及びprototype hookを上書きして失わず、元処理を呼び出してから更新する。

このラベルは利便表示であり、Pythonへ隠し値として送信せず、Picture番号の真正な入力として扱わない。Queue時は4.4節のバックエンド検出を必ず再実行する。フロントエンド表示とバックエンド結果が異なる場合は、バックエンド結果を優先し、誤ったPictureタグを出力しない。

## 5. 出力

出力順序を次で固定する。

```python
RETURN_TYPES = ("IMAGE", "MASK", "STRING", "STRING")
RETURN_NAMES = ("image", "mask", "result", "status")
```

| 名前 | 型 | 内容 |
| --- | --- | --- |
| `image` | IMAGE | 読み込んだ画像。Preview Image及びMiniMax H3 Ref2Vへ直接接続できる |
| `mask` | MASK | アルファチャンネルから作るComfyUI標準マスク。アルファがなければ全0 |
| `result` | STRING | 選択プロファイルをPythonでレンダリングした日本語テキスト又はJSON文字列 |
| `status` | STRING | 使用モデル、projector、元画像寸法、解析寸法、プロファイル、再試行数及び警告の要約 |

`image`及び`mask`はVision推論の成功後に返す。モデルロード、画像解析又は検証が失敗した場合、部分的な出力を正常結果として返さずノードを停止する。

## 6. 画像アップロードと読込

### 6.1 D&D

`image`はComfyUI標準の入力画像コンボボックスとし、`image_upload: true`を指定する。ブラウザからD&D又はファイル選択した画像は標準upload APIにより`ComfyUI/input/`へ保存される。

本ノードは任意の絶対パスを受理しない。バックエンドでは`folder_paths`のannotated filepath APIを使い、選択名が入力ディレクトリ内の実在ファイルへ解決できることを再検証する。`..`、NUL、存在しないファイル又は入力境界外への解決は停止エラーとする。

### 6.2 外部IMAGEによる上書き

`image_override`へ別の画像ローダー、生成ノード又は加工ノードの標準`IMAGE`が接続された場合、内部`image`選択値は解析、cache key、`image`出力及びdebug上の画像名に使用しない。未接続時だけ内部D&D画像を使用する。優先順位は次で固定する。

```text
接続済み image_override > 内部 image upload
```

外部入力は`[batch, height, width, channels]`の0.0～1.0有限値Tensorとし、channel数は1、3又は4を許可する。1 channelはRGBへ複製し、4 channelはRGBを`image`へ返して反転alphaを`mask`へ返す。3 channelは入力Tensorを変更せず返し、同一寸法の全0 maskを生成する。過大画像、不正shape、未知channel数、NaN、無限大又は範囲外pixelは停止エラーにする。

batch全体は通常の`IMAGE`出力へ維持するが、一回の正規観測は先頭画像だけを対象とし、複数batchの場合は`status`へ警告する。観測cache keyも実際に解析した先頭画像pixelから作る。`image_override`接続時のComfyUI `IS_CHANGED()`は上流ノードへ変更判定を委ね、本ノード自体を実行する。画像が同一なら内部の検証済み観測cacheがVision再推論を回避する。

### 6.3 デコード

- Pillowで読込み、`ImageOps.exif_transpose()`相当のEXIF方向補正を一度だけ行う。
- 元画像をRGB又はRGBAへ正規化し、ComfyUI標準の0.0～1.0浮動小数点`IMAGE`を生成する。
- アルファがある場合はComfyUI標準の反転アルファ意味で`MASK`を生成する。
- アルファがない場合は元画像寸法に一致する全0の`MASK`を生成する。
- Ref2V用`image`を再圧縮、リサイズ、クロップ又は色調補正しない。
- アニメーション画像は初版では先頭の有効フレームだけを使用し、`status`へ警告を追加する。

### 6.4 解析用コピー

Visionモデルへ渡すコピーだけを`analysis_max_edge`以下へアスペクト比を維持して縮小する。拡大は行わない。アルファは白背景へ合成し、メモリ上のPNGへ符号化して`data:image/png;base64,...`としてMTMDメッセージへ渡す。一時画像ファイルを作らない。

## 7. Vision GGUFとprojectorの発見

### 7.1 探索範囲

既存GGUFと同じ`ComfyUI/models/LLM/GGUF/`及び追加のComfyUI `LLM`モデルルートを再帰探索する。通常の本体GGUF一覧とVisionペア一覧は別に管理する。

Visionモデル候補は次を全て満たす。

1. 本体ファイルの拡張子が大文字小文字を問わず`.gguf`である。
2. 本体ファイル名に`mmproj`を含まない。
3. 同じディレクトリにファイル名が`mmproj`を含むGGUFが一個以上ある。
4. 本体及びprojectorの先頭4 byteが`GGUF`である。
5. 解決済み実パスが登録モデルルート内にある。

テキスト専用GGUFはVisionモデルコンボボックスへ表示しない。Visionモデルは通常のテキストGGUFコンボボックスへ現れてもよいが、既存ノードが画像を認識することを意味しない。

### 7.2 ディレクトリ規則

一つのVisionモデル系列を一つのサブディレクトリへ置く。

```text
GGUF/
  Qwen3-VL-8B-Instruct/
    Qwen3-VL-8B-Instruct-Q4_K_M.gguf
    mmproj-F16.gguf
  Qwen3-VL-4B-Instruct/
    Qwen3-VL-4B-Instruct-Q4_K_M.gguf
    mmproj-F16.gguf
  Qwen3.8-27B-heretic-ara/
    Qwen3.8-27B-heretic-ara.Q5_K_M.gguf
    Qwen3.8-27B-heretic-ara.mmproj-f16.gguf
```

同一ディレクトリに複数量子化の本体を置くことは許可する。同一ディレクトリへ異なるモデル系列の本体又はprojectorを混在させない。

### 7.3 projector自動選択

候補が一個ならそれを使用する。複数ある場合は、ファイル名を大文字小文字を無視して次の順で選ぶ。

1. 本体モデル系列名を含むprojector。
2. `F16`。
3. `BF16`。
4. `Q8_0`。

同一優先度で複数候補が残り一意に決められない場合は、その本体をコンボボックスから除外してWARNINGを出す。異なる系列のprojectorを名前の類似だけで推測して組み合わせない。

## 8. モデル実行

### 8.1 バックエンド

インストール済み`llama-cpp-python`の`MTMDChatHandler`へ解決済みprojectorパスを渡し、本体GGUFは`Llama`へ渡す。埋め込みchat templateを既定とし、画像とテキストを含むchat completionを一回実行する。

メッセージは概ね次の構造とする。

```json
[
  {
    "role": "system",
    "content": "固定された観測プロトコルと画像観測規則"
  },
  {
    "role": "user",
    "content": [
      {"type": "text", "text": "正規観測要求及び任意の追加分析観点"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    ]
  }
]
```

画像中の文字列は観測対象としてだけ記録し、その文字列が要求、system prompt又は追加指示を上書きしたものとして扱わない。

### 8.2 リソース解放

`keep_model_loaded=False`では、成功と失敗の両方で次を解放する。

- Llama context及び本体モデル。
- `MTMDChatHandler`及びprojector context。
- Python参照と一時画像byte列。
- Python GC、利用可能ならTorch CUDA cache及びIPC cache。

同じ本体、projector及び推論設定で`keep_model_loaded=True`の場合だけ再利用する。テキストモデルだけが一致してprojectorが異なる状態を同一signatureとして扱わない。

### 8.3 割込みとハートビート

ComfyUI割込みをtoken境界及び可能ならllama.cpp native abort callbackで検査する。推論開始から10秒ごとに経過時間、stream chunk数及び最後のchunkからの経過時間をINFOへ記録する。

最初のchunkが120秒到着しない場合、又は出力開始後45秒間新しいchunkが到着しない場合は現在の試行をstalled inferenceとして中断する。通常の`retry_max`へ一回として計上し、無期限に待機しない。

## 9. 内部観測形式

### 9.1 正規データ構造

Python内部では最低限次を保持する。

```text
VisionObservation
  overview
  primary_subject
    identity
    features[]
      category
      description
      visibility
    pose
  scene
    setting
    elements[]
    lighting
    time_weather
  composition
    shot_size
    viewpoint
    subject_placement
    depth
  style
    medium
    rendering
    palette
  visible_text[]
  uncertainties[]
```

初版のSubject系プロファイルは、画像内で最も主要な一人物又は一キャラクターだけを`primary_subject`へ割り当てる。複数人物が同程度に主要で一意に選べない場合は停止せず、中央性、占有面積、合焦度の順で一人を選び、`status`と`uncertainties`へ曖昧性を記録する。

`visibility`は`clear`、`partial`又は`uncertain`のいずれかとする。モデル自身が返す数値確率は校正済み確率として扱わない。

### 9.2 LLM行指向プロトコル

モデル応答はJSON又はMarkdownではなく、次の固定プロトコル`cl-vision-observation-line-v2`とする。

```text
OBSERVATION_V2
OVERVIEW\t日本語の概要
PRIMARY_SUBJECT\t日本語の単数名詞句
HINT_ASSESSMENT\tnot_used|consistent|ambiguous|conflict\t日本語の根拠又は空
SUBJECT_FEATURE\tface\t特徴の説明\tclear
SUBJECT_FEATURE\thair\t特徴の説明\tpartial
SUBJECT_POSE\t姿勢の説明
SCENE_SETTING\t場所と環境
SCENE_ELEMENT\t背景要素
LIGHTING\t照明
TIME_WEATHER\t時間帯と天候
COMPOSITION\tshot_size\t説明
COMPOSITION\tviewpoint\t説明
COMPOSITION\tsubject_placement\t説明
COMPOSITION\tdepth\t説明
STYLE\tmedium\t説明
STYLE\trendering\t説明
STYLE\tpalette\t説明
VISIBLE_TEXT\t画像内で確認した文字列
UNCERTAINTY\t確認できない事項
END_OBSERVATION
```

許可する`SUBJECT_FEATURE` categoryは`face`、`hair`、`eyes`、`eyebrows`、`ears`、`body`、`clothing`、`accessory`、`tail`及び`distinctive_feature`とする。同一categoryの反復を許可する。`HINT_ASSESSMENT`は常に一行とし、ヒントが無効な場合は`not_used`と空の根拠、ヒントが有効な場合はそれ以外の値と空でない根拠を要求する。

`PRIMARY_SUBJECT`へ`image`、`picture`、`photo`、`画像`又は`写真`だけを記述してはならない。`SUBJECT_FEATURE`は色、形、数、材質、模様、長さ又は状態等の具体的な視覚属性を含める。`顔が見える`、`髪が見える`、`目が見える`、`全身が見える`等、可視性しか表さない記述は検証エラーとし再試行する。瞳を観測できる場合は虹彩中心部の主色を先に記述し、赤いアイライン、睫毛、瞼の影、反射光及び周囲の衣装色と区別する。虹彩の縁だけが別色なら主色の後に縁色を記述する。

小型Visionモデルが`clear`と同じ意味で返す既知の`visible`だけは、Pythonが`clear`へ正規化して警告を残す。また、visibility前後の空白、既知categoryの不要な補助列、又は説明末尾へ連結された許可visibilityは、categoryと行末visibilityが一意に確定できる場合だけ正規の四列へ修復する。`SUBJECT_FEATURE`が`category`と具体的な説明だけの三列で、末尾visibilityだけが欠落した場合も解釈は一意であるため、説明を保持したまま保守的な`partial`を補完し警告する。同じ内容でLLM再試行を消費しない。明示された未知visibility、空の説明又は一意に分離できないその他の列崩れを推測で変換せず、検証エラーとして再試行する。

小型Visionモデルが`SUBJECT_FEATURE`を`category, visibility, description`の順で返した場合、第三列が既知visibilityで最終列が未知visibilityであることを条件に、Pythonは`category, description, visibility`へ決定論的に入れ替える。説明本文を破棄せず、一意に確定するこの列反転だけでLLMを再試行してはならない。

値は単一物理行とし、TAB、NUL、参照タグ、Markdown見出し、コードフェンス又はプロトコル終端語を含めない。フィールド順、必須フィールド及び許可レコードは正規観測schemaとして共通に検証する。未知レコード、重複単一フィールド、不正visibility、不正category又は終端欠落は検証エラーとする。

Pythonは検証済みレコードから正規データ構造を作る。`structured_json`もこの正規構造を`json.dumps(..., ensure_ascii=False, indent=2)`で直列化し、生LLM応答をJSONとして解釈しない。

全プロファイルで画像全体の同じ正規観測を生成する。`analysis_profile`はLLMへ送らず、正規観測のレンダリング時だけ参照する。これによりprofile変更で内容の観測基準が変化せず、キャッシュ再利用時と新規推論時の意味を一致させる。

## 10. 分析プロファイル

### 10.1 `general`

画像全体の概要、主要Subject、情景、構図及び画風を観測する。Pythonは固定見出しと箇条書きによる読みやすい日本語へレンダリングする。可読文字を検出した場合は観測結果として記載してよい。

例:

```markdown
## 画像概要
* 神社の鳥居前に狐耳を持つ人物が立っている。

## 主要人物
* 長い淡い金髪、金橙色の瞳及び白と深紅の衣装を持つ。

## 情景と構図
* 森林、朱色の鳥居及び奥の社殿が見える。
* 低い位置から人物を見上げる構図である。
```

### 10.2 `subject_only`

主要Subjectの外観だけを観測する。背景、照明、ポーズ、カメラ位置及び画像内文字をSubjectの恒久属性へ混入させない。

Pythonは`# サブジェクト`と`# 保持分析`だけを生成する。各セクションは正確に一バレットとし、一個のSubjectを複数バレットへ分割しない。

次の例はPicture参照が`<Picture 1>`へ解決された場合である。

```markdown
# サブジェクト
* <Picture 1>を外観参照としてのみ使用する、長い淡い金髪、金橙色の瞳、一対の狐耳、白と深紅の衣装及び一本の狐尻尾を持つ成人の狐巫女。

# 保持分析
* <Subject 1> 完全に保持: <Picture 1>由来の顔、髪、瞳、狐耳、体格、衣装、装飾及び明瞭に確認できる識別可能な特徴を維持する。
```

この出力はPlannerの`planning_markdown`用サブセットであり、Sceneを持たないため単独では`CL Japanese to JSON`の完全入力ではない。

Picture参照が未解決又は`none`の場合、Subjectと保持分析は画像から得た文章特徴だけで構成し、`<Picture N>`、`<Picture N>由来`又は画像参照を示唆する文言を挿入しない。

```markdown
# サブジェクト
* 長い淡い金髪、金橙色の瞳、一対の狐耳、白と深紅の衣装及び一本の狐尻尾を持つ成人の狐巫女。

# 保持分析
* <Subject 1> 完全に保持: 顔、髪、瞳、狐耳、体格、衣装、装飾及び識別可能な特徴を維持する。
```

### 10.3 `scene_only`

主要Subjectの識別可能な外観を除外し、場所、背景物、時間帯、天候、照明、構図、奥行き及び画風を観測する。

Pythonは`# 共通プロンプト`と一個以上のバレットを生成する。画面内文字の内容は既定で除外し、文字の存在が情景上重要な場合も`文字要素がある`という非逐語情報に限定する。

### 10.4 `planner_brief`

主要Subject、保持分析及び画像から観測した情景をPlanner入力用のコア仕様サブセットとして生成する。順序は必ず次とする。

```text
# サブジェクト
# 保持分析
# 共通プロンプト
```

SubjectとRetentionはそれぞれ正確に一バレットとする。Commonは意味単位で複数バレットへ分けてよい。

Picture参照が解決済みの場合、PythonはCommonへ次の参照分離規則を決定的に追加する。次の例は`<Picture 1>`へ解決された場合である。

```markdown
* <Picture 1>は<Subject 1>の外観だけに使用する。画像の背景、照明、構図、ポーズ及びカメラ位置を直接の情景参照として使用せず、情景は以下の文章から構成する。
```

その後へ、画像から観測した情景を文章化したCommon行を置く。これにより画像を情景として直接コピーさせず、観測した要素をユーザーが編集可能なPlanningBriefとして扱う。

Picture参照が未解決又は`none`の場合はこの参照分離行を生成しない。観測した情景の文章化は継続するが、H3へ画像参照が接続されているような表現を追加しない。

画面内で検出した字幕、看板、ロゴ又は可読文字の内容をPlannerBriefへ転記しない。必要な場合は`general`又は`structured_json`を使用する。

### 10.5 `structured_json`

正規`VisionObservation`をJSON文字列として出力する。最低限次のトップレベルkeyを固定する。

```json
{
  "schema": "cl-vision-observation-v2",
  "overview": "",
  "primary_subject": {},
  "hint_assessment": {
    "alignment": "not_used",
    "explanation": ""
  },
  "scene": {},
  "composition": {},
  "style": {},
  "visible_text": [],
  "uncertainties": [],
  "subject_hint": {
    "mode": "observe_only",
    "value": "",
    "identity_locked": false
  }
}
```

key名と型はPythonが生成するため常に有効なJSONとする。観測されなかった任意値は空文字列、空object又は空arrayとし、文字列`unknown`を乱用しない。

## 11. Pythonによるコア仕様Markdown生成

`subject_only`及び`planner_brief`のレンダラーは次を保証する。

1. Subjectは一個につき一行、一バレットである。
2. Retentionは一Subjectにつき一行、一バレットである。
3. Picture参照番号は検証済みH3接続解決又は`manual`時の`picture_index`からだけ生成し、Subject参照番号はUIの`subject_index`からだけ生成する。
4. LLMが返した参照タグ又は見出しを再利用しない。
5. `clear`な顔、髪、瞳、眉、耳、体格、衣装、装飾、尾及び識別特徴を優先してSubjectへまとめる。
6. `partial`な特徴は、見えている範囲を明記できる場合だけ採用する。
7. `uncertain`な特徴はSubject又は保持分析へ確定事項として入れず、`status`へ警告する。
8. ポーズ、表情の一時状態、照明色、影、背景色及びカメラ歪みを恒久的な身体特徴へしない。
9. `planner_brief`のセクション順を固定する。
10. レンダリング後に既存PlanningBrief構文として再検証し、不正なら出力せず停止する。
11. `scene_only`及び`planner_brief`では、Visionが`VISIBLE_TEXT`と重複する内容を誤って情景fieldへ混入させても、`「」`又は`<d>...</d>`を含む句をPythonがCommonから除外する。句は読点又はカンマ単位で除外し、同じfield内の非文字情景は保持する。除外数は`status`へWARNINGとして記録する。`general`及び`structured_json`は人間による観測確認用なので原観測を維持する。

Pythonは特徴の文書順も固定する。基本順序はidentity、face、hair、eyes、eyebrows、ears、body、clothing、accessory、tail、distinctive_featureとする。同じ入力観測に対してsampling以外のMarkdown整形結果を変化させない。

## 12. 再試行とフォールバック

初回応答が`cl-vision-observation-line-v2`として不正な場合、検証済みの正しいレコードを勝手に最終出力へ混ぜず、エラー理由と正規観測schemaを付けて全観測を再送する。`analysis_profile`は再試行要求にも送らない。ヒントが有効なのに`not_used`を返す、又は無効なのにヒント評価を返す応答も再試行対象とする。

- `retry_max=0`は再試行しない。
- 再試行は最大10回だが既定は2回とする。
- seedは`seed + attempt`を32-bit非ゼロ範囲へ正規化する。
- protocol外の前置き、thinking markup、コードフェンス及び自然文だけの応答は不正とする。
- JSON修復、Markdown修復又は自由文からの推測抽出を行わない。
- `END_OBSERVATION`だけが欠落し、その他の全必須レコードと順序が完全な場合に限りPythonが終端を補完し、一度だけWARNINGを出して受理してよい。
- retry上限到達時は最後の具体的な検証理由、本体モデル名、projector名及びprofileを含む`VisionAnalysisError`で停止する。

観測自体は成立したがSubjectが存在しない場合、`general`、`scene_only`及び`structured_json`は成功できる。`subject_only`及び`planner_brief`はSubjectセクションを捏造せず、主要Subjectを確認できないという停止エラーにする。

## 13. キャッシュ

### 13.1 二段キャッシュ

`cache_mode=reuse`では次の二段キャッシュを使用する。

1. ComfyUI標準実行キャッシュ。ノード入力と`IS_CHANGED()`が一致する場合はノード自体を実行せず、`IMAGE`、`MASK`、`result`及び`status`を再利用する。
2. ノード固有観測キャッシュ。ComfyUIがノードを再実行した場合、又はComfyUI再起動後でも、同じVision観測keyがあればLLMをロードせず正規`VisionObservation`を復元する。

二段目は画像tensorや最終profile文字列を保存せず、検証済み正規観測だけを保存する。ノード実行時には入力画像を通常どおり読み込んで`IMAGE`及び`MASK`を生成し、キャッシュ観測を選択profileでPythonレンダリングする。

### 13.2 観測cache key

Vision観測keyは少なくとも次をNUL区切りでSHA-256へ入力して生成する。

- EXIF方向補正及びRGB/RGBA正規化後の画像pixel内容hashと寸法。
- 本体GGUFの解決済みパス、サイズ及び更新時刻。
- projector GGUFの解決済みパス、サイズ及び更新時刻。
- `analysis_max_edge`。
- `additional_instruction`の完全なUTF-8内容。
- Visionへ実際に送った正規化済み`subject_hint`と有効な`hint_mode`。`observe_only`では入力されたヒント本文をkeyへ含めない。
- `max_tokens`、`temperature`、`top_p`、`repetition_penalty`、`gpu_layers`、`n_batch`、`n_ctx`、`flash_attn`、`kv_cache_type`、`op_offload`及び`seed`。
- `observation_system_prompt.txt`の内容hash。
- `cl-vision-observation-line-v2` schema version。
- 画像前処理version及び利用する`llama-cpp-python` version。

次は観測cache keyへ含めない。

- `analysis_profile`。
- `hint_conflict`。これは検証済み観測の扱いだけを変え、Vision推論を変えない。
- `picture_reference_mode`及び実行promptから解決したH3 Picture番号。
- `picture_index`及び`subject_index`。
- Python renderer version。
- `save_debug_output`。
- `keep_model_loaded`。
- `cache_mode`自身。

除外項目は画像の観測事実を変えない。変更時は同じ正規観測をPythonで再レンダリングする。renderer versionはComfyUIの`IS_CHANGED()`へ含め、最終文字列だけを再生成する。

### 13.3 永続保存

永続cacheは`folder_paths.get_user_directory()/cl_vision_analyzer_cache/v2/`以下へ保存する。ファイル名は観測keyとし、先頭2文字のサブディレクトリで分散する。

```text
ComfyUI/user/<user>/cl_vision_analyzer_cache/v2/ab/abcdef....json
```

各cache entryは次だけを持つ。

- cache schema version。
- 観測key。
- 生成日時。
- 画像内容hashと寸法。
- 本体及びprojectorのbasenameとfingerprint。
- 正規`VisionObservation`。
- 観測時の警告。

元画像、絶対パス、base64画像、生LLM応答、追加指示全文又は`subject_hint`全文をcache entryへ保存しない。`additional_instruction`及び有効な`subject_hint`はkeyのhashへ影響するが平文保存しない。観測結果自体には画像由来の個人情報又は可読文字が含まれる可能性がある。

書込は同じディレクトリの一時ファイルへUTF-8 JSONを書き、flush後に原子的renameで確定する。推論又は検証失敗時はcacheを書かない。既存の正常entryを失敗結果で削除又は上書きしない。

### 13.4 cache hitと破損

memory cacheを先に確認し、次にdisk cacheを確認する。disk entryは読込後にschema、key及び正規観測を再検証する。

- 正常hitではGGUF本体とprojectorをロードしない。
- 壊れたentry、schema不一致又は検証失敗はWARNINGを一回出し、そのentryを無視して通常推論する。
- cache破損だけを理由にノードを停止しない。
- 正常な新規観測を得た場合は同じkeyへ原子的に置換する。
- cache missとhitでPython renderer及び最終検証を同じ経路にする。

cache hit時の`status`には`cache: hit (memory)`又は`cache: hit (disk)`、新規推論には`cache: miss`、無効時には`cache: disabled`を含める。

### 13.5 ComfyUI `IS_CHANGED()`

`cache_mode=reuse`の`IS_CHANGED()`は少なくとも次を含む安定したtuple又はhashを返す。

- 入力画像の内容hash。
- 観測cache key。
- `analysis_profile`、`picture_reference_mode`、`picture_index`及び`subject_index`。
- `auto_h3`で解決したPicture番号と、対象H3ノードID及び入力名から作る接続binding hash。未接続状態も固有値として含める。
- renderer version。
- profile固有の最終検証version。

`cache_mode=refresh`又は`disabled`ではComfyUI標準cacheも推論を隠さないよう、毎Queueでchangedとなる値を返す。`refresh`成功後にUI値を自動で`reuse`へ変更しない。

画像ファイルの選択名だけでキャッシュを決めない。同名画像を置換した場合は再解析する。画像pixel、モデル、projector、system prompt、追加分析指示又は推論設定が変わった場合は、画像名が同じでも以前の観測を再利用しない。Ref2V用`image`出力だけを必要とする場合も、ComfyUIの通常実行契約上はVision解析をバイパスしない。

## 14. ログ、status及びデバッグ

正常終了時は共有`log_node_success()`を使用し、ANSIシアン色で一度だけ次に相当するログを出す。

```text
[cl_vision_analyzer] success: analyzed image with Qwen3-VL-4B-Instruct-Q4_K_M.gguf + mmproj-F16.gguf; profile=planner_brief; source=1536x2048; analysis=768x1024; retries=0
```

cache hit時も正常終了としてシアン色ログを一回出すが、`analyzed image`ではなく`reused cached observation`と明記する。

途中経過を成功として表示しない。画像pixel、base64、`additional_instruction`全文及び可読文字全文を通常ログへ出さない。

`llama-cpp-python`のMTMD前処理が`verbose=False`でも標準出力又は標準エラーへ直接書くchat template、system prompt、user prompt、tokenize、`add_text`、`add_media`及び画像encode/decode詳細は、Vision chat呼出しの前処理区間だけ抑制する。本ノード自身の開始、heartbeat、完了、検証警告、モデル解放、成功及び停止エラーは抑制しない。これにより日本語promptがWindows consoleの異なる文字コードで文字化けして表示されることと、通常ログへのprompt全文漏出を防ぐ。

`status`は少なくとも次を含む人間可読の複数行STRINGとする。

```text
self test passed
model: Qwen3-VL-4B-Instruct-Q4_K_M.gguf
projector: mmproj-F16.gguf
profile: planner_brief
picture_reference: auto_h3 -> <Picture 1> (node 136, ref_images.ref_image_0)
source: 1536x2048
analysis: 768x1024
retries: 0
cache: miss
warnings: none
```

`self test passed`は、観測プロトコル検証、正規構造生成、profileレンダリング及びprofile固有の最終検証を全て通過した場合だけ表示する。

`save_debug_output=True`では`ComfyUI/output/cl_vision_analyzer_debug/<実行ID>/`へ次を保存する。

- `manifest.json`: ファイルbasename、内容hash、寸法、モデル、projector及び推論設定。
- `system_prompt.txt`。
- `request.json`: base64画像本体を除き、画像寸法とhashだけを記録する。
- `response.txt`: 生LLM応答。
- `observation.json`: Pythonの正規観測。
- `result.txt`。
- 失敗時の`error.txt`。

cache hit時のdebug bundleにはcache key、hit階層、再検証結果及び最終出力を保存する。実行されていないLLMのsystem prompt、生応答又は推論時間を存在したように生成しない。

入力画像そのもの又はbase64をdebug bundleへ複製しない。生応答と結果には画像由来の個人情報又は可読文字が含まれる可能性があるため、共有前の確認を必要とする。

## 15. エラー条件

少なくとも次を停止エラーとする。

- 画像未選択、存在しない入力又は入力ディレクトリ境界外のパス。
- Pillowで復号できない画像、寸法0、NUL又は過大なpixel数。
- Visionモデル又は対応projectorが見つからない。
- 本体又はprojectorのGGUF headerが不正。
- projector候補を一意に解決できない。
- `MTMDChatHandler`が利用できない、又はprojectorがVisionを提供しない。
- 数値、Boolean、profile、参照番号又はモデル選択がUI契約外。
- `auto_h3`で同じ画像出力が異なるPicture番号へ分岐し、一意に解決できない。
- 推論失敗、割込み又はstall。
- 行指向観測プロトコルがretry上限内で検証できない。
- `subject_only`又は`planner_brief`で主要Subjectを確認できない。
- Python生成Markdownが既存PlanningBrief構文検証を通らない。

エラー文にはユーザーが修正可能な対象名を含め、巨大なtrace前に原因を一行で理解できるようにする。モデルが画像非対応の場合は、同じディレクトリに対応`mmproj`が必要であることを明記する。

## 16. テスト要件

### 16.1 単体テスト

- ノード登録名、表示名、カテゴリ、入力順序、既定値及び出力順序。
- `image_upload: true`とComfyUI input境界検証。
- `image_override`接続時の優先、内部画像未使用、RGB/RGBA/gray Tensor変換、batch先頭解析、Tensor異常値拒否及び内部cache再利用。
- EXIF回転、RGB/RGBA、アルファmask及び非アルファmask。
- Ref2V用出力が`analysis_max_edge`で縮小されないこと。
- 解析コピーだけがアスペクト比を維持して縮小されること。
- 再帰Visionモデル発見、テキスト専用除外及びprojector優先順位。
- 異なるモデル系列又は曖昧projectorの拒否。
- GGUF magic検証。
- 全許可レコード、必須フィールド、visibility及びcategoryの解析。
- thinking、コードフェンス、未知行、参照タグ及び終端欠落の拒否。
- 安全な終端補完条件。
- 5 profileのPythonレンダリング。
- `observe_only`、`assist`及び`lock_identity`のヒント適用、identity固定、ヒント構文拒否、曖昧警告及び矛盾時のwarn/strict。
- `structured_json`が常にparse可能でschema及び型が固定されること。
- Subject及びRetentionが一Subject一行になること。
- Picture参照番号を検証済みH3接続又は`manual`時の`picture_index`以外から生成せず、Subject参照番号を`subject_index`以外から生成しないこと。
- `ref_images.ref_image_0`～`ref_images.ref_image_8`を`<Picture 1>`～`<Picture 9>`へ正しく対応させること。
- 同一Picture番号への複数H3接続を受理し、異なるPicture番号への分岐を停止エラーにすること。
- H3未接続の`auto_h3`及び`none`ではPicture参照を出力しないこと。
- `manual`ではH3接続に関係なく`picture_index`を使用すること。
- Preview、Save及び無関係なIMAGE入力をH3 Picture接続として誤検出しないこと。
- 読み取り専用ラベルが接続、切断、mode変更、`picture_index`変更及びworkflow読込後に即時更新されること。
- 読み取り専用ラベルが編集操作を受け付けず、`serialize=false`でworkflowのwidget値へ混入しないこと。
- フロントエンド表示値を改変してもバックエンドのPicture解決結果へ影響しないこと。
- 不確実特徴を保持分析へ確定挿入しないこと。
- PlannerBriefのセクション順、参照分離規則及び既存構文検証。
- `retry_max`、seed増分、stall及びComfyUI割込み。
- `keep_model_loaded=False`で本体とprojectorを解放すること。
- `reuse`、`refresh`及び`disabled`の読込・書込動作。
- memory hit及びComfyUI再起動相当のdisk hitでVision backendを呼ばないこと。
- profile、Picture参照mode、H3接続binding、`picture_index`、`subject_index`及びrenderer変更では観測を再推論せず再レンダリングすること。
- 画像pixel、モデル、projector、system prompt、追加指示及び推論設定の変更で観測cache keyが変わること。
- 同名画像の内容置換で以前の観測を再利用しないこと。
- 壊れたcache entryを無視し、正常推論後に原子的に置換すること。
- 失敗した推論が正常cacheを破壊しないこと。
- 成功時だけシアン色の成功ログを一回出すこと。

### 16.2 Fake backendテスト

Fake MTMD backendは画像byte列を解釈せず、要求メッセージに画像blockが正確に一個あり、data URIがPNGとして復号できることを検証する。正常プロトコル、部分欠落、無限反復、stall、例外及び割込みを再現する。

### 16.3 実モデルsmoke test

手動又は任意の統合テストとして次を実行する。

- Qwen3-VL-4B-Instruct Q4_K_M + 同梱F16 projector。
- Qwen3-VL-8B-Instruct Q4_K_M + 同梱F16 projector。
- Qwen3.8-27B-heretic-ara + 同じ系列のprojector。
- 人物一人、人物なし、複数人物、透明PNG、縦長、横長及び可読文字を含む画像。
- `image`出力をPreview ImageとMiniMax H3 Ref2Vの通常IMAGE入力へ接続できること。

実モデルの意味認識精度は単体テストの合否条件にしない。形式、停止性、画像受渡し、モデルとprojectorの組及び出力構造を検証する。

## 17. 非目標

- 動画全体のVision解析。
- 複数画像の一括比較又はキャラクター同一性判定。
- 顔認証、人物特定又は固有名詞の検索。
- OCR専用ノード相当の完全な文字起こし。
- 画像編集、背景除去、アップスケール又はマスク生成AI。
- 画像からScene時間、歌詞タイミング又はH3 Chain Planを生成すること。
- Visionモデルが推測した設定を事実として自動確定すること。
- Ref2V用画像への解析リサイズ、再圧縮又は画質補正。

## 18. 推奨ワークフロー

```text
任意のComfyUI画像ローダー又は画像生成ノード.IMAGE
  └─→ CL Image Analyzer (Vision GGUF).image_override

CL Image Analyzer (Vision GGUF).image
  └─→ MiniMax H3 Ref2V / Picture入力

CL Image Analyzer (Vision GGUF).result
  └─→ Preview STRING
  └─→ CL MV Prompt Planner (GGUF).planning_markdown
```

Plannerへ接続する場合は`subject_only`又は`planner_brief`を使用する。`general`は人間による確認、`scene_only`は背景案、`structured_json`はPython又は他の構造化処理を主用途とする。
