# cl_japanese2json ComfyUIカスタムノード実装仕様書

## 1. 目的

本書は`cl_japanese2json`コンパイラを独立したComfyUI V1カスタムノードとして提供する実装要件を定義する。入力文法とJSON生成規則の正本は`docs/cl_japanese2json_spec.md`である。

本版はドラフトの破壊的改訂であり、後方互換性を要件としない。実装は明示的Shot、話者ID、Retention及びFull-Reference 6セクションだけを対象とする。

## 2. 境界と独立性

- パッケージ名: `ComfyUI-cl-japanese2json`
- ノードクラス: `CLJapaneseToJSONGGUF`
- 表示名: `CL Japanese to JSON (GGUF)`
- カテゴリ: `MiniMax H3/Prompt Tools`
- 出力ノードではない。
- ComfyUI本体及び他の`custom_nodes`を変更しない。
- ComfyUI-QwenVL-Modをimportしない。
- `llama-cpp-python`とモデルを自動インストール、更新、ダウンロードしない。
- Python標準ライブラリ以外をパッケージ依存関係へ宣言しない。

`llama-cpp-python`が存在しない環境でも、カスタムノードのimportと登録は成功させる。実行時にだけ手動導入を案内する`ModelLoadError`を発生させる。

## 3. ノード登録

パッケージ直下`__init__.py`は次を公開する。

```python
NODE_CLASS_MAPPINGS = {
    "CLJapaneseToJSONGGUF": CLJapaneseToJSONGGUF,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLJapaneseToJSONGGUF": "CL Japanese to JSON (GGUF)",
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
| `retry_max` | INT | 3 | -1～100 |

optionalは次である。

| 名前 | 型 | 既定 | 用途 |
| --- | --- | --- | --- |
| `save_debug_output` | BOOLEAN | False | ComfyUI output下へ診断バンドルを保存 |

`steps`はJSONの`defaults.steps`だけへ反映し、LLM翻訳の生成設定へ渡さない。

`retry_max`の意味は次である。

- `0`: 初回失敗後に再試行しない。
- 1～100: 指定回数まで再試行する。
- `-1`: 成功、バックエンドエラー又はComfyUI中断まで無制限に再試行する。

各値は実行時にも型と範囲を検証する。Booleanを整数として受理してはならない。

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
- temperature、top_p、repetition_penalty、max_tokens、seed、steps、retry_max、system prompt変更だけではロードシグネチャを変えない。

### 6.4 解放

モデル解放時は状態参照を先に消し、可能なら`close()`、なければfinalizerを呼ぶ。その後`gc.collect()`を行う。Torchが既に利用可能でCUDAが有効なら`torch.cuda.empty_cache()`及び可能な場合`torch.cuda.ipc_collect()`を呼ぶ。Torchを本パッケージから導入しない。

ロード失敗、翻訳失敗、JSON検証失敗では常にモデルを解放する。成功時は`keep_model_loaded=False`なら解放し、Trueなら保持する。

### 6.5 推論

`create_chat_completion()`へsystem/user message、`max_tokens`、`temperature`、`top_p`、`repeat_penalty`、`seed`及び停止条件を渡す。JSON modeは使わない。

Qwen3と判定でき、呼出しシグネチャが対応する場合は次を追加する。

- `enable_thinking=False`
- `chat_template_kwargs={"enable_thinking": False}`
- `reasoning=False`

ユーザーメッセージ末尾にも`/no_think`を置く。

### 6.6 コンテキスト計算

保持モデルがtokenizerを公開する場合は実トークン数を推定し、利用できない場合はUTF-8バイト長から保守的に見積もる。実効`n_ctx`に収まる範囲で翻訳区間をまとめる。1区間が単独でも入らない場合は明示エラーとし、途中分割しない。

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

プロンプトはLLMを生テキスト翻訳器に限定し、`SUB/RET/SCN/SND`構造プレースホルダ、参照、`(Sx)`、ダイレクトスピーチの改変を禁止する。

## 8. コンパイラ実行フロー

`compile_json()`はノードインスタンスの再入可能ロック内で次を行う。

1. `keep_last_prompt=True`かつ成功履歴ありなら履歴を即時返却する。
2. UIパラメータを検証する。
3. system promptを読み込む。
4. 選択GGUFを再解決する。
5. ロードシグネチャに応じてモデルをロード又は再利用する。
6. `translate_markdown()`で日本語Markdownを正規形へ変換する。
7. `parse_markdown()`で`Emd`へ変換する。
8. `generate_json(steps=steps)`でPlan文字列を作る。
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
        json_text = generate_json(emd, steps=steps)
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
# シーン [1～60秒] [継続]
## ショット [開始秒]
## 音響
```

旧`# 共通プロンプト`、暗黙Shot、`生成する`、`継続する`は即時エラーにする。

### 9.2 Shot

各Sceneは`preamble`と`shots`を別に保持する。最初のShotは0ms固定、2個目以降は1～3桁の小数を含む開始秒をmsへ変換する。

不正時刻はLLMロード後であっても推論前の日本語字句解析で検出し、MDPARSEでも防御的に再検証する。

### 9.3 Retention

`# 保持分析`の関係語はPythonが固定マーカーへ変換し、説明部分だけを翻訳する。属性転送の転送先は構造として保持する。JSONGENは各SceneのアクティブSubjectへ規則をフィルタする。

### 9.4 話者ID

`<Subject N> (Sx)`又は空白なしの`<Subject N>(Sx)`は、Subject参照と話者IDを合わせた1個の不可分プレースホルダとして保護する。これによりLLMが両者の間へ翻訳語句を挿入できない。ペアになっていない`(Sx)`も単独でプレースホルダ保護する。JSONGENはShot本文を再走査し、次を検証する。

- ダイレクトスピーチより前の同じ行に`<Subject N> (Sx)`がある。
- ID単独行がない。
- 同一SubjectのIDが不変。
- 同一IDのSubjectが不変。
- 新規IDが実発声順の連番。

`(Sx)`は`subject_definitions`内のAudio定義と`detailed_description`へ出力する。MiniMax公式ガイドに従い`retention_analysis`へは出力しない。

### 9.5 Soundscape

Soundscapeは各Sceneの`Soundscape`値へ保存する。発声省略又は`なし`は無発声である。Environment、Sound effects、Vocalizationの全省略は完全無音である。

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

Scene preamble及びShot本文で参照したSubjectだけを定義する。未定義Subjectはエラー。無Subject Sceneは次の固定文とする。

```text
subject_definitions:
No character subject or reference-image person is active.
```

発声が有効なSubjectの定義にAudio参照があればSubject本文からAudio句を除去し、独立行を生成する。

```text
<Audio N> is the voice-timbre reference for <Subject M> (Sx).
```

### 10.2 summary

タスク種別は`[reference generation]`を基底とし、有効Audioがあれば`[reference generation + audio reference]`とする。`継続`はContex-Loopのガイド設定であり、公式Full-Referenceの`video continuation`参照種別とは扱わない。

### 10.3 retention_analysis

グローバルRetention規則をSceneのアクティブSubjectへだけ適用する。明示規則がなければ`fully_preserved`とする。属性転送の両Subjectが同一Sceneでアクティブでなければエラー。

Audio声質参照は`reference`を使い、元信号及び元発話をコピーしないことを明示する。このセクションへ`(Sx)`を書かない。

### 10.4 detailed_description

Scene preambleを冒頭へ置き、次に明示Shotを順に出す。

```text
detailed_description:
Scene-wide style and premise.
[Shot 1] Opening action.
[Shot 2] At 00:03.250, next action.
```

有効Audioがその話者のShot本文に明示されていなければ、声質とdeliveryだけを使い、元の音声信号又は元発話を追加しない固定文をShotへ追加する。

### 10.5 overall_soundscape

Soundscapeの許可項目だけを連結する。明示台詞を許可した場合でも台詞本文はここへ複製せず、shot-synchronizedな指定台詞だけが唯一の人物発声であると記述する。何も許可しない場合は`Complete silence.`。

### 10.6 non_diegetic_music

常に次とする。

```text
non_diegetic_music:
N/A
```

H3のランダムBGMを無効化し、Suno等で生成した音楽を後編集する運用を前提とする。

## 11. 発声安全規則

発声は次の三重条件を満たす場合だけ有効である。

1. Shot本文に`<d>...</d>`がある。
2. 同じ行で台詞より前に`<Subject N> (Sx)`がある。
3. SceneのVocalizationが`EXPLICIT_DIALOGUE_ONLY`である。

さらに、肯定的な発声動詞を持つ行は同じ行にダイレクトスピーチを必要とする。別行又は別Shotの台詞で条件を満たしたことにしない。

無発声Sceneでは次を行う。

- Subject定義からAudio参照句を除去する。
- 独立Audio定義を生成しない。
- Audio retentionを生成しない。
- detailed_descriptionにAudioを残さない。
- overall_soundscapeへ人物発声を追加しない。

Environment又はSound effectsにAudio参照又は台詞がある場合はエラーとし、発声許可を迂回させない。

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

- 新ディレクティブ正規化
- 旧Commonと旧Scene構文拒否
- Retention固定マーカー
- Shot時刻の推論前検証
- Soundscape固定値
- `(Sx)`保護
- 全文1推論
- コンテキスト時のレコード境界バッチ
- プレースホルダ欠落、重複、移動
- thinking、コードフェンス、切断、日本語残留
- 検証済み区間保持と未解決区間再試行
- 再試行ごとのseed変更

### 17.4 MDPARSE

- トップレベル順序
- Scene preamble
- Shot開始時刻と昇順
- 空Shotと空Soundscape
- SceneローカルSoundscape
- 改行差

### 17.5 JSONGEN

- 6セクションの種類と順序
- 空`prompt_prefix`
- Subject抽出とSubjectless固定文
- RetentionのSceneフィルタと既定値
- 属性転送の両端検証
- Shot labelとtimestamp
- 話者IDの全体一意性
- 発声三重条件
- 肯定的発声指示の同一行台詞要件
- Audioの条件付き定義・削除
- `retention_analysis`に`(Sx)`がないこと
- Soundscape許可リストと完全無音
- 固定BGM無効化
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
- debug bundle
- workflows内の新構文

実モデル試験は別途手動で行い、Qwen3 GGUF、複数Scene、複数Shot、話者ID、Audio声質参照、Retention、長文再試行を確認する。

## 18. README要件

READMEは少なくとも次を含む。

- 目的とLLM/Pythonの責務分離
- ドラフトの破壊的構文変更
- Windowsの必須wheelビルド
- 導入手順とGGUF探索先
- 新Markdownのコピー可能な例
- Retentionマーカー
- Shot時刻規則
- 話者IDと発声許可
- 無音フォールバックとAudio除去
- 6セクションJSON例
- 固定`non_diegetic_music: N/A`
- Suno等を使う後編集前提
- 全UI入力
- デバッグ出力と機密性注意
- テスト手順
- ライセンス

## 19. 完了条件

- 仕様書、README、実装、system prompt、テスト、同梱workflowが同じ新構文を使用する。
- `# 共通プロンプト`と暗黙Shotが残っていない。ただし廃止説明及び拒否テストは除く。
- 各Scene promptがFull-Referenceの6セクションを公式順で持つ。
- Retentionと話者IDの公式制約を満たす。
- 無発声時にAudio又は人物発声を有効化しない。
- `llama-cpp-python`を自動変更しない。
- 全自動テストが成功する。
