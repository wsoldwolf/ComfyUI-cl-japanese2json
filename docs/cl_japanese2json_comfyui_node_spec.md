# cl_japanese2json ComfyUIカスタムノード実装仕様書

## 1. 目的

本書は`cl_japanese2json`コンパイラとPCM無音パディング機能を独立したComfyUIカスタムノードとして提供する実装要件を定義する。入力文法とJSON生成規則の正本は`docs/cl_japanese2json_spec.md`である。

本版はドラフトの破壊的改訂であり、後方互換性を要件としない。実装は明示的Shot、条件付きCommon、Python生成の話者ID、Retention、台詞指定及び参照音声駆動のAudio再利用リップシンク、BGM生成、既存BGM Audioの再利用、BGM内ボーカルへのリップシンク及びFull-Reference 6セクションを対象とする。

## 2. 境界と独立性

- パッケージ名: `ComfyUI-cl-japanese2json`
- ノードクラス: `CLJapaneseToJSONGGUF`, `CLAudioPad`
- 表示名: `CL Japanese to JSON (GGUF)`, `CL Audio Pad (PCM Silence)`
- カテゴリ: `MiniMax H3/Prompt Tools`, `MiniMax H3/Audio Tools`
- 出力ノードではない。
- ComfyUI本体及び他の`custom_nodes`を変更しない。
- ComfyUI-QwenVL-Modをimportしない。
- `llama-cpp-python`とモデルを自動インストール、更新、ダウンロードしない。
- Python標準ライブラリ以外をパッケージ依存関係へ宣言しない。

`llama-cpp-python`が存在しない環境でも、カスタムノードのimportと登録は成功させる。実行時にだけ手動導入を案内する`ModelLoadError`を発生させる。

`CLAudioPad`は`llama-cpp-python`を使用せず、ComfyUI標準`AUDIO`テンソルのAPIだけで動作させる。Contex-Loopがなくてもノード登録を成功させ、任意の`H3_CHAIN_PLAN`入力を接続した場合だけその辞書を参照する。

## 3. ノード登録

パッケージ直下`__init__.py`は次を公開する。

```python
NODE_CLASS_MAPPINGS = {
    "CLJapaneseToJSONGGUF": CLJapaneseToJSONGGUF,
    "CLAudioPad": CLAudioPad,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLJapaneseToJSONGGUF": "CL Japanese to JSON (GGUF)",
    "CLAudioPad": "CL Audio Pad (PCM Silence)",
}
```

クラスメタデータは次である。

```python
RETURN_TYPES = ("STRING",)
RETURN_NAMES = ("json_text",)
FUNCTION = "compile_json"
CATEGORY = "MiniMax H3/Prompt Tools"
OUTPUT_NODE = False
```

`compile_json()`は1要素tuple`(json_text,)`を返す。

## 4. INPUT_TYPES

### 4.1 CL Japanese to JSON (GGUF)

requiredの順序は次のとおりである。

| 名前 | 型 | 既定 | 範囲又は候補 |
| --- | --- | --- | --- |
| `plain_text` | STRING multiline | 空 | 空白だけは実行時エラー |
| `model_name` | COMBO | 最初の検出モデル | 検出ID |
| `max_tokens` | INT | 4096 | 32～16384、step 32 |
| `temperature` | FLOAT | 0.1 | 0.1～1.0、step 0.05 |
| `top_p` | FLOAT | 0.9 | 0.0～1.0、step 0.01 |
| `repetition_penalty` | FLOAT | 1.05 | 0.5～2.0、step 0.05 |
| `gpu_layers` | INT | -1 | -1～1000 |
| `n_batch` | INT | 256 | 32～4096、step 32 |
| `n_ctx` | INT | 0 | 0～131072、step 512 |
| `flash_attn` | BOOLEAN | True | True/False |
| `kv_cache_type` | COMBO | `q8_0` | `q8_0`, `f16` |
| `op_offload` | BOOLEAN | True | True/False |
| `keep_model_loaded` | BOOLEAN | True | True/False |
| `seed` | INT | 1 | 1～4294967295 |
| `keep_last_prompt` | BOOLEAN | False | True/False |
| `steps` | INT | 8 | 1～10000 |
| `retry_max` | INT | 10 | -1～100 |

optionalは次である。

| 名前 | 型 | 既定 | 用途 |
| --- | --- | --- | --- |
| `save_debug_output` | BOOLEAN | False | ComfyUI output下へ診断バンドルを保存 |
| `speech_guard` | COMBO | `strict` | `strict`, `warn`。未保護発声キューの扱い |

`steps`はJSONの`defaults.steps`だけへ反映し、LLM翻訳の生成設定へ渡さない。

`retry_max`の意味は次である。

- `0`: 初回失敗後に再試行しない。
- 1～100: 指定回数まで再試行する。
- `-1`: 成功、バックエンドエラー又はComfyUI中断まで無制限に再試行する。

既定値は長文での局所的なプレースホルダ欠落を吸収するため10とする。検証済み区間は再送しないため、再試行回数を増やしても毎回文書全体を推論してはならない。恒常的な失敗時の待ち時間を制限するため20又は`-1`を既定値にはしない。

`speech_guard`の意味は次である。

- `strict`: 保護台詞を同じ行に持たない肯定的な英語発声キューをエラーにする。
- `warn`: ComfyUIへWARNINGを出し、該当英文を変更せずJSON生成を続行する。Audio参照、話者ID又は発声許可は自動追加せず、無音フォールバックも変更しないため、MiniMax H3が想定外の人物音声を生成する可能性がある。

`warn`でも、台詞、番号付き台詞なしリップシンク又はSource Timeline駆動リップシンクとSoundscape発声許可の不一致、不正なダイレクトスピーチ、参照、リップシンク又はSoundscape構造はエラーである。

各値は実行時にも型と範囲を検証する。Booleanを整数として受理してはならない。

### 4.2 CL Audio Pad (PCM Silence)

requiredは次のとおりである。

| 名前 | 型 | 既定 | 範囲又は候補 |
| --- | --- | --- | --- |
| `audio` | AUDIO | 接続必須 | ComfyUI標準の`waveform`, `sample_rate`辞書 |
| `target_duration_seconds` | FLOAT | 0.0 | 0～86400、step 0.001。0はUI目標を無効化 |
| `extra_padding_seconds` | FLOAT | 0.0 | 0～3600、step 0.001 |
| `pad_position` | COMBO | `end` | `end`, `start`, `both` |

optionalは次である。

| 名前 | 型 | 用途 |
| --- | --- | --- |
| `plan` | H3_CHAIN_PLAN | Contex-Loopの完成フレーム数とfpsから必要な音声長を自動計算 |
| `match_audio` | AUDIO | 基準トラックの継続時間を最小目標に追加し、短い整列済みトラックの末尾を無音補完 |

入力波形は`[batch, channels, samples]`でなければならない。サンプルレート、波形dtype、デバイス、バッチ数及びチャンネル数を維持し、新規領域をPCM値`0.0`で埋める。入力音声を切り詰めたり、リサンプルしたり、音量を変更してはならない。

サンプル数は次で決定する。

```text
ui_target_samples = round(target_duration_seconds * sample_rate)
plan_target_samples = round(total_delivered_frames / fps * sample_rate)
match_target_samples = round(match_audio_samples / match_audio_sample_rate * sample_rate)
base_target_samples = max(original_samples, ui_target_samples, plan_target_samples, match_target_samples)
output_samples = base_target_samples + round(extra_padding_seconds * sample_rate)
padding_samples = output_samples - original_samples
```

`plan`がない場合の`plan_target_samples`、`match_audio`がない場合の`match_target_samples`はそれぞれ0である。Planにfpsがない場合は24fpsを既定とする。`match_audio`は波形を混合、連結又は出力せず、その継続時間だけを入力音声のサンプルレートへ換算して最小目標に使用する。基準より入力音声が長い場合も入力を切り詰めてはならない。`target_duration_seconds=0`、Planなし、`match_audio`なし、`extra_padding_seconds=0`なら入力AUDIOを同一オブジェクトのまま返す。

MiniMax H3 Audio Tracksへfull mixとvocal stemを渡す場合、full mixを時間軸の基準とする。Planで補完したfull mixの`padded_audio`をvocal側`CLAudioPad.match_audio`へ接続し、vocal側`extra_padding_seconds`は0とする。これにより、元の開始時刻を保ったまま短いvocal stemだけを基準トラックと同じ尺まで補完できる。`CLAudioPad`相互の`match_audio`接続は禁止する。Planへ戻るGeneration Profileを構成するLip-Sync OptionsにはPlan依存の`padded_audio`を接続せず、元のvocal stemを接続してComfyUIの循環依存を避ける。

`end`は原音の開始位置を維持して末尾へ全量を追加する。`start`は先頭、`both`は前後へほぼ等分し、奇数サンプルの余りを末尾へ置く。リップシンク用source trackでは`end`を既定かつ推奨とし、`start`と`both`は原音の時刻を移動させることをtooltipで明示する。

出力は`padded_audio`, `original_duration`, `padded_duration`, `padding_added`, `status`の順とする。秒数出力はFLOAT、状態はSTRINGである。

Python logger名及びユーザー可視ログ接頭辞は`cl_audiopad`とし、翻訳コンパイラの`cl_japanese2json`から分離する。

## 5. GGUFモデル探索

### 5.1 探索先

標準探索先は次である。

```text
ComfyUI/models/LLM/GGUF/
```

ComfyUIの`folder_names_and_paths`に`LLM`が登録されている場合、各登録ルート自体とその`GGUF`サブディレクトリも探索する。

### 5.2 規則

- 再帰探索する。
- 拡張子`.gguf`は大文字小文字を区別しない。
- ファイル名に`mmproj`を含むものは除外する。
- 実パスが重複する場合は1回だけ表示する。
- 異なるルートで相対名が衝突する場合は安定したルートIDを表示名へ付ける。
- 選択値からパスを解決するときは毎回再探索し、消失したファイルを使用しない。

モデルがない場合、COMBOは`(no GGUF models found)`を表示し、実行時に探索先を示す`ModelDiscoveryError`とする。

## 6. llama-cpp-pythonバックエンド

### 6.1 import

モジュールimport時に`llama_cpp`と`Llama`を任意依存として試行する。失敗は保持し、ノード登録を妨げない。モデルロード時に明示的な手動導入エラーへ変換する。

### 6.2 モデルロード引数

`Llama`へ次を渡す。

```python
{
    "model_path": resolved_path,
    "n_ctx": n_ctx,
    "n_gpu_layers": gpu_layers,
    "n_batch": n_batch,
    "flash_attn": flash_attn,
    "type_k": selected_ggml_type,
    "type_v": selected_ggml_type,
    "offload_kqv": True,
    "op_offload": op_offload,
    "chat_format": "qwen",
    "verbose": False,
}
```

`kv_cache_type=q8_0`は`GGML_TYPE_Q8_0`、`f16`は`GGML_TYPE_F16`へ明示写像する。選択定数が公開されないバックエンドでは別型へフォールバックせずエラーにする。

### 6.3 モデルシグネチャ

再利用可否は次のtupleで決める。

```text
(resolved path, size, mtime_ns,
 n_ctx, gpu_layers, n_batch, flash_attn, kv_cache_type, op_offload)
```

- シグネチャ一致かつモデル保持中なら再利用する。
- 不一致なら古いモデルを解放してロードする。
- temperature、top_p、repetition_penalty、max_tokens、seed、steps、retry_max、speech_guard、system prompt変更だけではロードシグネチャを変えない。

### 6.4 解放

モデル解放時は状態参照を先に消し、可能なら`close()`、なければfinalizerを呼ぶ。その後`gc.collect()`を行う。Torchが既に利用可能でCUDAが有効なら`torch.cuda.empty_cache()`及び可能な場合`torch.cuda.ipc_collect()`を呼ぶ。Torchを本パッケージから導入しない。

ロード失敗、翻訳失敗、JSON検証失敗では常にモデルを解放する。成功時は`keep_model_loaded=False`なら解放し、Trueなら保持する。

### 6.5 推論

`create_chat_completion()`へsystem/user message、`max_tokens`、`temperature`、`top_p`、`repeat_penalty`、`seed`及び停止条件を渡す。JSON modeは使わない。

ComfyUI実行時は`stream=True`で応答を逐次消費し、チャンクを連結して非ストリーミング時と同じ`choices[0].message.content`及び`finish_reason`を持つ応答へ再構築してからLLMJ2Eへ返す。ストリームがusageを返さない場合だけ、利用可能なtokenizer又は既存の保守的見積りでusageを補完する。翻訳、停止条件及び応答検証規則はストリーミングの有無で変更しない。

各バッチの各attemptはComfyUIの`ProgressBar`を1個作成し、受信した非空contentチャンク数を`max_tokens`に対する概算進捗として通知する。内部カウンターは毎チャンク更新する一方、`ProgressBar.update_absolute()`はUI/WebSocketイベントの滞留を避けるため最大4回/秒に制限する。開始値、完了値及びattempt切替は強制通知する。チャンクとトークンは必ずしも1対1ではなく、応答が`max_tokens`より前に終了し得るため、これは残り時間又は厳密なトークン割合ではない。検証成功時は完了値へ進め、再試行又は次バッチでは新しいバーを0から開始する。ComfyUI外での単体利用ではProgressBarを必須としない。

推論呼出し中はdaemon heartbeatを動かし、10秒ごとにバッチ番号、総バッチ数、attempt番号、経過秒、受信済みcontentチャンク数、最終チャンクからの経過時間及び進捗callback実行中フラグをINFOログへ記録する。最初のチャンク以前も0件として記録し、入力評価中の死活確認を可能にする。`progress_callback_active=true`のまま最終チャンク時刻だけが古くなる場合はUI通知内の待機、`false`ならllama.cppから次のチャンクを待っている状態と判断できる。heartbeatは応答又は例外時に必ず停止し、プロンプト本文及び途中の翻訳本文を記録しない。

Qwen3と判定でき、呼出しシグネチャが対応する場合は次を追加する。

- `enable_thinking=False`
- `chat_template_kwargs={"enable_thinking": False}`
- `reasoning=False`

ユーザーメッセージ末尾にも`/no_think`を置く。ただし、`/no_think`はsoft switchなのでこれだけをhard switchとして扱わない。Qwen3かつロード済みllama-cpp-pythonが`create_completion()`を提供する場合は、system/userメッセージをChatMLへ変換し、assistant生成開始位置へ空の`<think>\n\n</think>\n\n`を事前配置してtext completionを実行する。戻り値は通常のchat completion形式へ正規化する。停止条件には既存の構造停止トークンに加えて`<|im_end|>`及び`<|endoftext|>`を含める。

ストリーム応答は生本文を応答全体検証より先に保持する。位置にかかわらず正常に閉じた完全なthinking blockはQwen制御エンベロープとして除去できるが、不完全なタグを推測して削除しない。残留thinking、長さ上限又は構造不正があっても、構造マーカーで安全に分離できる各区間を個別検証し、正常区間を保持して未解決区間だけを再送する。未閉鎖thinking又は余分な前置きが最初の構造マーカーより前にある場合も、後続区間の回収を試みる。重複、欠落又は順序不正マーカーは隣接境界を曖昧にする区間だけを失敗させる。

未解決区間の再送時は参照用`CLJ...X`へ対応する標準参照タグを一時注釈し、モデルによる代名詞化を抑止する。応答がCLJトークン又は正確な標準参照タグのどちらを維持しても元のCLJトークンへ正規化してから通常検証する。それでも原文の文頭参照が省略された場合は、原文と英文の文境界が一意に対応するときだけ文頭へ決定論的に復元する。文中参照は推測復元しない。日本語台詞プレースホルダは展開しない。

### 6.6 コンテキスト計算

保持モデルがtokenizerを公開する場合は実トークン数を推定し、利用できない場合はUTF-8バイト長から保守的に見積もる。実効`n_ctx`に収まり、かつ1バッチ最大64区間となる範囲で翻訳区間をまとめる。長文を無制限な単一生成にせず、一方で一行単位推論にも戻さない。1区間が単独でも入らない場合は明示エラーとし、途中分割しない。

## 7. システムプロンプト

ファイルは次である。

```text
prompts/llmj2e_qwen3_8b_system_prompt.txt
```

- UTF-8又はUTF-8 BOMとして読む。
- 欠落、空又は不正UTF-8は`SystemPromptError`。
- mtime、size、SHA-256でキャッシュする。
- 内容変更時は同一プロセスでも再読込する。
- 文書構造又はPythonコードへ埋め込まない。

プロンプトはLLMを生テキスト翻訳器に限定し、`SUB/RET/COM/SCN/SND`構造プレースホルダ、参照及びダイレクトスピーチの改変を禁止する。話者IDは翻訳ストリームへ含めず、JSONGENが生成する。

## 8. コンパイラ実行フロー

`compile_json()`はノードインスタンスの再入可能ロック内で次を行う。

1. `keep_last_prompt=True`かつ成功履歴ありなら履歴を即時返却する。
2. UIパラメータを検証する。
3. system promptを読み込む。
4. 選択GGUFを再解決する。
5. ロードシグネチャに応じてモデルをロード又は再利用する。
6. `translate_markdown()`でCスタイルコメントを除外し、日本語Markdownを正規形へ変換する。
7. `parse_markdown()`で`Emd`へ変換する。
8. `generate_json(steps=steps, speech_guard=speech_guard)`でPlan文字列を作る。
9. `validate_final_json()`で最終文字列を再検証する。
10. 成功JSONを`last_json_text`へ保存してtupleで返す。
11. 設定又は失敗状態に従ってモデルを解放する。

擬似コードを示す。

```python
with instance_lock:
    if keep_last_prompt and last_json_text is not None:
        return (last_json_text,)
    try:
        validate_parameters()
        system_prompt = load_system_prompt()
        model_path = resolve_model_name(model_name)
        backend.ensure_loaded(model_path, load_settings)
        canonical = translate_markdown(
            plain_text,
            backend,
            system_prompt,
            generation_settings,
            retry_max,
        )
        emd = parse_markdown(canonical)
        json_text = generate_json(
            emd,
            steps=steps,
            speech_guard=speech_guard,
        )
        validate_final_json(json_text)
        last_json_text = json_text
        return (json_text,)
    except Exception:
        backend.clear_model()
        raise
    finally:
        if not keep_model_loaded:
            backend.clear_model()
```

## 9. 新Markdown構造の実装

### 9.1 認識ディレクティブ

```text
# サブジェクト
# 保持分析
# 共通プロンプト
# シーン [1～60秒] [継続]
## ショット [開始秒]
## 音響
```

トップレベルはSubject、Retention、Common、Sceneの順とし、前3者は任意かつ各1回までとする。暗黙Shot、`生成する`、`継続する`は即時エラーにする。

#### 9.1.1 コメント前処理

`compiler/comments.py`の決定論的スキャナーをLLMJ2E字句解析より前に実行する。MDPARSEも防御的に同じスキャナーを使用する。

- 行頭の空白を除いて`//`で始まる物理行は行末までコメント。
- 通常本文後方の`//`及びURLの`//`は通常文字列。
- `/* ... */`は行内及び複数行コメント。
- コメントの入れ子、未閉鎖開始記号及び対応しない終了記号は`CommentSyntaxError`。
- `「...」`及び`<d>...</d>`内部ではコメント記号を認識しない。
- コメントは空白へ置換し、改行と元の行番号を保持する。
- コメントだけの行は構文状態を変更せず、ディレクティブと箇条書きの間でも透明に扱う。
- コメント内部を翻訳レコード、参照走査、発声走査又はJSON生成へ渡さない。
- HTMLコメントはサポートせず、`CommentSyntaxError`とする。

#### 9.1.2 参照タグの正規範囲

MiniMax H3-Base-Ref2VAの入力上限に合わせ、正規の参照タグ範囲を次とする。

```text
<Picture 1>～<Picture 9>
<Video 1>～<Video 3>
<Audio 1>～<Audio 3>
<Subject 1>～<Subject 4>
```

`Subject`は本コンパイラが定義する論理被写体番号であり、MiniMax H3の入力メディア本数ではない。範囲外又は非正規の参照タグは警告を出し、文字列自体は翻訳から保護して復元する。

### 9.2 Common

Commonは`Emd.common_prompt`へ文書順で保存する。JSONGENはScene preamble及びShot本文からアクティブSubjectを、声質参照、リップシンク及びBGM再利用からアクティブAudioを確定してから、Commonの各行を次のように選択する。

- Subject又はAudio参照のない行は全Sceneへ適用する。
- Subject参照がある行は、その全SubjectがSceneでアクティブな場合だけ適用する。
- Audio参照がある行は、その全AudioがSceneで別途アクティブな場合だけ適用する。
- SubjectとAudioの両方がある行は両条件を満たす場合だけ適用する。
- Commonの参照だけでSubject又はAudioをアクティブにしない。
- Audio参照は正規形の`<Audio 1>`～`<Audio 3>`だけを許可する。
- 未定義Subject、不正Audio参照、ダイレクトスピーチ又はユーザー入力の`(Sx)`を含むCommonはエラー。肯定的な英語発声指示は`speech_guard`でエラー又はWARNING継続とする。

選択したCommon行は`detailed_description`内でScene preambleより前に出す。

### 9.3 Shot

各Sceneは`preamble`と`shots`を別に保持する。最初のShotは0ms固定、2個目以降は1～3桁の小数を含む開始秒をmsへ変換する。

不正時刻はLLMロード後であっても推論前の日本語字句解析で検出し、MDPARSEでも防御的に再検証する。

Shot内の構造化リップシンクバレットは次とする。

```text
* リップシンク: <Subject N> <- <Audio N> 「正確な台詞」
* リップシンク: <Subject N> <- <Audio N>
* リップシンク: <Subject N> <- ソースボーカル
```

Subject 1～4を必須とする。番号付き形式ではAudio 1～3も必須とする。台詞指定形式は空でない台詞1個を必須とし、番号付き参照音声駆動形式及びSource Timeline駆動形式は台詞を持たない。LLMJ2Eはいずれのバレットも翻訳ストリームへ含めず、Pythonで検証して次の正規形へ固定変換する。

```text
* Lip sync: <Subject N> <- <Audio N>: <d>[Japanese]正確な台詞</d>
* Lip sync: <Subject N> <- <Audio N>
* Lip sync: <Subject N> <- SOURCE_VOCAL
```

MDPARSEは正規形をShot行の元位置に保持する。JSONGENは通常の発話Audio再利用を`audio reuse`、`partially_copy`及び対象Subjectの`(SN)`へ展開する。同じAudioを複数Subjectへ割り当てること、又は同一Sceneで声質参照と信号再利用へ競合させることはエラーとする。

同じAudioがSceneの`BGM再利用`にも指定されている場合、そのリップシンクは独立した発話音声ではなく、再利用BGM内の元ボーカルを人物が歌唱演技する指定として展開する。記載された台詞は歌詞の正本であり、元BGMのボーカル信号、語句及びタイミングを保持して、置換又は追加ボーカルを生成しない。

参照音声駆動形式では、Audioの現在区間を発声内容とタイミングの唯一の正本とする。文字起こし又は歌詞推測は行わず、音素時刻、口の閉鎖、持続音及びフレーズ境界へ同期する固定英文を生成する。人声のない区間又は間奏では口を閉じ、Scene境界で曲若しくは歌唱フレーズを再開始せず、継続境界をまたぐフレーズは現在の口形と時刻を継続する。単語又は歌詞の生成、置換、反復、翻訳及び追加を禁止する。

Source Timeline駆動形式は、Contex-Loopの`source_timeline`へ接続されたロック済みフルミックスと、`lip_sync_voice`へ接続された同尺・同起点のボーカルステムを対象とする。番号付きAudioスロットを有効化せず、Source Vocalは口形駆動だけに使い、最終音声へ追加しない。

構造化リップシンク行自体が発声又は歌唱指示を兼ねる。別のShot行へダイレクトスピーチのない肯定的な発声又は歌唱指示を追加した場合は、通常の発声安全規則により`strict`ではエラー、`warn`では警告付きで通過させる。

字句解析では、`リップシンク:`又は`リップシンク：`で始まるバレットだけを構造化リップシンクとして認識する。`リップシンク中は...`等のコロンを伴わない通常文は、Common、Scene preamble又はShot本文の通常翻訳レコードとして扱う。

### 9.4 Retention

`# 保持分析`の関係語はPythonが固定マーカーへ変換し、説明部分だけを翻訳する。属性転送の転送先は構造として保持する。JSONGENは各SceneのアクティブSubjectへ規則をフィルタする。

### 9.5 話者ID

ユーザー入力及び正規形Markdownの`(Sx)`はエラーとする。JSONGENはShot本文を再走査し、各ダイレクトスピーチより前の同じ行にある最も近い`<Subject N>`を話者として、話者ID`(SN)`を生成する。

- 話者ID番号はSubject番号と同一であり、発声順に依存しない。
- 同じ行で台詞より前にSubject参照がなければエラー。
- 通常動作だけのSubject参照へは話者IDを付けない。

`(SN)`は実際の台詞位置、リップシンク展開、`subject_definitions`内のAudio定義及び`detailed_description`内のAudio利用説明へ出力する。MiniMax公式ガイドに従い`retention_analysis`へは出力しない。

### 9.6 Soundscape

Soundscapeは各Sceneの`Soundscape`値へ保存する。フィールドはEnvironment、Sound effects、Vocalization、Background music、構造化されたBackground music reuse及びSource audioである。発声は`なし`、`指定台詞のみ`、`参照音声のみ`又は`ソースボーカルのみ`であり、それぞれ`NONE`、`EXPLICIT_DIALOGUE_ONLY`、`REFERENCE_AUDIO_ONLY`又は`SOURCE_VOCAL_ONLY`へ固定変換する。`ソース音声: 完全維持`は`Source audio: FULLY_PRESERVE`へ固定変換する。発声省略又は`なし`は無発声、BGM生成、BGM再利用及びSource audioを全て省略すれば`non_diegetic_music: N/A`である。全項目の省略は完全無音である。

Source Timeline経路の例を次に示す。

```text
## ショット
* リップシンク: <Subject 1> <- ソースボーカル
## 音響
* 発声: ソースボーカルのみ
* ソース音声: 完全維持
```

間奏Sceneではリップシンク行を省略し、`発声: なし`と`ソース音声: 完全維持`を使用する。Source audio保持は生成BGM、BGM再利用、番号付きAudio、生成台詞、Environment及びSound effectsと同一Sceneで併用しない。

生成BGMは任意の日本語説明をLLMで英訳する。楽器、テンポ、リズム及び音量変化を対象とし、Audio参照、ダイレクトスピーチ又は具体的な歌詞は受理しない。劇中人物にも聞こえる音楽は生成BGMではなくShot本文へ書く。

既存BGM Audioは次の構造化バレットで指定する。

```text
* BGM再利用: <Audio 1> 完全コピー
* BGM再利用: <Audio 2> 部分コピー
* BGM再利用: <Audio 3> 部分コピー 00:20.000-00:30.000
```

上の3行は選択肢の例であり、同一Sceneには1行だけ記載する。LLMJ2Eは本文をLLMへ送らず、Audio番号、関係及び任意の元音源時間範囲を検証して次のいずれかへ固定変換する。

```text
* Background music reuse: <Audio 1> fully_copy
* Background music reuse: <Audio 2> partially_copy
* Background music reuse: <Audio 3> partially_copy 00:20.000-00:30.000
```

MDPARSEは`BackgroundMusicReuse(audio_number, relationship, source_start_ms, source_end_ms)`としてSceneのSoundscapeへ保存する。生成`BGM`と`BGM再利用`は相互排他である。

- `fully_copy`は元Audio全体を最終音声トラックとして1:1再利用する。元Audio全体とSceneの長さが一致する用途を前提とし、時間範囲を受理しない。Environment、Sound effects、生成台詞又は別Audioのリップシンクを同じSceneへ追加しない。同じAudio内のボーカルへのリップシンクだけは、追加音声を生成しないため許可する。
- `partially_copy`は元AudioのBGM層をaudience-only scoreとして再利用し、Environment、Sound effects、生成台詞又は許可されたリップシンクを別音響層として混在できる。
- `partially_copy`には`MM:SS.mmm-MM:SS.mmm`形式の元音源時間範囲を任意で指定できる。分は2桁以上、秒は`00`～`59`、ミリ秒は3桁、区切りはASCIIハイフンとする。
- 時間範囲は増加順で、その長さをScene durationとミリ秒単位で一致させる。JSONGENは元区間をScene先頭から末尾へ1:1で割り当てる。
- 時間範囲付き`partially_copy`は、元区間の音楽、ボーカル、編曲、楽器構成、テンポ、リズム、タイミング及び内部ミックスを保持し、再構成、再生成、スタイル変更、リタイミング、ループ、再開始及びクロスフェードを禁止する固定英文を出力する。
- BGM内ボーカルへ同期する場合は、Shotの`リップシンク`と`BGM再利用`に同じAudio番号を書く。正確な歌詞を持つ台詞指定形式又は歌詞を持たない参照音声駆動形式のどちらかをScene単位で選択する。
- Audio内容の文字起こし又は歌詞推測は行わない。参照音声駆動形式ではAudio信号自体が正本である。

## 10. Full-Reference 6セクション

各Contex-Loop Sceneオブジェクトの`prompt`は、正確に次の6文字列を順に持つ。

```text
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```

### 10.1 subject_definitions

Scene preamble及びShot本文で参照したSubjectだけを定義する。Commonだけの参照はアクティブ化に使わない。未定義Subjectはエラー。無Subject Sceneは次の固定文とする。

```text
subject_definitions:
No character subject or reference-image person is active.
```

発声が有効なSubjectの定義にAudio参照があればSubject本文からAudio句を除去し、独立行を生成する。

```text
<Audio N> is the voice-timbre reference for <Subject M> (Sx).
```

リップシンクAudioは次の役割を独立行で定義する。

```text
<Audio N> is the directly reused spoken-audio signal performed by <Subject M> (Sx) for exact lip synchronization in [Shot K].
```

参照音声駆動形式では次の役割を使用する。

```text
<Audio N> is the directly reused human-vocal audio signal performed by <Subject M> (Sx) for audio-driven lip synchronization in [Shot K].
```

BGM再利用Audioも独立行で定義する。BGM内ボーカルのリップシンクがある場合は、同じAudio定義へ対象Subject、話者ID及びShotを統合する。

```text
<Audio N> is the directly reused audience-only background-music signal.
<Audio N> is the directly reused audience-only background-music signal whose original vocal layer is performed in exact lip synchronization by <Subject M> (Sx) in [Shot K].
```

BGM再利用だけが有効な無Subject Sceneでは、無Subject固定文の後へAudio定義を追加する。

Source Vocal駆動時は番号付きAudio定義を作らず、現在のSource Timelineボーカルステム、対象Subject及びShotの結び付きを固定文で追加する。

### 10.2 summary

タスク種別は`[reference generation]`を基底とする。声質参照Audioがあれば`audio reference`、通常リップシンク又はBGM再利用Audioがあれば`audio reuse`を追加する。Source audio保持では`source audio preservation`、Source Vocal駆動では`source-vocal lip synchronization`を追加する。BGM再利用ではAudio番号、コピー関係及び同じAudio内のボーカルがリップシンクを駆動するかを記述する。`継続`はContex-Loopのガイド設定であり、公式Full-Referenceの`video continuation`参照種別とは扱わない。

### 10.3 retention_analysis

グローバルRetention規則をSceneのアクティブSubjectへだけ適用する。明示規則がなければ`fully_preserved`とする。属性転送の両Subjectが同一Sceneでアクティブでなければエラー。

Audio声質参照は`reference`を使い、元信号及び元発話をコピーしないことを明示する。通常リップシンクAudioは`partially_copy`を使い、対象Shot、対象Subject及び他の音響層が別生成であることを明示する。

BGM再利用Audioは入力どおり`fully_copy`又は`partially_copy`を使う。前者は元Audio全体を最終音声トラックとして1:1保持し、後者はBGM信号を保持しながら他音響層を別生成できることを記述する。時間範囲付き`partially_copy`では正確な始端と終端及び非再構成制約を記述する。同じAudio内のボーカルへ同期する場合は対象SubjectとShotを同じAudio行へ統合する。このセクションへ`(Sx)`を書かない。

Source audio保持では`Source Timeline: fully_preserved`固定行を出力し、フルミックスの波形、構成、ボーカル、テンポ、絶対時刻及び内部ミックスを維持する。Source Vocal駆動時は、口形駆動専用で最終ミックスへ追加しないこと、対象Subject及びShotを同じ固定行へ記述する。このセクションへ話者IDは書かない。

### 10.4 detailed_description

そのSceneへ適用されるCommon、Scene preamble、明示Shotの順に出す。

```text
detailed_description:
Applicable global style and constraints.
Scene-wide style and premise.
[Shot 1] Opening action.
[Shot 2] At 00:03.250, next action.
```

有効Audioがその話者のShot本文に明示されていなければ、声質とdeliveryだけを使い、元の音声信号又は元発話を追加しない固定文をShotへ追加する。

台詞指定の構造化リップシンク行は、指定Subjectが指定Audioの元信号と正確な台詞を物理的に発声し、その信号へ口を正確に同期する自然文へ置換する。置換、反復又は追加発声を禁止する固定文を続ける。

参照音声駆動の構造化リップシンク行は、Audioの現在区間を唯一の内容及び時刻情報として口形を同期し、人声のない区間で口を閉じ、Scene境界で再開始せず、歌詞を推測又は生成しない固定文へ置換する。

同じAudioがBGM再利用にも指定されていれば、元BGM内のボーカル、歌詞及びタイミングを保持した歌唱演技として展開する。独立した置換ボーカル又は追加ボーカルを生成しない。

時間範囲付きBGM再利用では、適用Common及びScene preambleの後、`[Shot 1]`より前に、元区間をScene全体へ1:1で割り当てて再構成、再生成又はリタイミングしない固定文を追加する。

Source audio保持では、現在のSource Timeline絶対時間区間をScene全体で連続使用し、生成、置換、再開始、再ミックス、リタイミング、ループ、クロスフェード、複製又は音声追加を行わない固定文を`[Shot 1]`より前へ追加する。`SOURCE_VOCAL`行はSource Vocalの人声区間、音素、閉口、持続音及びフレーズ境界だけで口形を駆動し、無声区間では閉口する固定文へ置換する。

### 10.5 overall_soundscape

Environment、Sound effects及び許可済み明示台詞だけを連結する。明示台詞を許可した場合でも台詞本文はここへ複製せず、shot-synchronizedな指定台詞だけが唯一の人物発声であると記述する。全音響が無効なら`Complete silence.`。生成BGMだけが有効なら、環境音、物理音及び人物発声がないことを明示し、BGMと矛盾する`Complete silence.`を使用しない。

BGM再利用だけが有効なら、元トラックに含まれる可能性があるボーカル又は他音響層を否定せず、別生成の環境音、物理音及び人物発声を追加しないと記述する。同じBGM内のボーカルへリップシンクする場合、その元ボーカルが唯一の同期発声であり、新しい声を生成しないと記述する。

Source audio保持では、ロック済みSource Timelineフルミックスが唯一の最終音声であると記述する。Source Vocalは口形駆動専用で、別音声としてミックスしない。音楽、声、環境音及び効果音を生成、置換又は追加しない。

### 10.6 non_diegetic_music

生成BGMとBGM再利用がともに省略されるか、生成BGMが`NONE`なら次とする。

```text
non_diegetic_music:
N/A
```

生成BGMが指定されれば翻訳済み本文を出力する。BGM再利用ではAudio番号、`fully_copy`又は`partially_copy`の意味、audience-only scoreであることを出力する。時間範囲があれば、正確な始端と終端、Scene先頭から末尾への1:1割当て及び非再構成制約を追加する。同じAudio内のボーカルへリップシンクする場合は対象Subject、話者ID及びShotを追加し、元ボーカルを置換又は重複生成しない。

Source audio保持では`N/A`ではなく、ロック済みSource Timelineフルミックスを現在の絶対時間位置から変更せず連続使用する固定文を出力する。番号付きAudio参照は出力しない。

H3のBGM生成はランダム性が高く、BGM再利用の固定文及び時間範囲も元波形の同一性を保証しない。再現性、品質又は波形同一性を優先する場合は、Suno等で用意した元音源を保持し、動画生成後にH3生成音声を元音源へ差し替える運用も維持する。

## 11. 発声安全規則

通常台詞及び台詞指定リップシンクの発声は次の三重条件を満たす場合だけ有効である。

1. Shot本文に`<d>...</d>`がある。
2. 同じ行で台詞より前に`<Subject N>`がある。
3. SceneのVocalizationが`EXPLICIT_DIALOGUE_ONLY`である。

参照音声駆動リップシンクは、Shotに台詞なし`Lip sync: <Subject N> <- <Audio N>`があり、SceneのVocalizationが`REFERENCE_AUDIO_ONLY`で、同じSceneに保護台詞がない場合だけ有効である。このモードはAudio区間に既に含まれる人声の視覚同期だけを許可し、新しい発声内容を許可しない。

Source Timeline駆動リップシンクは、Shotに`Lip sync: <Subject N> <- SOURCE_VOCAL`があり、SceneのVocalizationが`SOURCE_VOCAL_ONLY`、Source audioが`FULLY_PRESERVE`の場合だけ有効である。同一Sceneの番号付きAudio、保護台詞及び別Subjectへの同じSource Vocal割当てはエラーとする。

さらに、肯定的な発声動詞を持つ行は同じ行にダイレクトスピーチを必要とする。別行又は別Shotの台詞で条件を満たしたことにしない。話者IDはSubject番号から内部生成し、ユーザー指定を受理しない。

この発声動詞検査だけは`speech_guard`で挙動を選択できる。`strict`は従来どおりエラーにし、`warn`はCommon、Scene preamble又はShotの位置と検出語を`LOGGER.warning()`で記録してJSON生成を続行する。警告文にはMiniMax H3が想定外の人物発声を生成し得ることを含める。`warn`は検出行を書き換えず、Audio又は発声許可を追加せず、Soundscapeから生成される無音指定も変更しない。

検出語は会話、ナレーション、朗読、歌唱及び人物由来の非言語発声（笑い、息を呑む、溜め息、鼻歌、うめき等）を対象とする。Environment又はSound effectsとして指定する非人物音は対象外である。

保護台詞があるのにVocalizationが無効、Vocalizationが`EXPLICIT_DIALOGUE_ONLY`なのに保護台詞がない、番号付き台詞なしリップシンクと`REFERENCE_AUDIO_ONLY`が対にならない、Source Timeline駆動リップシンクと`SOURCE_VOCAL_ONLY`及び`FULLY_PRESERVE`が対にならない、又は構造化リップシンク、参照若しくはSoundscapeが不正な場合は、`warn`でもエラーとする。

台詞指定の構造化リップシンクは`EXPLICIT_DIALOGUE_ONLY`を、参照音声駆動の構造化リップシンクは`REFERENCE_AUDIO_ONLY`を必要とする。Audioから推測又は自動文字起こしを行わない。純粋なリップシンクだけのSubjectでは、Subject定義内のAudio声質参照を有効化せず、リップシンクバレットのAudioを信号再利用として有効化する。

無発声Sceneでは次を行う。

- Subject定義からAudio参照句を除去する。
- 独立Audio定義を生成しない。
- Audio retentionを生成しない。
- detailed_descriptionにAudioを残さない。
- overall_soundscapeへ人物発声を追加しない。

Environment、Sound effects又は生成BGM本文にAudio参照又は台詞がある場合はエラーとし、発声許可を迂回させない。BGM Audioは構造化された`BGM再利用`だけで指定する。

`fully_copy`のBGM再利用は元Audio全体を最終音声として使用するため、別生成のEnvironment、Sound effects、通常台詞又は別Audioのリップシンクと併用できない。同じAudio内のボーカルリップシンクは、元信号を増やさず映像だけを同期するため例外として許可する。BGM再利用Audioを同じSceneの声質参照としても使うことはできない。

## 12. 最終JSON検証

最終出力は次を満たす。

- UTF-8で表現可能なJSON object文字列。
- コードフェンス、前後説明なし。
- 末尾はLF1個。
- `prompt_prefix`は空文字列。
- `defaults.duration_seconds`はinteger。
- `defaults.steps`は1～10000のinteger。
- `shots`は1～128要素。
- 各IDは一意なstring。
- 各promptはstring 6要素。
- 6要素の接頭辞と順序はFull-Reference形式に一致。
- 6セクションは全て非空。
- detailed_descriptionのShot番号は1始まりの連番。
- non_diegetic_music内のAudio参照はsubject_definitions及びretention_analysisにも存在する。
- BGMが有効なSceneでoverall_soundscapeが`Complete silence.`にならない。
- durationは1～60のinteger。
- 継続Sceneは`continuation_mode=guide`だけを持つ。
- 非継続Sceneはvisual/audio context lengthを0にする。

検証成功前のJSONを履歴へ保存又は出力してはならない。

## 13. keep_last_promptとComfyUIキャッシュ

### 13.1 履歴

`last_json_text`はノードインスタンスごとに保持する。

- `keep_last_prompt=False`: 通常コンパイルし、成功値を更新する。
- `keep_last_prompt=True`かつ履歴あり: 現在のMarkdown、モデル、生成設定を評価せず履歴を返す。
- `keep_last_prompt=True`かつ履歴なし: 通常コンパイルして成功値を保存する。
- 失敗時: 以前の成功履歴を破壊しない。

### 13.2 IS_CHANGED

ComfyUIキャッシュ指紋には少なくとも次を含める。

- 選択モデルの解決パス、size、mtime_ns
- system promptのsize、mtime_ns、SHA-256

モデル解決失敗時も例外を投げず、エラー内容のダイジェストを安定した指紋として返す。

## 14. デバッグ出力

`save_debug_output=False`ではファイルを作らない。Trueの場合のみ、ComfyUIが返すoutputディレクトリの下へ次を作る。

```text
cl_japanese2json_debug/<timestamp>_<uuid8>/
```

内容は次を含み得る。

- `source.md`
- `system_prompt.txt`
- `manifest.json`
- 各attemptのprotected stream、request、raw response、metadata
- 成功時`canonical.md`及び`result.json`
- 失敗時`error.txt`

ComfyUI/inputへは書かない。診断保存自体の失敗は本来の生成結果を上書きせず警告にする。ファイルはプロンプトを含むためREADMEで取扱注意を示す。

## 15. エラー体系

最低限、次の専用例外を使用する。

- `CLJapaneseToJSONError`
- `ModelDiscoveryError`
- `ModelLoadError`
- `SystemPromptError`
- `CommentSyntaxError`
- `ProtectedTextError`
- `TranslationError`
- `MarkdownParseError`
- `JSONGenerationError`
- `JSONValidationError`

エラーは原因例外を保持し、対象モデル、Scene、Shot、行又はプレースホルダを可能な範囲で示す。破損結果へ黙ってフォールバックしてはならない。

## 16. Windowsビルド要件

READMEにはWindows利用前に`llama-cpp-python`の手動ビルドが必須であることを明記する。実行シェルは「x64 Native Tools Command Prompt for VS 2022」に限定する。

RTX 4070 TiとRTX 5090の共用wheel例は少なくとも次を含む。

```bat
set "CMAKE_GENERATOR=Visual Studio 17 2022"
set "CMAKE_GENERATOR_PLATFORM=x64"
set "CMAKE_ARGS=-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89;120 -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF -DGGML_AVX512_BF16=OFF -DGGML_AMX_TILE=OFF -DGGML_AMX_INT8=OFF -DGGML_AMX_BF16=OFF"
set "FORCE_CMAKE=1"
```

`GGML_NATIVE=OFF`及びAVX-512/AMX無効化は、AVX-512対応CPU上でビルドしたwheelを5900XT等へ移す際の不正命令を避けるためである。

## 17. テスト要件

実GGUF又はGPUなしで次を自動テストする。

### 17.1 モデル探索

- 再帰探索
- 大文字小文字を区別しないGGUF
- mmproj除外
- 実パス重複除外
- 表示ID衝突処理
- 選択後の消失

### 17.2 バックエンド

- 引数写像
- KV定数不足
- シグネチャ一致再利用
- 設定又はファイル変更時の再ロード
- constructor失敗後の空状態
- Qwen thinking無効化引数
- clearの冪等性

### 17.3 LLMJ2E

- 行コメント、行内ブロックコメント及び複数行ブロックコメントの推論前除外
- コメントだけの行による構文状態の非分断
- コメント内の偽ディレクティブ、参照及び日本語の無視
- 台詞内コメント記号及びURLの`//`保持
- 入れ子、未閉鎖、対応しないブロックコメント及びHTMLコメントの拒否
- 新ディレクティブ正規化
- Common正規化、順序及び禁止要素
- Common Audio参照の条件付き適用と非アクティブ化
- 旧Scene構文拒否
- Retention固定マーカー
- Shot時刻の推論前検証
- Soundscape固定値
- 台詞指定及び参照音声駆動リップシンクの固定正規化と推論対象外化
- BGM再利用の固定正規化、推論対象外化、関係、Audio番号及び元音源時間範囲検証
- BGM本文の保護付き翻訳
- ユーザー入力の`(Sx)`拒否
- 64区間以下の短文書を1推論
- 65区間以上又はコンテキスト不足時のレコード境界バッチ
- プレースホルダ欠落、重複、移動
- thinking、コードフェンス、切断、日本語残留
- 検証済み区間保持と未解決区間再試行
- 再試行ごとのseed変更

### 17.4 MDPARSE

- トップレベル順序
- Common格納、順序及び禁止要素
- Scene preamble
- Shot開始時刻と昇順
- 空Shotと空Soundscape
- SceneローカルSoundscape
- 台詞あり及び台詞なしの構造化リップシンク正規形
- Background music格納
- Background music reuseの構造化格納と生成BGMとの競合拒否
- 改行差

### 17.5 JSONGEN

- 6セクションの種類と順序
- 空`prompt_prefix`
- Subject抽出とSubjectless固定文
- RetentionのSceneフィルタと既定値
- CommonのSubject/Audio Sceneフィルタ、配置及び非アクティブ化
- 属性転送の両端検証
- Shot labelとtimestamp
- Subject番号に一致する話者IDの内部生成
- 台詞指定の発声三重条件と参照音声駆動モードの対応条件
- 肯定的発声指示の同一行台詞要件
- speech_guardのstrictエラー、warnログ継続及び構造エラー非緩和
- Audioの条件付き定義・削除
- 台詞指定及び参照音声駆動Audio再利用リップシンクの定義、summary、`partially_copy`及びShot展開
- BGM Audioの`fully_copy`/`partially_copy`、時間範囲の1:1割当て、無Subject Scene及び同一Audio内ボーカルリップシンク
- `fully_copy`と追加音響層の競合拒否
- 声質参照と信号再利用の競合拒否
- `retention_analysis`に`(Sx)`がないこと
- Soundscape許可リストと完全無音
- BGM生成と`N/A`フォールバック
- BGM再利用の`non_diegetic_music`展開
- continuation/reset
- steps反映
- 最終JSON再検証

### 17.6 ノード統合

- V1登録情報
- INPUT_TYPESの全入力と順序
- 1要素tuple返却
- keep_last_prompt
- モデル保持と解放
- failure時履歴保持
- retry_max転送
- speech_guardのUI既定値、値検証及びJSONGEN転送
- ストリーミング応答の連結、finish reason及びusage保持
- ComfyUI進捗の開始、逐次更新、検証成功時完了及びattempt切替
- 長時間推論中のheartbeatと終了時停止
- debug bundle
- workflows内の新構文
- `CLAudioPad`の登録、UI既定値及び出力メタデータ
- UI秒数、H3 Planフレーム数及び追加マージンからのサンプル数計算
- `match_audio`の継続時間を基準とする同一及び異種サンプルレートでのサンプル数計算
- `end`、`start`、`both`のPCM値0.0配置
- dtype、デバイス、チャンネル、サンプルレート及び付加AUDIOメタデータの保持
- 十分長い音声を切らないことと不正AUDIO/Plan/パラメータの拒否
- `cl_audiopad` logger及びユーザー可視接頭辞
- 同梱BGM workflowがパディング後の同一AUDIOをLoop Start、Current及びAssembleへ渡すこと

実モデル試験は別途手動で行い、Qwen3 GGUF、複数Scene、複数Shot、Common、話者ID自動生成、Audio声質参照、台詞指定及び参照音声駆動リップシンク、BGM生成、BGM Audio再利用、BGM内ボーカルリップシンク、Retention、長文再試行を確認する。

## 18. README要件

READMEは少なくとも次を含む。

- 目的とLLM/Pythonの責務分離
- ドラフトの破壊的構文変更
- Windowsの必須wheelビルド
- 導入手順とGGUF探索先
- 新Markdownのコピー可能な例
- Cスタイルコメント構文と制約
- CommonのSubject及びAudioに基づく条件付き適用
- Retentionマーカー
- Shot時刻規則
- 話者IDと発声許可
- 台詞指定及び参照音声駆動Audio再利用リップシンク構文
- BGM Audio再利用とBGM内ボーカルリップシンク構文
- 無音フォールバックとAudio除去
- BGM生成、BGM再利用と省略時`N/A`
- 6セクションJSON例
- Suno等を使う後編集前提
- 全UI入力
- PCM無音パディングノードのPlan自動計算、基準音声尺、UI目標、追加マージン及び接続例
- デバッグ出力と機密性注意
- テスト手順
- ライセンス

## 19. 完了条件

- 仕様書、README、実装、system prompt、テスト、同梱workflowが同じ新構文を使用する。
- コメントを推論前に安全に除外し、改行、行番号及び台詞本文を保持する。
- CommonがSceneのアクティブSubject及びAudio集合に従って適用され、それだけでSubject又はAudioを有効化しない。
- ユーザー入力に話者IDがなく、JSONGENがSubject番号に一致するIDを生成する。
- 暗黙Shotが残っていない。ただし拒否テストは除く。
- 各Scene promptがFull-Referenceの6セクションを公式順で持つ。
- Retentionと話者IDの公式制約を満たす。
- 無発声時にAudio又は人物発声を有効化しない。
- 通常リップシンクを`audio reuse`と`partially_copy`へ決定論的に変換する。
- 歌詞なしリップシンクを`REFERENCE_AUDIO_ONLY`と対応付け、Audio区間駆動の固定英文へ変換する。
- BGM Audio再利用を指定された`fully_copy`又は`partially_copy`へ変換し、同じAudio内のボーカルリップシンクと統合する。
- BGM生成又はBGM再利用を`non_diegetic_music`へ出力し、省略時は`N/A`とする。
- `llama-cpp-python`を自動変更しない。
- H3 Plan、基準音声又はUI秒数に対する不足音声を`CLAudioPad`がサンプル単位で自動計算し、原音を切らずPCM値0.0で補完する。
- 全自動テストが成功する。
