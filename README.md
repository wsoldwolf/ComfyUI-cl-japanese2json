# ComfyUI-cl-japanese2json

`CL Japanese to JSON (GGUF)` は、日本語の縮小版Markdownを英語へ翻訳し、MiniMax H3 Contex-Loop Planへ渡せる厳格なJSON文字列を生成する独立したComfyUIカスタムノードです。

LLMは箇条書き本文の日本語からUS Englishへの翻訳だけを担当します。ディレクティブ、箇条書き、参照タグ、日本語台詞、シーン構造、使用Subjectの選択、JSON構文はPythonが決定論的に処理します。LLMに最終JSONを生成させません。

## 必要環境

- Python 3.11以降を使用するComfyUI
- Qwen3 8Bなどのテキスト用GGUFモデル
- 使用環境に合う `llama-cpp-python`

このノードは `llama-cpp-python` を自動インストール・更新しません。CUDA対応wheelやユーザー独自ビルドが、依存解決によってCPU版へ置き換えられる事故を避けるためです。ComfyUIが使用しているPython環境へ、目的のGPU/CPUバックエンドに合う版をユーザー自身で導入してください。`requirements.txt` はなく、`pyproject.toml` の依存関係も空です。

`llama-cpp-python` が未導入でもカスタムノードのimportと登録は行えます。ノード実行時に手動導入を案内するエラーになります。

## Windows: llama-cpp-python の必須ビルド

Windowsでこのノードを使用する前に、ComfyUIが使用する仮想環境へCUDA対応の `llama-cpp-python` wheelを**必ず手動ビルドして導入**してください。本ノードは、この処理を自動実行しません。

以下はCUDA 13.0、Visual Studio 2022、RTX 4070 Ti（Compute Capability 8.9）およびRTX 5090（12.0）向けの例です。`GGML_NATIVE=OFF` とAVX-512無効化により、9950Xでビルドしたwheelを5900XTのようなAVX-512非対応CPUへ持ち込んだ場合にも動作する、移植可能なCPUバックエンドを生成します。

「x64 Native Tools Command Prompt for VS 2022」で実行してください。

```bat
cd /d C:\Software\ComfyUI
venv\Scripts\activate

set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.0"
set "PATH=%CUDA_PATH%\bin;%CUDA_PATH%\lib\x64;%PATH%"

set "CMAKE_GENERATOR=Visual Studio 17 2022"
set "CMAKE_GENERATOR_PLATFORM=x64"
set "CMAKE_ARGS=-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89;120 -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF -DGGML_AVX512_BF16=OFF -DGGML_AMX_TILE=OFF -DGGML_AMX_INT8=OFF -DGGML_AMX_BF16=OFF"
set "FORCE_CMAKE=1"

python -m pip install --upgrade pip setuptools wheel

if not exist dist mkdir dist
python -m pip -vvv wheel "llama-cpp-python==0.3.34" --no-deps --no-cache-dir --no-binary=llama-cpp-python -w dist
python -m pip install --force-reinstall --no-deps dist\llama_cpp_python-0.3.34-*.whl
```

ビルド後は、次のコマンドでCUDAバックエンドとCPU機能を確認できます。

```bat
python -c "import llama_cpp; print(llama_cpp.__version__); print(llama_cpp.llama_print_system_info().decode())"
```

## 導入

1. このディレクトリ全体を次の位置へ配置します。

   ```text
   ComfyUI/custom_nodes/ComfyUI-cl-japanese2json/
   ```

2. テキスト生成用GGUFを次のディレクトリ以下へ配置します。サブディレクトリも再帰的に探索されます。

   ```text
   ComfyUI/models/LLM/GGUF/
   ```

   ComfyUIへ追加の `LLM` モデルパスを登録している場合は、そのルートと `GGUF` サブディレクトリも探索します。ファイル名に `mmproj` を含むGGUFは対象外です。モデルの自動ダウンロードは行いません。

3. 使用環境に適した `llama-cpp-python` がComfyUIのPython環境でimportできることを確認します。
4. ComfyUIを再起動します。
5. ComfyUIノードから `CL Japanese to JSON (GGUF)` を追加します。

## 入力Markdown

ディレクティブと箇条書きは必ず行頭から書きます。箇条書きマーカーは `* ` です。

```text
# サブジェクト
* <Picture 1>を外観参照、<Audio 1>を声質参照として使用する人物。
* <Picture 2>を外観参照として使用する人物。

# 共通プロンプト
* 背景は近代的なオフィスビル街。

# シーン 5秒
* <Subject 1>が回転し、「ようこそ！」と言う。
* <Subject 2>が御辞儀する。

## 音響
* 環境音: 近代的なオフィスビル街の環境音。
* 効果音: 衣服の動作音。
* 発声: 指定台詞のみ

# シーン 5秒 継続
* <Subject 1>が手を上げる。
```

利用できるトップレベルディレクティブとシーン専用サブディレクティブは次のとおりです。

```text
# サブジェクト
# 共通プロンプト
# シーン [1～60秒] [継続]
## 音響
```

例えば `# シーン 8秒` は8秒のシーン、`# シーン 8秒 継続` は前シーンから継続する8秒のシーンです。旧形式の `# シーン 8秒生成する` と `継続する` は正規構文ではなく、警告後に5秒・非継続へフォールバックします。

秒数の既定値は5秒です。`継続` を指定しないシーンは映像と生成音声のコンテキストを明示的にリセットします。シーン1の `継続` は、外部動画コンテキストに未対応の本ノードでは警告のうえ非継続になります。

通常文中の `「日本語台詞」` は `<d>[Japanese]日本語台詞</d>` へ変換され、内部は翻訳されません。既存の `<d>...</d>` と次の参照タグも完全に保護されます。

```text
<Picture 1>～<Picture 9>
<Video 1>～<Video 9>
<Audio 1>～<Audio 3>
<Subject 1>～<Subject 4>
```

タグ名と番号の間には1個のASCII空白が必要です。`<Picture1>` や `<Audio1>` は不正形式として警告されるため、`<Picture 1>`、`<Audio 1>` と記述してください。

### シーン音響

`## 音響`は直前の`# シーン`に所属し、必ずそのシーンの通常の箇条書きより後へ置きます。内容は当該シーンの内部データだけへ格納され、他シーンへ継承されません。

```text
## 音響
* 環境音: 草原を吹く弱い風音。
* 効果音: 草を踏む足音と衣服の擦れ音。
* 発声: なし
```

音響は許可リスト方式です。`## 音響`、`環境音`、`効果音`又は`発声`を省略した場合、その項目は無効になります。すべて省略したシーンは`overall_soundscape:\nComplete silence.`になります。明示的に無効化する場合は`環境音: なし`、`効果音: なし`又は`発声: なし`も使用できます。

`環境音`及び`効果音`の本文には`<Audio N>`や日本語台詞を書かないでください。台詞とAudio参照は通常のシーン箇条書きへ記述します。

日本語台詞を使用するシーンでは、台詞に加えて次を必ず記述します。

```text
## 音響
* 発声: 指定台詞のみ
```

台詞があるのに`発声: 指定台詞のみ`がない場合、又は`発声: 指定台詞のみ`なのに台詞がない場合は、ランダム音声を許す曖昧なJSONを出力せずコンパイルエラーになります。発声が許可されていないシーンではSubject定義から`<Audio N>`参照を削除します。

この変更により、従来形式の台詞付きMarkdownには互換性上の注意があります。既存入力の各台詞シーンへ`## 音響`と`* 発声: 指定台詞のみ`を追加してください。無発声シーンは変更しなくても完全無音へフォールバックします。

## 出力例

ノードの `json_text` はコードフェンスや説明を含まない1個の `STRING` です。実際の英訳はモデルにより表現が多少変わります。

```json
{
  "prompt_prefix": "The background is a modern office-building district.",
  "defaults": {
    "duration_seconds": 5,
    "steps": 8
  },
  "shots": [
    {
      "id": "scene_1",
      "prompt": [
        "subject_definitions:\n<Subject 1> is a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.\n<Subject 2> is a character whose appearance is based on <Picture 2>.",
        "[Shot 1] <Subject 1> turns around and says, <d>[Japanese]ようこそ！</d>.",
        "[Shot 2] <Subject 2> bows.",
        "overall_soundscape:\nEnvironment: The ambience of a modern office-building district. Sound effects: Clothing movement. The only character vocalization is the exact shot-synchronized dialogue explicitly specified in this scene. No other sound is present.",
        "non_diegetic_music:\nN/A"
      ],
      "duration_seconds": 5,
      "context_length": 0,
      "audio_context_length": 0
    },
    {
      "id": "scene_2",
      "prompt": [
        "subject_definitions:\n<Subject 1> is a character whose appearance is based on <Picture 1>.",
        "[Shot 1] <Subject 1> raises a hand.",
        "overall_soundscape:\nComplete silence.",
        "non_diegetic_music:\nN/A"
      ],
      "duration_seconds": 5,
      "continuation_mode": "guide"
    }
  ]
}
```

各シーンの `subject_definitions` には、そのシーンのショット本文で参照したSubjectだけが番号順に入ります。共通プロンプトだけに登場するSubjectや、そのシーンで未使用のSubjectは自動挿入されません。

シーン内のショットに`<Subject N>`が一つもない場合、`subject_definitions`には`No character subject or reference-image person is active.`を固定で出力し、定義済み人物及び参照画像由来の人物を表示しないよう明示します。

シーン内のAudio参照を維持するのは、保護済み台詞と`発声: 指定台詞のみ`の両方が存在する場合だけです。発声項目の省略又は`発声: なし`ではAudio参照句を削除します。`does not speak`や`without speaking`等の否定された発声指示も無発声として扱います。

## BGMについて

MiniMax H3によるBGM生成はランダム性が高いため、本ノードはすべてのシーンの `prompt` 末尾へ `non_diegetic_music:\nN/A` を固定で追加し、非劇伴音楽の生成を無効化します。BGMはSunoなど別の音楽生成AIで作成し、動画生成後の編集工程で追加する運用を想定しています。台詞、環境音及び効果音は直前の`overall_soundscape`要素で独立して許可又は無効化します。

## UIパラメータ

| 名前 | 意味 |
| --- | --- |
| `plain_text` | 日本語の縮小版Markdown。空または空白だけの入力はエラーです。 |
| `model_name` | `models/LLM/GGUF` 以下で検出したGGUF。`(no GGUF models found)` のままでは実行できません。 |
| `max_tokens` | 各翻訳バッチの最大生成トークン数。最終JSON全体の長さではありません。 |
| `temperature` | 翻訳のsampling temperature。既定値は再現性重視の0.1です。 |
| `top_p` | nucleus samplingの上限です。 |
| `repetition_penalty` | `repeat_penalty` としてllama.cppへ渡します。 |
| `gpu_layers` | GPUへオフロードするモデルレイヤー数。`-1` は利用可能な全レイヤー、`0` はモデルレイヤーをGPUへ載せない設定です。 |
| `n_batch` | プロンプト処理時の論理バッチサイズです。 |
| `n_ctx` | モデルのコンテキスト長。`0` はGGUFに記録された学習時コンテキスト長を使用します。 |
| `flash_attn` | llama.cppのFlash AttentionをBooleanで切り替えます。非対応環境ではロードエラーになります。 |
| `kv_cache_type` | K/V両方のKVキャッシュ型。`q8_0` または `f16` を選びます。暗黙の型フォールバックはしません。 |
| `op_offload` | ホスト側テンソル演算のデバイスオフロード。 |
| `keep_model_loaded` | `True` なら成功後も同じロード設定のモデルをノードインスタンス内で再利用します。`False` なら成功・失敗後に解放します。 |
| `seed` | 翻訳生成seed。バッチと再試行でも決定論的に派生させます。 |
| `keep_last_prompt` | 実際の保持対象は最後に検証成功したJSONです。履歴ありなら現在の入力と生成設定を無視して即時返却します。履歴なしの初回はエラーや空文字列にせず通常処理し、成功JSONを保存します。 |
| `steps` | 最終JSONの `defaults.steps`。既定値は8、指定範囲は1～10000です。LLMの翻訳処理には影響しません。 |
| `retry_max` | 初回検証失敗後の最大再試行回数。既定値は3です。`0`は再試行なし、正数はその回数まで、`-1`は成功またはComfyUIからの中断まで上限なく再試行します。各回で成功区間を保持し、未解決区間だけを異なるseedで再送します。 |
| `save_debug_output` | 既定値は`False`です。`True`では原文、保護済み要求、LLM生応答、検証結果、成功時の正規形MarkdownとJSONを `ComfyUI/output/cl_japanese2json_debug/<実行ID>/` へ保存します。診断ファイルにはプロンプト内容が含まれるため、必要な実行時だけ有効にしてください。 |

モデルの再ロードが必要なのは、モデルファイル、`gpu_layers`、`n_batch`、`n_ctx`、`flash_attn`、`kv_cache_type`、`op_offload` が変わった場合です。生成パラメータだけの変更では、保持中のモデルを再ロードしません。

system promptはPythonへ埋め込まず、次のUTF-8ファイルから読み込みます。

```text
prompts/llmj2e_qwen3_8b_system_prompt.txt
```

内容変更はComfyUIキャッシュの外部指紋へ反映されますが、GGUFの再ロードは発生しません。

## Contex-Loop Planへの接続

`json_text` 出力をMiniMax H3 Contex-Loop Planノードの文字列入力 `plan_json_input` へ接続します。出力は `json.loads()` で再読込済みで、末尾に1個のLFを持つJSONオブジェクトです。

## 主なエラー

- **GGUFが見つからない**: `ComfyUI/models/LLM/GGUF` 以下へテキストモデルを配置し、ComfyUIを再起動してください。
- **`llama-cpp-python` をimportできない**: ComfyUIが使うPythonへ、GPU/CPU環境に合う版を手動導入してください。本ノードからは導入しません。
- **`GGML_TYPE_Q8_0` / `GGML_TYPE_F16` がない**: 選択したKV型の公開定数を持つ互換版が必要です。暗黙に別の型へ切り替えません。
- **Flash Attentionやoffloadのロード失敗**: バックエンドの対応状況を確認するか、UIで明示的に設定を変更してください。
- **コンテキスト長不足**: `n_ctx` または `max_tokens` を見直してください。1個の箇条書き区間を途中分割することはありません。
- **長い入力の翻訳に時間がかかる**: 文書全体のディレクティブと各箇条書き先頭を索引付きプレースホルダへ、参照タグと日本語発話を短い保護プレースホルダへ置換し、残った通常文章を一つの生テキスト翻訳ストリームとして送信します。区間末尾用プレースホルダと転送用JSONは使用せず、llama.cppの停止トークンで最終区間直後に生成を終了します。実効コンテキスト長へ収まる場合、文書全体を1回の推論で翻訳します。
- **翻訳検証失敗**: 構造プレースホルダの個数と順序、区間ごとに一意な保護プレースホルダ、説明文、コードフェンス、thinking、日本語残留などを検出しています。元のディレクティブ、参照タグ、`<d>` 発話はPython側の対応表から復元します。全区間の`SUB/COM/SCN/SND`マーカーが正順で残っていれば、冗長な`D`マーカーだけの欠落はPython側のブロック情報から復元します。先頭SUBマーカーがモデルによって番号付きSubjectタグと`is`へ置換された場合は、完全一致形式、1始まりの連番、行数、保護プレースホルダ所有関係がすべて正しい場合だけSUBマーカーへ戻します。`refers to`など任意の書換えは採用しません。構造応答が途中で欠落しても検証済み区間は保持し、欠落区間と境界未確定の隣接区間だけを`retry_max`の範囲で再試行します。モデルが構造プレースホルダをすべて省略した場合は、段落数または非空行数が区間数と一致すれば順番に厳格検証します。件数が一致しない長文応答でも、完全に検証できるCLJ保護プレースホルダを順序アンカーとして使い、アンカー間の件数が一致する範囲だけを安全に保持します。各再試行で新たに成功した区間も保持するため、正常な長文全体を再翻訳しません。再試行ごとにseedを変えます。各再試行後、Scene英文が残り、欠落がPython側へ完全保存された直接発話だけの場合は、その発話を辞書から補完して再検証します。Subject、Picture、Video、Audioの欠落や空の英文は補完せず、上限内では未解決区間として再試行します。`retry_max`へ到達した場合だけ、破損した結果を返さずノードエラーになります。`retry_max=-1`は明示的に中断するまで続く可能性があるため、モデルが恒常的に制約を守れない場合は有限値を使用してください。詳細確認が必要な場合は`save_debug_output=True`で再実行し、`ComfyUI/output/cl_japanese2json_debug`を確認してください。
- **デバッグ出力**: `save_debug_output=True`の実行ごとに専用サブディレクトリを作り、成功・失敗を問わず中間結果を保存します。`source.md`とLLM生応答には入力内容が含まれるため、共有前に内容を確認し、調査後は不要なディレクトリを削除してください。`ComfyUI/input`へは書き込みません。
- **長文入力での設定目安**: まず `max_tokens=4096`、`n_batch=256`、`keep_model_loaded=true` を使用してください。`max_tokens=8192` は通常応答を必ず遅くする値ではありませんが、モデルが冗長な出力を続けた場合の上限とコンテキスト予約を増やします。VRAMに余裕がある場合だけ `n_batch=512` を試せますが、これは主に入力処理へ効く値です。再現確認中はComfyUIのseed生成後制御を `fixed` にしてください。
- **モデルが制約を繰り返し破る**: `abliterated` 版より、同規模の標準的なinstruction-tuned GGUFを優先してください。モデル変更は翻訳速度よりも、プレースホルダの追従性改善を目的とします。
- **Qwen3のthinkingが混入する**: 本ノードはユーザーメッセージ末尾の `/no_think` を使用します。system promptでも全プレースホルダを翻訳、改変、移動、複製または削除しないよう明示しています。それでもQwen3が応答先頭へ正常に閉じた `<think>...</think>` を1個付けた場合だけ、その制御ブロックを破棄して後続の翻訳ストリームを検証します。途中のthinking、未閉鎖、複数ブロック、翻訳本文がない応答は検証エラーになります。
- **鉤括弧や `<d>` の不整合**: `「...」` と `<d>...</d>` は同一行で閉じ、入れ子にしないでください。
- **台詞と音響許可の不一致**: 台詞があるシーンには`## 音響`と`* 発声: 指定台詞のみ`が必要です。台詞がないシーンでは発声項目を省略するか`* 発声: なし`にしてください。
- **音響構文エラー**: `## 音響`は直前のシーンへ1回だけ置き、項目は`環境音`、`効果音`、`発声`を各1回まで使用してください。
- **シーン数エラー**: 1～128シーンが必要です。

## テスト

標準ライブラリだけで自動テストを実行できます。プロジェクトの親ディレクトリがimport可能な状態で、プロジェクトルートから次を実行します。

```text
python -m unittest discover -s tests -t . -v
```

自動テストは実GGUFやGPUを使わず、Llamaコンストラクタとchat completionをモックします。実モデル試験ではQwen3 8B GGUFを使い、`n_ctx=0/16384`、KVキャッシュ `q8_0/f16`、モデル保持、履歴、複数シーン、Subject 1～4、日本語台詞を個別に確認してください。

## ライセンスと参考元

このプロジェクトは [GNU General Public License v3.0](LICENSE) で配布します。

モデル探索、保持、VRAM解放、`llama-cpp-python` 呼び出しの要件を検討する際に、次のプロジェクトと公式資料を参考にしました。本実装は独立して作成しており、`ComfyUI-QwenVL-Mod` をimportせず、同プロジェクトへ実行時依存しません。

- https://github.com/huchukato/ComfyUI-QwenVL-Mod
- https://github.com/abetlen/llama-cpp-python
- https://docs.comfy.org/custom-nodes/backend/server_overview
- https://docs.comfy.org/custom-nodes/backend/datatypes

`llama-cpp-python` 自体はMIT Licenseです。
