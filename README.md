# ComfyUI-cl-japanese2json

`CL Japanese to JSON (GGUF)` は、日本語の縮小版Markdownを英語へ翻訳し、MiniMax H3 Full-Reference形式のContex-Loop Plan JSONを生成する独立したComfyUIカスタムノードです。生成BGMに加えて、既存BGM Audioの再利用と、そのBGMに含まれるボーカルへのリップシンクも構造化して指定できます。

LLMが担当するのは箇条書き本文の日本語からUS Englishへの翻訳だけです。ディレクティブ、参照タグ、日本語台詞、シーンとショットの構造、使用するSubject、話者ID、6セクションの順序、JSON構文はPythonが決定論的に処理します。LLMに最終JSONを生成させません。

現在の入力文法はドラフト版です。旧来の明示的な`(Sx)`話者ID、暗黙ショット、`N秒生成する`、`継続する`との後方互換性はありません。

## 必要環境

- Python 3.11以降を使用するComfyUI
- Qwen3 8Bなどのテキスト用GGUFモデル
- 使用環境に合う`llama-cpp-python`

このノードは`llama-cpp-python`を自動インストール・更新しません。CUDA対応wheelやユーザー独自ビルドが依存解決によってCPU版へ置き換えられる事故を避けるためです。`requirements.txt`はなく、`pyproject.toml`の依存関係も空です。

`llama-cpp-python`が未導入でもノードのimportと登録はできますが、実行時に手動導入を案内するエラーになります。

## Windows: llama-cpp-pythonの必須ビルド

Windowsで使用する前に、ComfyUIの仮想環境へCUDA対応の`llama-cpp-python` wheelを必ず手動ビルドして導入してください。次はCUDA 13.0、Visual Studio 2022、RTX 4070 Ti（Compute Capability 8.9）及びRTX 5090（12.0）向けの例です。

`GGML_NATIVE=OFF`とAVX-512/AMX無効化により、9950Xでビルドしたwheelを5900XTのようなAVX-512非対応CPUへ持ち込めるCPUバックエンドにします。

必ず「x64 Native Tools Command Prompt for VS 2022」で実行してください。通常の`cmd.exe`だけでは必要なVisual Studioビルド環境が初期化されないため対象外です。

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

ビルド後はバックエンドを確認できます。

```bat
python -c "import llama_cpp; print(llama_cpp.__version__); print(llama_cpp.llama_print_system_info().decode())"
```

## 導入

1. このディレクトリ全体を`ComfyUI/custom_nodes/ComfyUI-cl-japanese2json/`へ配置します。
2. テキスト生成用GGUFを`ComfyUI/models/LLM/GGUF/`以下へ配置します。サブディレクトリも再帰探索します。追加のComfyUI `LLM`モデルパスがあれば、そのルートと`GGUF`サブディレクトリも探索します。ファイル名に`mmproj`を含むGGUFは除外します。
3. 使用環境向けの`llama-cpp-python`がComfyUIのPythonからimportできることを確認します。
4. ComfyUIを再起動し、`MiniMax H3/Prompt Tools`から`CL Japanese to JSON (GGUF)`を追加します。

モデルの自動ダウンロードは行いません。

## 入力Markdown

最小構文は次のとおりです。ディレクティブと`* `は必ず行頭から書き、同じブロックのディレクティブと箇条書きの途中へ空行を入れないでください。

```text
# サブジェクト
* <Picture 1>を外観参照、<Audio 1>を明記された台詞の声質参照として使用する人物。
* <Picture 2>を外観参照として使用する人物。

# 保持分析
* <Subject 1> 完全に保持: 顔、髪型、髪色及び衣装を維持する。
* <Subject 2> 部分的に保持: 衣装を維持し、画風はシーン指定へ合わせる。

# 共通プロンプト
* 明るい昼の近代的なオフィス街を、鮮やかな2Dアニメ調で描く。
* <Subject 1>と<Subject 2>の外観を混同しない。

# シーン 8秒
* カメラは二人を正面から捉える。
## ショット
* <Subject 1>が画面左で「ようこそ！」と正確に一度だけ言う。
* <Subject 2>が画面右で御辞儀する。
## ショット 4.5秒
* カメラが二人へゆっくり接近する。
## 音響
* 環境音: 遠くの都市環境音。
* 効果音: 衣服の微かな動作音。
* 発声: 指定台詞のみ
* BGM: なし
```

利用できるディレクティブは次のとおりです。

```text
# サブジェクト
# 保持分析
# 共通プロンプト
# シーン [1～60秒] [継続]
## ショット [開始秒]
## 音響
```

`# サブジェクト`、`# 保持分析`、`# 共通プロンプト`は不要なら省略できます。この順序で、最初の`# シーン`より前に各1回まで置きます。シーンは1～128個です。

`# 共通プロンプト`はグローバルなスタイル、背景、画面上の制約を簡潔に書く場所です。参照を含まない行は全シーンへ適用します。`<Subject N>`又は`<Audio N>`を含む行は、その行で参照する全Subjectと全Audioがシーンのプリンブル、ショット又は音響設定で実際に有効な場合だけ適用します。共通文だけではSubject又はAudioをアクティブにしません。Commonで使用できるAudioは正規形の`<Audio 1>`～`<Audio 3>`です。台詞、`(Sx)`及び肯定的な発声指示は書けません。出力時は、適用された共通文、シーンプリンブル、`[Shot 1]`以降の順で`detailed_description`へ配置します。

### コメント

Cスタイルの行コメントとブロックコメントを使用できます。コメントは翻訳要求と生成JSONへ含まれません。

```text
// この行全体はコメントです。

/*
複数行のメモも書けます。
ディレクティブや参照タグを書いても無視されます。
*/

# シーン 5秒 /* 行内のメモ */
## ショット
* <Subject 1>が前を向く。 /* 後でカメラを調整 */
```

- `//`は、空白を除いて物理行の先頭にある場合だけコメントを開始します。文の後ろの`//`や`https://example.com`はコメントになりません。
- `/* ... */`は行内と複数行で使えます。入れ子、閉じ忘れ、対応しない`*/`はエラーです。
- `「...」`及び`<d>...</d>`の内側では、`//`と`/* ... */`を台詞の文字として保持します。
- コメントだけの行は、ディレクティブとその箇条書きの間に置いてもブロックを分断しません。
- コメントは空白へ置換して改行と行番号を維持した後、ディレクティブ解析より前に除外します。
- HTMLコメント`<!-- ... -->`は対応せず、使用するとエラーになります。

### シーンとショット

`# シーン 8秒`は8秒の生成単位です。秒数を省略すると5秒です。`# シーン 8秒 継続`は前の生成シーンをガイドとして継続します。最初のシーンの`継続`は外部コンテキスト未対応のため警告して非継続にします。

旧`# シーン 8秒生成する`と`継続する`はエラーです。

各シーンには少なくとも1個の明示的な`## ショット`が必要です。

- `# シーン`直下から最初の`## ショット`までの箇条書きは、`detailed_description`で`[Shot 1]`より前に置くシーン共通のスタイル・前提です。台詞は書けません。
- 最初の`## ショット`に開始秒は書きません。開始は0秒です。
- 2個目以降は`## ショット N秒`が必須です。Nは整数又は小数1～3桁です。
- 開始秒は昇順で、シーン時間より小さくなければなりません。
- 例として`## ショット 4.5秒`は`[Shot 2] At 00:04.500, ...`になります。
- `## 音響`を使う場合は全ショットの後へ1回だけ置きます。

### 参照タグ

次の参照タグは翻訳前にプレースホルダへ退避し、LLM応答検証後に完全一致で復元します。

```text
<Picture 1>～<Picture 9>
<Video 1>～<Video 9>
<Audio 1>～<Audio 3>
<Subject 1>～<Subject 4>
```

タグ名と番号の間には1個のASCII空白が必要です。`<Picture1>`のような形式は警告されます。

`# サブジェクト`の各箇条書きが順に`<Subject 1>`、`<Subject 2>`となります。`<Picture N>`又は`<Video N>`がSubjectの出典にすぎない場合、独立した定義を増やさず、そのSubject定義内へ残します。

各シーンの`subject_definitions`には、シーンプリンブル又はショット本文で実際に参照したSubjectだけを番号順に出します。未定義Subjectはエラーです。Subjectが1つもないエフェクト専用シーンでは、次の固定文になります。

```text
subject_definitions:
No character subject or reference-image person is active.
```

### 保持分析

`# 保持分析`はグローバルな宣言ですが、各シーンでは実際に参照されるSubjectの規則だけを`retention_analysis`へ出します。

```text
# 保持分析
* <Subject 1> 完全に保持: <Picture 1>の人物の顔、髪型、髪色及び衣装を維持する。
* <Subject 2> 部分的に保持: 衣装だけを維持し、照明と質感は変更する。
* <Subject 3> 属性転送 -> <Subject 1>: <Picture 2>のフォトリアルな照明、質感及びレンダリング特性を<Subject 1>へ転送する。
* <Subject 4> 弱い参照: <Picture 3>の構図と雰囲気だけを大まかに参照する。
```

| 日本語 | JSON内のマーカー |
| --- | --- |
| `完全に保持` | `fully_preserved` |
| `部分的に保持` | `partially_preserved` |
| `属性転送` | `attribute_transfer` |
| `弱い参照` | `weak_reference` |

`属性転送`だけは`-> <Subject N>`で転送先を指定します。転送元と転送先は異なる必要があり、規則が適用されるシーンでは両方がシーン内で参照されなければなりません。明示規則のないアクティブSubjectは`fully_preserved`へ安全にフォールバックします。Audioの声質参照は`reference`として自動生成し、公式規則に従って`retention_analysis`内へ`(Sx)`は書きません。

### 台詞、話者ID、Audio参照

通常文中の`「日本語台詞」`は`<d>[Japanese]日本語台詞</d>`へ変換され、内部は翻訳されません。既存の`<d>...</d>`も保護されます。

人物が話す箇条書きでは、同じ行の台詞より前に話者の`<Subject N>`を記述します。

```text
* <Subject 1>が「こんにちは」と言う。
* <Subject 2>が「よろしく」と答える。
```

Pythonは各台詞の直前にある最も近い`<Subject N>`を話者とし、JSONの該当箇所へ`(SN)`を自動挿入します。たとえば`<Subject 2>`の話者IDは、登場順や発声順にかかわらず常に`(S2)`です。ユーザーが`(Sx)`を入力するとエラーになります。話者IDは実際の台詞、対応するAudio定義及びAudio利用説明にだけ生成し、通常の動作参照や`retention_analysis`へは付けません。台詞だけ、又は発声動詞だけでは発声を有効にできません。

台詞シーンには全て次の許可が必要です。

```text
## 音響
* 発声: 指定台詞のみ
```

台詞があるのに許可がない、許可があるのに台詞がない、台詞より前の同じ行に`<Subject N>`がない、肯定的な発声指示に同じ行の台詞がない場合はコンパイルエラーです。これにより未指定の声、掛け声、疑似言語を促す曖昧な結果を拒否します。

アクティブな話者のSubject定義に`<Audio N>`があるときだけ、独立したAudio定義と`reference`保持分析を生成します。Audioはその話者の明記された台詞の声質と話し方だけに使用し、元音声信号や元の発話内容はコピーしません。台詞のないシーンではSubject定義からAudio句を取り除きます。

### 既存音声によるリップシンク

元のAudio信号を部分的に再利用して人物の口を同期させる場合は、Shot内へ次の構造化バレットを書きます。

```text
# シーン 6秒
## ショット
* <Subject 1>がカメラを見る。
* リップシンク: <Subject 1> <- <Audio 1> 「こんにちは、よろしくお願いします。」
## 音響
* 発声: 指定台詞のみ
* BGM: なし
```

`<Subject N>`は1～4、`<Audio N>`は1～3です。台詞は空にできず、日本語鉤括弧又は既存の`<d>...</d>`を正確に1個指定します。Audioからの自動文字起こしは行わないため、実際の音声と同じ台詞を記載してください。

この行はLLMへ送らずPythonが固定変換します。出力では`summary`へ`audio reuse`、`retention_analysis`へ`partially_copy`、発声位置へ`<Subject N> (SN)`を生成し、元信号と台詞を保持したリップシンクとして`detailed_description`へ展開します。同じAudioを複数人物へ割り当てることや、同一シーンで声質参照と信号再利用の両方へ使うことはできません。

### 既存BGMの再利用とボーカルのリップシンク

既存のBGM Audioを使う場合は、`BGM`の代わりに`BGM再利用`を指定します。

```text
## 音響
* BGM再利用: <Audio 1> 完全コピー
```

関係は次の2種類です。

- `完全コピー`: `<Audio N>`全体を最終音声トラックとして1:1再利用します。別の環境音、効果音、生成台詞、別Audioのリップシンクは追加できません。
- `部分コピー`: `<Audio N>`のBGM層を視聴者だけに聞こえる音楽として再利用し、環境音、効果音、生成台詞などの別音響層を併用できます。

BGM内のボーカルへ人物の口を同期させる場合は、同じAudio番号を`リップシンク`にも指定します。歌詞は自動文字起こしされないため、元ボーカルと一致する正確な文字列を書いてください。

```text
# サブジェクト
* <Picture 1>を外観参照として使用する歌手。

# シーン 8秒
## ショット
* <Subject 1>がステージ中央でカメラを見ながら音楽に合わせて身体を動かす。
* リップシンク: <Subject 1> <- <Audio 1> 「夜空を越えて、君のもとへ」
## 音響
* 発声: 指定台詞のみ
* BGM再利用: <Audio 1> 完全コピー
```

この例では、`<Audio 1>`の音楽と元ボーカルを変更せず、映像上の`<Subject 1> (S1)`だけを歌詞とタイミングへ同期します。別の歌声は生成しません。`発声: 指定台詞のみ`はリップシンクでも必須です。

`リップシンク`バレット自体が歌唱・発声指示を兼ねます。別のバレットへ「歌う」「発声する」などの肯定的な発声指示だけを書かないでください。追加する場合は、身体動作、視線、表情、カメラなど発声以外の演技だけを記述します。

`BGM再利用`はLLMへ送らず、PythonがAudio番号と`fully_copy`又は`partially_copy`へ固定変換します。出力では同じAudioを`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`及び`non_diegetic_music`へ一貫して反映します。生成用`BGM`と`BGM再利用`は同一シーンで併用できません。

### 音響

`## 音響`はシーンローカルの許可リストです。省略した項目は無効です。全項目を省略したシーンは`overall_soundscape:\nComplete silence.`になります。空の`## 音響`はエラーです。

```text
## 音響
* 環境音: 草原を吹く弱い風音。
* 効果音: 足音と衣服の擦れ音。
* 発声: なし
* BGM: ゆっくりしたピアノと低い弦楽器。終盤で徐々に音量を下げる。
```

各項目は1回までです。環境音、効果音及び生成用BGMは任意文又は`なし`、発声は`なし`又は`指定台詞のみ`だけを使えます。環境音、効果音及び生成用BGMへ`<Audio N>`や台詞は書けません。既存Audioは`BGM再利用: <Audio N> 完全コピー|部分コピー`でだけ指定します。

`BGM`は登場人物に聞こえず、視聴者だけに聞こえる非ダイジェティック音楽です。楽器、テンポ、リズム、音量変化を具体的に記述します。劇中で人物にも聞こえる音楽はShot本文へ書いてください。

生成用`BGM`と`BGM再利用`の両方を省略するか`BGM: なし`とすると`non_diegetic_music:\nN/A`になります。生成用BGMは英訳し、BGM再利用はAudio番号とコピー関係を`non_diegetic_music`へ出力します。MiniMax H3によるBGM生成はランダム性が高いため、再現性や楽曲品質を優先する場合は従来どおりSunoなど別の音楽生成AIで作り、動画生成後に追加する運用も推奨します。

## JSON出力

トップレベル`prompt_prefix`は空文字列です。各生成シーンの`prompt`配列は、公式Full-Reference形式に合わせた次の6文字列を厳密にこの順で持ちます。

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

出力例です。英訳表現はモデルによって多少変わります。

```json
{
  "prompt_prefix": "",
  "defaults": {
    "duration_seconds": 5,
    "steps": 8
  },
  "shots": [
    {
      "id": "scene_1",
      "prompt": [
        "subject_definitions:\n<Subject 1> is a character whose appearance is based on <Picture 1>.\n<Audio 1> is the voice-timbre reference for <Subject 1> (S1).",
        "summary:\n[reference generation + audio reference] The target video uses <Subject 1> in a 2-shot scene. <Audio 1> is referenced only for the explicitly specified dialogue.",
        "retention_analysis:\n<Subject 1> (used in [Shot 1]): fully_preserved - The defined identity and visual characteristics are preserved.\n<Audio 1>: reference - only the voice timbre and delivery are referenced for <Subject 1>; the source signal and its original speech are not copied.",
        "detailed_description:\nA bright modern office district is rendered in a vivid 2D anime style.\n[Shot 1] <Subject 1> (S1) says <d>[Japanese]ようこそ！</d>. For <Subject 1> (S1)'s explicitly specified dialogue in this shot, use <Audio 1> only as a voice-timbre and delivery reference; do not copy or introduce any other speech from the source audio.\n[Shot 2] At 00:04.500, the camera slowly approaches <Subject 1>.",
        "overall_soundscape:\nEnvironment: Distant city ambience. The only character vocalization is the exact shot-synchronized dialogue explicitly specified in this scene. No other ambience, physical sound, or character vocalization is present.",
        "non_diegetic_music:\nN/A"
      ],
      "duration_seconds": 8,
      "context_length": 0,
      "audio_context_length": 0
    }
  ]
}
```

非継続シーンは`context_length: 0`と`audio_context_length: 0`を持ちます。継続シーンは代わりに`continuation_mode: "guide"`を持ちます。

## UIパラメータ

| 名前 | 意味 |
| --- | --- |
| `plain_text` | 日本語縮小版Markdown。空入力はエラーです。 |
| `model_name` | 検出したテキストGGUF。 |
| `max_tokens` | 各翻訳推論の最大生成トークン数。 |
| `temperature` | sampling temperature。既定値0.1。 |
| `top_p` | nucleus sampling上限。 |
| `repetition_penalty` | llama.cppの`repeat_penalty`。 |
| `gpu_layers` | GPUへオフロードするレイヤー数。`-1`は全レイヤー、`0`はCPU側です。 |
| `n_batch` | 入力処理の論理バッチサイズ。 |
| `n_ctx` | コンテキスト長。`0`はGGUF既定値です。 |
| `flash_attn` | Flash Attentionを切り替えます。 |
| `kv_cache_type` | K/Vキャッシュ型。`q8_0`又は`f16`。 |
| `op_offload` | ホスト側テンソル演算のデバイスオフロード。 |
| `keep_model_loaded` | 成功後に同じ設定のモデルを再利用します。 |
| `seed` | 翻訳seed。再試行ごとに決定論的に変更します。 |
| `keep_last_prompt` | 最後に検証成功したJSONがあれば現在の入力を無視して返します。 |
| `steps` | JSONの`defaults.steps`。既定値8、範囲1～10000。翻訳には影響しません。 |
| `retry_max` | 検証失敗後の最大再試行回数。既定3、`0`はなし、`-1`は成功又は中断まで無制限です。 |
| `save_debug_output` | 中間情報をComfyUIのoutput下へ保存します。既定`False`。 |

モデルファイル又は`gpu_layers`、`n_batch`、`n_ctx`、`flash_attn`、`kv_cache_type`、`op_offload`が変わると再ロードします。生成パラメータだけの変更では保持中モデルを再ロードしません。

## 翻訳と再試行

文書全体のディレクティブ、各箇条書き先頭、参照タグ、日本語台詞を索引付きプレースホルダへ置き換え、残る日本語本文を1本の保護翻訳ストリームとして送ります。実効コンテキストへ収まる場合は文書全体を1回で推論します。収まらない場合だけ、箇条書きを途中分割せず複数バッチにします。話者IDは翻訳後のJSON生成時にPythonが付与します。

応答では構造と保護プレースホルダの個数・順序・所有区間、コードフェンス、thinking、日本語残留などを検証します。失敗時は検証済み区間を保持し、未解決区間だけを新しいseedで`retry_max`まで再送します。正常に閉じた先頭`<think>...</think>`を1個だけ無視できます。

`save_debug_output=True`では、実行ごとのディレクトリを`ComfyUI/output/cl_japanese2json_debug/`へ作り、`source.md`、system prompt、保護要求、LLM生応答、検証メタデータ、成功時の`canonical.md`と`result.json`、失敗時の`error.txt`を保存します。入力内容を含むため共有前に確認してください。`ComfyUI/input`へは書きません。

system promptは`prompts/llmj2e_qwen3_8b_system_prompt.txt`からUTF-8で読み込みます。変更はComfyUIキャッシュ指紋へ反映されます。

## Contex-Loop Planへの接続

`json_text`をMiniMax H3 Contex-Loop Planノードの`plan_json_input`へ接続します。出力は説明やコードフェンスを含まない1個の`STRING`で、`json.loads()`による再検証済み、末尾は1個のLFです。

## 主なエラー

- GGUFがない: `ComfyUI/models/LLM/GGUF`以下へモデルを置き、ComfyUIを再起動します。
- `llama-cpp-python`をimportできない: ComfyUIのPythonへ適切な版を手動導入します。
- `Windows Error 0xc000001d`: 実行CPUが対応しない命令を含むwheelの可能性があります。上記の`GGML_NATIVE=OFF`、AVX-512/AMX無効化設定でリビルドします。
- コンテキスト不足: `n_ctx`又は`max_tokens`を見直します。
- プレースホルダ欠落: `retry_max`を増やすか、標準的なinstruction-tuned GGUFを試します。診断には`save_debug_output=True`を使います。
- 台詞エラー: 同じショット行で台詞より前に`<Subject N>`を書き、`「...」`と発声動詞を記述し、シーン末尾で`発声: 指定台詞のみ`を許可します。`(Sx)`は入力しません。
- リップシンクエラー: Shot内で`リップシンク: <Subject N> <- <Audio N> 「正確な台詞」`と書き、`発声: 指定台詞のみ`を許可します。同一Audioの役割競合も確認します。
- BGMエラー: 生成用`BGM`にはAudio参照、台詞又は具体的な歌詞を書かず、既存Audioは`BGM再利用`で指定します。`BGM`と`BGM再利用`は併用できません。`完全コピー`では同じBGM Audio内のボーカルリップシンクを除き、別の音響層を追加できません。
- ショットエラー: 最初は`## ショット`、2個目以降は昇順の`## ショット N秒`にします。
- コメントエラー: `/* ... */`の入れ子、閉じ忘れ、対応しない`*/`、HTMLコメントがないか確認します。行コメントの`//`は行頭でだけ有効です。

## テスト

実GGUFやGPUを使わない自動テストを、プロジェクトルートから実行できます。

```text
python -m unittest discover -s tests -t . -v
```

## ライセンスと参考元

このプロジェクトは[GNU General Public License v3.0](LICENSE)で配布します。

実装検討ではComfyUI、`llama-cpp-python`、MiniMax H3の公式プロンプト文書及びComfyUI-QwenVL-Modを参考にしています。本実装は独立しており、ComfyUI-QwenVL-Modをimportせず実行時依存しません。

- https://github.com/huchukato/ComfyUI-QwenVL-Mod
- https://github.com/abetlen/llama-cpp-python
- https://docs.comfy.org/custom-nodes/backend/server_overview
- https://docs.comfy.org/custom-nodes/backend/datatypes

`llama-cpp-python`自体はMIT Licenseです。
