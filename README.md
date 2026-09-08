# ComfyUI-cl-japanese2json

`CL Japanese to JSON (GGUF)` は、日本語の縮小版Markdownを英語へ翻訳し、MiniMax H3 Full-Reference形式のContex-Loop Plan JSONを生成する独立したComfyUIカスタムノードです。生成BGM、番号付き既存BGM Audioの再利用、及びContex-LoopのSource TimelineフルミックスとSource Vocalステムによる歌詞なしリップシンクを構造化して指定できます。`CL Vocal to Prompt Segments`はボーカルステムの有声・無音検出とWhisperの単語時刻をSuno Lyricsへ対応付け、編集可能な日本語プロンプト、SRT及び検証JSONを生成します。`CL Load Text File (Drag & Drop)`は任意のローカル場所からUTF-8テキストを選択又はD&DしてSTRINGへ渡し、`CL Audio Pad (PCM Silence)`は音源がH3 Plan又は基準トラックより短い場合の不足サンプルを自動計算して無音補完します。

LLMが担当するのは箇条書き本文の日本語からUS Englishへの翻訳だけです。ディレクティブ、参照タグ、日本語台詞、シーンとショットの構造、使用するSubject、話者ID、6セクションの順序、JSON構文はPythonが決定論的に処理します。LLMに最終JSONを生成させません。

現在の入力文法はドラフト版です。旧来の明示的な`(Sx)`話者ID、暗黙ショット、`N秒生成する`、`継続する`との後方互換性はありません。

## 必要環境

- Python 3.11以降を使用するComfyUI
- Qwen3 8Bなどのテキスト用GGUFモデル
- 使用環境に合う`llama-cpp-python`
- Vocal区間・SRT生成を使用する場合のみ、ComfyUI環境へ手動導入したOpenAI Whisper（`openai-whisper`）とローカル`.pt`チェックポイント

このノードは`llama-cpp-python`を自動インストール・更新しません。CUDA対応wheelやユーザー独自ビルドが依存解決によってCPU版へ置き換えられる事故を避けるためです。`requirements.txt`はなく、`pyproject.toml`の依存関係も空です。

`llama-cpp-python`又はOpenAI Whisperが未導入でもノードのimportと登録はできますが、それぞれを必要とするノードの実行時に手動導入を案内するエラーになります。本プロジェクトはWhisperパッケージ及びモデルも自動インストール、更新又はダウンロードしません。

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
4. Vocal区間・SRT生成を使用する場合は、OpenAI WhisperをComfyUIのPython環境へユーザー自身で導入し、任意の公式`.pt`チェックポイントを`ComfyUI/models/whisper/`以下へ配置します。モデル名指定による暗黙ダウンロードは使用しません。
5. ComfyUIを再起動し、`MiniMax H3/Prompt Tools`から`CL Japanese to JSON (GGUF)`、`CL Vocal to Prompt Segments`又は`CL Load Text File (Drag & Drop)`、`MiniMax H3/Audio Tools`から必要に応じて`CL Audio Pad (PCM Silence)`を追加します。

モデルの自動ダウンロードは行いません。

## 任意パスのテキストファイルを読み込む

`CL Load Text File (Drag & Drop)`のボタンでファイルを選択するか、Windows Explorer等からノード上へファイルをドロップします。ファイルは`ComfyUI/input`へコピー又はアップロードされず、ブラウザで読み取った内容だけが`text`出力になります。`CL Vocal to Prompt Segments.lyrics_text`、`CL Japanese to JSON (GGUF).plain_text`又は任意のSTRINGプレビューノードへ接続できます。ノード自身は本文プレビューを表示しません。

ブラウザのセキュリティ仕様により、絶対パスそのものはComfyUIバックエンドへ渡しません。選択元は任意のローカル場所で構いませんが、ノードが保持するのはファイル名、サイズ・更新時刻のメタデータ及びファイル内容です。元ファイルを後から編集しても自動再読込されないため、変更後は再度選択又はD&Dしてください。

- UTF-8及びUTF-8 BOMに対応し、改行をLFへ正規化します。
- 最大サイズは16 MiBです。UTF-8でないファイル、NULを含むバイナリ又は破損した埋め込みデータはエラーにします。
- 本文はBase64としてワークフローJSONへ保存されます。ワークフロー単体で再実行できますが、機密テキストを含むワークフローを共有しないでください。
- 実行ログは`[cl_textfile]`接頭辞で、ファイル名、バイト数及び文字数だけを出します。本文や絶対パスは出しません。

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

`# 共通プロンプト`はグローバルなスタイル、背景、画面上の制約を簡潔に書く場所です。翻訳した全行を文書順に改行で連結し、トップレベル`prompt_prefix`へ一度だけ格納します。Contex-Loopによって全シーンへ無条件に適用されるため、特定シーンだけに必要な条件はScene又はShotへ書いてください。`<Subject N>`や`<Audio N>`を含める場合も全シーンで有効な参照だけに限定してください。Commonで使用できるAudioは正規形の`<Audio 1>`～`<Audio 3>`です。台詞と`(Sx)`は書けません。肯定的な英語発声指示は`speech_guard`で検査します。

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
<Video 1>～<Video 3>
<Audio 1>～<Audio 3>
<Subject 1>～<Subject 4>
```

`Picture`、`Video`、`Audio`の上限は、MiniMax H3-Base-Ref2VAの[公式入力仕様](https://www.minimax.io/news/minimax-h3-open-source)（画像9枚、動画3本、音声3本）に合わせています。`Subject`は本コンパイラの論理被写体番号です。

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

台詞があるのに許可がない、許可があるのに台詞がない、又は台詞より前の同じ行に`<Subject N>`がない場合は、常にコンパイルエラーです。肯定的な英語発声指示に同じ行の台詞がない場合は、`speech_guard=strict`ではエラー、`speech_guard=warn`ではComfyUIログへWARNINGを出してJSON生成を続行します。`warn`はAudio参照又は発声許可を自動追加せず、無音指定と発声らしい詳細記述が同居し得るため、MiniMax H3が想定外の人物音声を生成する可能性があります。

検出対象には会話、ナレーション、歌唱に加え、笑い、息を呑む、溜め息、鼻歌、うめき等の人物由来の非言語発声も含みます。風音、足音、衝突音などの環境音・効果音はこの検査の対象外です。

アクティブな話者のSubject定義に`<Audio N>`があるときだけ、独立したAudio定義と`reference`保持分析を生成します。Audioはその話者の明記された台詞の声質と話し方だけに使用し、元音声信号や元の発話内容はコピーしません。台詞のないシーンではSubject定義からAudio句を取り除きます。

### 既存音声によるリップシンク

元のAudio信号を部分的に再利用して人物の口を同期させる場合は、Shot内で「台詞指定」又は「参照音声駆動」のどちらか一方を使います。

Contex-LoopのSource Timeline経路を使う現在のBGM同期ワークフローでは、番号付き`<Audio N>`ではなく次の専用記法を使います。

```text
# シーン 10秒 継続
## ショット
* <Subject 1>が音楽に合わせて身体を動かす。
* リップシンク: <Subject 1> <- ソースボーカル
## 音響
* 発声: ソースボーカルのみ
* ソース音声: 完全維持
```

間奏やイントロではリップシンク行を省略し、次のようにフルミックスだけを維持します。

```text
## 音響
* 発声: なし
* ソース音声: 完全維持
```

この経路では、ロック済みフルミックスをContex-Loopの`source_timeline`へ、同尺・同起点のボーカルステムを`lip_sync_voice`へ接続します。`ソースボーカル`は口形駆動専用で、最終音声へ重ねません。コンパイラは番号付きAudio定義を生成せず、現在の絶対時間位置を維持し、再生成、置換、再開始、再ミックス、リタイミング、ループ、クロスフェード、複製又は追加音声を避ける固定英文を6セクションへ出力します。

Source Timeline経路は、番号付きAudio参照、生成台詞、生成BGM、`BGM再利用`、環境音及び効果音と同一シーンで併用できません。フルミックスとボーカルステムの尺・先頭無音・サンプル位置はワークフロー側で一致させてください。単一のSource Vocalを同一シーン内の複数Subjectへ割り当てることもできません。

台詞が既知で、文字列もH3へ明示する場合は従来の台詞指定形式です。

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

歌詞や台詞を書かず、現在のAudio区間をそのまま同期の正本にする場合は参照音声駆動形式です。

```text
# シーン 8秒
## ショット
* <Subject 1>がカメラを見ながら音楽に合わせて身体を動かす。
* リップシンク: <Subject 1> <- <Audio 1>
## 音響
* 発声: 参照音声のみ
* BGM再利用: <Audio 1> 完全コピー
```

この形式もLLMへ送らず、Pythonが固定英文へ展開します。コンパイラは文字起こしや歌詞推測を行いません。`<Audio 1>`の現在区間だけを発声内容とタイミングの正本にし、音素、口の閉鎖、持続音及びフレーズ境界へ同期し、間奏又は人声のない区間では口を閉じ、シーン境界で曲や歌唱フレーズを先頭から再開しないよう指示します。`発声: 参照音声のみ`に`「...」`や`<d>...</d>`を併用することはできません。

### 既存BGMの再利用とボーカルのリップシンク

既存のBGM Audioを使う場合は、`BGM`の代わりに`BGM再利用`を指定します。

```text
## 音響
* BGM再利用: <Audio 1> 完全コピー
* BGM再利用: <Audio 1> 部分コピー 00:20.000-00:30.000
```

上の2行は選択肢の例であり、同一シーンにはどちらか1行だけを書きます。

関係は次の2種類です。

- `完全コピー`: `<Audio N>`全体を最終音声トラックとして1:1再利用します。元Audio全体の長さと対象シーンの長さが一致する用途向けです。別の環境音、効果音、生成台詞、別Audioのリップシンクは追加できません。
- `部分コピー`: `<Audio N>`のBGM層を視聴者だけに聞こえる音楽として再利用し、環境音、効果音、生成台詞などの別音響層を併用できます。
- `部分コピー MM:SS.mmm-MM:SS.mmm`: 元Audioの指定区間をシーン先頭から末尾へ1:1で割り当てます。終了時刻は開始時刻より後で、区間長はシーン秒数と厳密に一致させます。長い一曲を短い継続シーンへ分割する場合はこちらを推奨します。

BGM内のボーカルへ人物の口を同期させる場合は、同じAudio番号を`リップシンク`にも指定します。正確な歌詞が分かる場合は台詞指定形式を使えます。Suno等の音源で歌詞タイミングを確定しにくい場合は、歌詞を省略した参照音声駆動形式を推奨します。

```text
# サブジェクト
* <Picture 1>を外観参照として使用する歌手。

# シーン 8秒
## ショット
* <Subject 1>がステージ中央でカメラを見ながら音楽に合わせて身体を動かす。
* リップシンク: <Subject 1> <- <Audio 1> 「夜空を越えて、君のもとへ」
## 音響
* 発声: 指定台詞のみ
* BGM再利用: <Audio 1> 部分コピー 00:20.000-00:28.000
```

この例では、`<Audio 1>`の20～28秒の音楽と元ボーカルをシーン全体へ割り当て、映像上の`<Subject 1> (S1)`を歌詞とタイミングへ同期します。別の歌声は生成しません。`発声: 指定台詞のみ`はリップシンクでも必須です。

歌詞を省略する場合は、上例のリップシンク行を`* リップシンク: <Subject 1> <- <Audio 1>`、発声行を`* 発声: 参照音声のみ`へ置き換えます。この2方式は同一シーン内で混在できません。

`リップシンク`バレット自体が歌唱・発声指示を兼ねます。別のバレットへ「歌う」「発声する」などの肯定的な発声指示だけを書かないでください。追加する場合は、身体動作、視線、表情、カメラなど発声以外の演技だけを記述します。翻訳結果に発声語が残った場合、`speech_guard=warn`なら警告付きで通過させられますが、追加発声の抑止は保証されません。

構造化バレットとして予約されるのは、`リップシンク:`又は`リップシンク：`で始まる行だけです。`リップシンク中は顔を明瞭に表示する`のようにコロンを伴わない通常文は、Common、Scene preamble及びShot本文で通常の翻訳対象として使用できます。

`BGM再利用`はLLMへ送らず、PythonがAudio番号、`fully_copy`又は`partially_copy`、任意の元音源時間範囲へ固定変換します。時間範囲を指定した場合、同じ範囲と「再構成、再生成、リタイミング、ループ、再開始及びクロスフェードを行わない」という制約を`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`及び`non_diegetic_music`へ一貫して反映します。生成用`BGM`と`BGM再利用`は同一シーンで併用できません。

54秒の音源を最大10秒の継続シーンへ分ける場合は、各シーンで次の範囲を使用します。

```text
シーン1（10秒）: * BGM再利用: <Audio 1> 部分コピー 00:00.000-00:10.000
シーン2（10秒）: * BGM再利用: <Audio 1> 部分コピー 00:10.000-00:20.000
シーン3（10秒）: * BGM再利用: <Audio 1> 部分コピー 00:20.000-00:30.000
シーン4（10秒）: * BGM再利用: <Audio 1> 部分コピー 00:30.000-00:40.000
シーン5（10秒）: * BGM再利用: <Audio 1> 部分コピー 00:40.000-00:50.000
シーン6（4秒）:  * BGM再利用: <Audio 1> 部分コピー 00:50.000-00:54.000
```

これはH3へ元音源の使用位置と非再構成を強く指示する機能であり、波形の同一性を保証するものではありません。完全に同じBGMが必要な場合は、生成映像を連結した後にH3生成音声を元WAVへ差し替えてください。

### 音響

`## 音響`はシーンローカルの許可リストです。省略した項目は無効です。全項目を省略したシーンは`overall_soundscape:\nComplete silence.`になります。空の`## 音響`はエラーです。

```text
## 音響
* 環境音: 草原を吹く弱い風音。
* 効果音: 足音と衣服の擦れ音。
* 発声: なし
* BGM: ゆっくりしたピアノと低い弦楽器。終盤で徐々に音量を下げる。
```

各項目は1回までです。環境音、効果音及び生成用BGMは任意文又は`なし`、発声は`なし`、`指定台詞のみ`、`参照音声のみ`又は`ソースボーカルのみ`だけを使えます。`指定台詞のみ`は保護台詞付きリップシンク又は通常台詞専用、`参照音声のみ`は番号付き歌詞なしリップシンク専用、`ソースボーカルのみ`はSource Timeline駆動リップシンク専用です。環境音、効果音及び生成用BGMへ`<Audio N>`や台詞は書けません。既存Audioは`BGM再利用: <Audio N> 完全コピー|部分コピー [MM:SS.mmm-MM:SS.mmm]`、Source Timelineは`ソース音声: 完全維持`で指定します。

`BGM`は登場人物に聞こえず、視聴者だけに聞こえる非ダイジェティック音楽です。楽器、テンポ、リズム、音量変化を具体的に記述します。劇中で人物にも聞こえる音楽はShot本文へ書いてください。

生成用`BGM`、`BGM再利用`及び`ソース音声`を全て省略するか`BGM: なし`とすると`non_diegetic_music:\nN/A`になります。生成用BGMは英訳し、BGM再利用はAudio番号とコピー関係、`ソース音声: 完全維持`はロック済みSource Timelineフルミックスの連続維持を`non_diegetic_music`へ出力します。MiniMax H3によるBGM生成はランダム性が高いため、再現性や楽曲品質を優先する場合は従来どおりSunoなど別の音楽生成AIで作り、動画生成後に追加する運用も推奨します。

## JSON出力

トップレベル`prompt_prefix`には`# 共通プロンプト`の英訳を改行区切りの一文字列として格納します。Commonを省略した場合だけ空文字列です。各生成シーンの`prompt`配列は、公式Full-Reference形式に合わせた次の6文字列を厳密にこの順で持ちます。

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

出力例です。英訳表現はモデルによって多少変わります。

```json
{
  "prompt_prefix": "A bright modern office district is rendered in a vivid 2D anime style.\nKeep the lighting consistent across all scenes.",
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
        "detailed_description:\n[Shot 1] <Subject 1> (S1) says <d>[Japanese]ようこそ！</d>.\nFor <Subject 1> (S1)'s explicitly specified dialogue in this shot, use <Audio 1> only as a voice-timbre and delivery reference; do not copy or introduce any other speech from the source audio.\n[Shot 2] At 00:04.500, the camera slowly approaches <Subject 1>.",
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

各Shotの箇条書きは`detailed_description`内でも元順序の改行を維持します。JSONには隣接文字列を連結する`"foo" "bar"`構文や複数行文字列構文がないため、一つの文字列内の改行はJSONテキスト上で`"foo\nbar"`と表現されます。JSONを解析した後の値には実際の改行が入っています。

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
| `retry_max` | 検証失敗後の最大再試行回数。既定10、`0`はなし、`-1`は成功又は中断まで無制限です。長文でも検証済み区間は保持し、未解決区間だけを再送します。 |
| `save_debug_output` | 中間情報をComfyUIのoutput下へ保存します。既定`False`。 |
| `speech_guard` | 保護台詞のない肯定的発声語の扱い。`strict`（既定）はエラー、`warn`はWARNINGを記録してJSON生成を続行します。 |

モデルファイル又は`gpu_layers`、`n_batch`、`n_ctx`、`flash_attn`、`kv_cache_type`、`op_offload`が変わると再ロードします。`speech_guard`を含む生成・検証パラメータだけの変更では保持中モデルを再ロードしません。

## 翻訳と再試行

文書全体のディレクティブ、各箇条書き先頭、参照タグ、日本語台詞を索引付きプレースホルダへ置き換え、残る日本語本文を保護翻訳ストリームとして送ります。実効コンテキストへ収まり、翻訳区間が64件以下なら文書全体を1回で推論します。65件以上又はコンテキストへ収まらない場合は、箇条書きを途中分割せず、1バッチ最大64区間として複数回に分けます。これにより長い単一応答の停止や全体再試行の負担を抑えます。話者IDは翻訳後のJSON生成時にPythonが付与します。

翻訳推論はllama.cppのストリーミング応答を使用し、生成済み出力チャンク数を`max_tokens`に対する概算進捗としてComfyUIのノード上へ表示します。内部カウンターは毎チャンク更新しますが、長文生成でUIイベントを詰まらせないよう画面への進捗通知は最大4回/秒です。応答が上限より前に終了する場合があるため、バーは厳密な残り時間を示すものではなく、検証成功時に完了位置へ進みます。再試行又は次のバッチへ移ると、新しい推論としてバーが先頭から始まります。

推論中は約10秒ごとに、バッチ、attempt、経過秒、受信済みストリームチャンク数、最終チャンクからの経過時間及び進捗callbackの実行状態を通常ログへ出力します。入力評価中など、まだ最初の出力チャンクが得られていない場合も`streamed_chunks=0`のハートビートで処理中であることを確認できます。`progress_callback_active=true`で停止していればUI通知内、`false`ならllama.cppから次のチャンクを待っている状態です。プロンプト本文や生成途中の翻訳文は通常ログへ出しません。

Qwen3ではユーザーメッセージ末尾の`/no_think`に加え、llama-cpp-pythonのchat APIがhard switchを公開しない環境でも、空のthinking assistant prefillを生成開始位置へ与えてthinkingそのものを抑止します。

応答では構造と保護プレースホルダの個数・順序・所有区間、コードフェンス、thinking、日本語残留などを検証します。正常に閉じた完全な`<think>...</think>` blockは位置にかかわらずQwenの制御出力として除去しますが、未閉鎖タグは推測して削除しません。応答全体の検証に失敗しても、余分な前置きや局所的な構造破損から独立して境界を確定できる正常区間を個別回収し、未解決区間だけを新しいseedで`retry_max`まで再送します。再送時は不透明な参照プレースホルダへ標準参照タグを一時注釈し、Qwenが`<Subject N>`等を代名詞として省略し続ける現象を抑えます。原文の文頭参照だけがなお省略された場合は、文境界が一意な場合に限って決定論的に復元します。

`save_debug_output=True`では、実行ごとのディレクトリを`ComfyUI/output/cl_japanese2json_debug/`へ作り、`source.md`、system prompt、保護要求、LLM生応答、検証メタデータ、成功時の`canonical.md`と`result.json`、失敗時の`error.txt`を保存します。入力内容を含むため共有前に確認してください。`ComfyUI/input`へは書きません。

system promptは`prompts/llmj2e_qwen3_8b_system_prompt.txt`からUTF-8で読み込みます。変更はComfyUIキャッシュ指紋へ反映されます。

## Contex-Loop Planへの接続

`json_text`をMiniMax H3 Contex-Loop Planノードの`plan_json_input`へ接続します。出力は説明やコードフェンスを含まない1個の`STRING`で、`json.loads()`による再検証済み、末尾は1個のLFです。

## ボーカルステムからScene・SRTを生成

`CL Vocal to Prompt Segments`へ、フルミックスと同じ開始時刻・同じ全長で書き出したボーカルステムと、Suno Lyrics形式のテキストを接続します。`[Intro]`、`[Verse]`、`[Chorus]`等の行は見出しとして無視され、それ以外の歌詞行をWhisperの単語時刻へ上から順に対応付けます。

```text
Load Audio (vocal stem) ──> CL Vocal to Prompt Segments.vocal_audio
Suno Lyrics STRING ───────> CL Vocal to Prompt Segments.lyrics_text

CL Vocal to Prompt Segments.prompt_text ──> CL Japanese to JSON (GGUF).plain_text
CL Vocal to Prompt Segments.srt_text ──────> Preview/Save Text
```

主な設定は次のとおりです。

| 名前 | 意味 |
| --- | --- |
| `whisper_model` | `ComfyUI/models/whisper/`以下から再帰検出したローカル`.pt`。自動ダウンロードしません。 |
| `language` | `ja`（日本語）、`en`（米国英語を含む英語）、又はWhisper言語検出を使う`auto`。Whisperでは`us`ではなく`en`を使用します。 |
| `device` | `auto`、`cuda`、`cpu`。明示`cuda`が利用不能ならエラーです。 |
| `keep_whisper_loaded` | 同じモデルとdeviceを次回も再利用します。 |
| `max_scene_seconds` | 1 Sceneの最大整数秒。既定10秒です。 |
| `silence_threshold_dbfs` | 解析窓を有声候補とみなすRMS閾値。分離残留音が多い場合は0へ、弱い歌声を取りこぼす場合は-100へ近づけます。 |
| `analysis_window_ms` | PCM有声判定の解析窓。 |
| `min_voiced_ms` | これ未満の孤立した有声候補を除去します。 |
| `min_silence_ms` | これ未満の有声区間間の無音を結合します。 |
| `voice_padding_ms` | 確定有声区間の前後余白です。 |
| `lyrics_match_threshold` | Lyrics行とWhisper候補を確定する最小類似度。低くすると解決数と誤対応の両方が増えます。 |
| `lyrics_neighbor_threshold` | 両隣が通常閾値で確定した未解決行だけに使う救済閾値。既定0.45で、通常閾値以下に設定します。 |
| `lyrics_search_seconds` | 現在の歌詞位置から次行を探す最大時間です。 |

出力は次の4つです。

- `prompt_text`: 現行コンパイラへそのまま入力できる編集用テンプレート。有声SceneだけにSource Vocalリップシンクを設定し、解決済み歌詞を`// 歌詞`として記録します。
- `srt_text`: Lyrics原文を本文、Whisperを時刻根拠とするSRT。未解決行は推測せず省略します。
- `segments_json`: サンプル精度の検出区間、照合スコア、整数秒Scene及び末尾パディング量を含む検証用JSON。
- `status`: 解決数、Scene数、入力尺及び必要パディング量の1行要約。

冒頭歌詞の認識脱落を減らすため、セクション見出しを除く先頭Lyricsを最大12行・160文字だけWhisperの最初の復号ヒントに使用します。全文を渡したり後続窓へ反復したりはしません。通常閾値で未解決になった行は、前後の歌詞が確定して検索範囲を安全に限定できる場合だけ`lyrics_neighbor_threshold`で再照合します。

生成されたテンプレートのSubject、画風、背景、人物動作及びカメラワークは用途に合わせて編集してください。歌唱の母音伸長、コーラス、重唱、リバーブ及び分離残留によりWhisper時刻はずれることがあるため、SRTは行単位の初期同期データとして確認してください。未解決LyricsはコメントとSRTへ出力されませんが、`segments_json`に`unresolved`として残り、最良のWhisper候補と候補時刻も診断用に確認できます。

テンプレートの`ソースボーカル`は`<Audio 1>`ではなく、Contex-LoopのSource Vocal入力です。ボーカルステムを最終音声へ重ねず、ロック済みフルミックスをSource Timelineとして維持します。入力末尾が整数秒でない場合は`status`の不足量に従い、フルミックスとボーカルステムを`CL Audio Pad`の`pad_position=end`で同じ計画尺まで補完してください。

## PCM無音パディング

`CL Audio Pad (PCM Silence)`は、汎用の`Load Audio`とH3又はContex-Loopの間へ挿入します。Python側で必要サンプル数を求め、元波形と同じdtype、デバイス、バッチ、チャンネル及びサンプルレートのままPCM値`0.0`を追加します。元音声は切断、リサンプル又は音量変更しません。

推奨接続は次のとおりです。

```text
Load Audio ── audio ───────────────┐
                                   v
Contex-Loop Plan ── plan ──> CL Audio Pad ── padded_audio ──┬─> Loop Start.source_audio
                                                            ├─> Current.source_audio
                                                            └─> Assemble.source_audio
```

Contex-Loop Planを接続すると、`total_delivered_frames / fps`から必要な音声サンプル数を自動計算します。Math、Empty Audio、Concatenate Audioノードは不要です。Loop Start、Current及びAssembleには必ず同じ`padded_audio`を渡してください。別々の音声を渡すと、音声ハッシュ不一致又は長さ不足になります。

| 名前 | 意味 |
| --- | --- |
| `target_duration_seconds` | Planを接続しない場合の最小出力尺。`0`は無効です。Planと併用した場合は長い方を採用します。 |
| `extra_padding_seconds` | 必要尺を満たした後へ加える安全マージン。PlanもUI目標もない場合は固定パディング秒数になります。 |
| `pad_position` | `end`（既定）、`start`、`both`。リップシンクでは原音開始を動かさない`end`を使用します。 |
| `plan` | 任意の`H3_CHAIN_PLAN`。接続時は完成フレーム数に自動追従します。 |
| `match_audio` | 任意の基準AUDIO。その継続時間を最小出力尺に追加します。波形は混合せず、短い入力だけを無音補完します。 |

出力にはパディング済みAUDIOのほか、元尺、出力尺、追加秒数及び状態文字列があります。音源が既に十分長く、追加マージンも0なら入力をそのまま返します。最終Assembleで`audio_source: source`を使うと、Planを超える安全マージンだけが動画尺で切られ、元音源部分は維持されます。

フルミックスとボーカルステムを`MiniMax H3 Audio Tracks`へ渡す場合は、フルミックスを基準尺として次のように接続します。ステム分離による数百ミリ秒の末尾差があっても、短いボーカルだけがPCM値`0.0`で延長されます。

```text
Full Mix Load Audio ──> Full Mix CL Audio Pad.audio
Contex-Loop Plan ─────> Full Mix CL Audio Pad.plan
Full Mix CL Audio Pad.padded_audio ──┬─> MiniMax H3 Audio Tracks.full_mix
                                     └─> Vocal CL Audio Pad.match_audio

Vocal Load Audio ──┬─> Vocal CL Audio Pad.audio
                   └─> MiniMax H3 Lip-Sync Options.voice
Vocal CL Audio Pad.padded_audio ───────> MiniMax H3 Audio Tracks.vocals
```

Vocal側は`pad_position=end`、`extra_padding_seconds=0`にします。両`CLAudioPad`を相互に`match_audio`接続しないでください。また、Planから補完したボーカルをLip-Sync Options経由でGeneration Profileへ戻すと循環するため、Lip-Sync Optionsには元のボーカルを直接接続します。入力音声が基準より長い場合は切り詰めないため、基準にするフルミックス自体が最長であることを確認してください。

パディング処理のログは翻訳処理と区別できるよう、`[cl_audiopad]`接頭辞で出力されます。

## 主なエラー

- GGUFがない: `ComfyUI/models/LLM/GGUF`以下へモデルを置き、ComfyUIを再起動します。
- Whisperモデルがない: OpenAI Whisperのローカル`.pt`を`ComfyUI/models/whisper`以下へ配置し、ComfyUIを再起動します。
- OpenAI Whisperをimportできない: ComfyUIのPythonへ`openai-whisper`をユーザー自身で導入します。同名の別パッケージは`load_model()`を持たないため使用できません。
- `llama-cpp-python`をimportできない: ComfyUIのPythonへ適切な版を手動導入します。
- `Windows Error 0xc000001d`: 実行CPUが対応しない命令を含むwheelの可能性があります。上記の`GGML_NATIVE=OFF`、AVX-512/AMX無効化設定でリビルドします。
- コンテキスト不足: `n_ctx`又は`max_tokens`を見直します。
- プレースホルダ欠落: 未解決区間だけが再送され、参照プレースホルダには自動的に意味注釈が付きます。なお繰り返し失敗する場合は、標準的なinstruction-tuned GGUFを試すか、`save_debug_output=True`で生応答を確認します。`retry_max`を増やすだけでは恒常的な省略が直らない場合があります。
- 台詞エラー: 同じショット行で台詞より前に`<Subject N>`を書き、`「...」`と発声動詞を記述し、シーン末尾で`発声: 指定台詞のみ`を許可します。`(Sx)`は入力しません。視覚的比喩などが英語発声語として誤検出される場合は、意味を視覚表現へ書き換えるか、`speech_guard=warn`で想定外の発声リスクを承知して続行できます。
- リップシンクエラー: Shot内で、台詞指定なら`リップシンク: <Subject N> <- <Audio N> 「正確な台詞」`と`発声: 指定台詞のみ`、歌詞なしなら`リップシンク: <Subject N> <- <Audio N>`と`発声: 参照音声のみ`を対にします。2方式の混在と同一Audioの役割競合も確認します。
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
