# cl_japanese2json 仕様書

## 1. 目的

本仕様は、日本語の縮小版MarkdownをMiniMax H3 Full-Reference形式のContex-Loop Plan JSONへ変換するコンパイラ`cl_japanese2json`を定義する。

処理は次の3段階からなる。

1. LLMJ2E: 日本語Markdownを字句解析し、通常文章だけをUS Englishへ翻訳する。
2. MDPARSE: 正規形英語Markdownを型付き中間表現へ変換する。
3. JSONGEN: 中間表現から厳格なPlan JSONを決定論的に生成し、再検証する。

LLMは最終JSON、ディレクティブ、ショット構造、参照関係、話者ID、保持関係を決定しない。これらはPythonが処理する。

本版はドラフトの破壊的改訂であり、ユーザーが記述する旧来の`(Sx)`話者ID、暗黙ショット、`N秒生成する`、`継続する`との後方互換性を持たない。

## 2. 対象範囲

対象は次のとおりである。

- UTF-8日本語縮小版Markdown
- Cスタイルの行コメント及びブロックコメント
- Subject、Picture、Video、Audio参照タグ
- 日本語ダイレクトスピーチ
- 既存Audio信号を部分コピーする明示的リップシンク
- 既存BGM Audioの`fully_copy`又は`partially_copy`、元音源時間範囲の1:1割当てと、そのボーカルへのリップシンク
- Subject番号からPythonが生成する安定した`(Sx)`話者ID
- シーンとシーン内ショット
- Sceneへ条件付き適用するグローバル共通プロンプト
- シーンローカルな音響許可リスト
- シーンローカルな非ダイジェティックBGM生成又はAudio再利用指定
- Subject単位のグローバル保持分析規則
- MiniMax H3 Full-Referenceの6セクション
- Contex-Loopの継続・リセット設定

対象外は次のとおりである。

- LLMによる最終JSON生成
- LLMによる保持マーカー、話者ID、ショット時刻の推測
- `llama-cpp-python`の自動インストール又は更新
- GGUFの自動ダウンロード
- ComfyUI本体又は他の`custom_nodes`の変更
- Environment又はSound effectsのAudio参照・Audio再利用
- Audio内容の自動文字起こし又は台詞推測
- 旧入力構文の互換変換

## 3. ファイル構成

```text
compiler/
  comments.py
  errors.py
  structures.py
  protected_text.py
  llmj2e.py
  mdparse.py
  jsongen.py
prompts/
  llmj2e_qwen3_8b_system_prompt.txt
tests/
```

`comments.py`はコメント走査、`structures.py`は中間表現、`protected_text.py`は保護と復元、`llmj2e.py`は字句解析・翻訳、`mdparse.py`は正規形パース、`jsongen.py`はPlan生成と最終検証を担当する。

## 4. 文字列共通規則

- 入力はPython`str`である。
- UTF-8として扱う。先頭BOMは許容する。
- LF、CRLF、最終改行の有無は意味を変えない。
- ディレクティブと箇条書きは行頭から始める。
- 箇条書きマーカーはASCIIの`* `である。
- 同一ブロックのディレクティブと箇条書きの途中へ空行を置かない。
- 空行はブロック間の区切りとして使用できる。
- 未知のディレクティブ、未知のサブディレクティブ、非箇条書き本文はエラーである。

### 4.1 コメント

入力は次のCスタイルコメントを使用できる。

```text
// 物理行全体のコメント

/* 単一行のブロックコメント */

/*
複数行のブロックコメント
*/
```

- `//`は、空白を除いて物理行の先頭にある場合だけ行末までをコメントとする。
- 通常本文の後ろにある`//`及びURL中の`//`はコメントを開始しない。
- `/*`は通常領域の任意位置でブロックコメントを開始し、最初の`*/`で終了する。
- ブロックコメントは行内又は複数行にできるが、入れ子は禁止する。
- 未閉鎖`/*`、入れ子の`/*`及び対応しない`*/`は`CommentSyntaxError`とする。
- `「...」`及び`<d>...</d>`内部のコメント区切りは台詞本文としてそのまま保持する。
- コメント内部のディレクティブ、箇条書き、参照タグ、話者ID及び台詞らしい文字列は意味を持たない。
- コメント文字は空白へ置換し、改行数及び後続要素の元の行番号を保持する。
- コメントだけの行は構文上透明であり、ディレクティブと所属する箇条書きの間にあってもブロックを分断しない。
- コメント除去はディレクティブ解析、参照保護、日本語台詞保護及び翻訳区間作成より前に行う。
- HTMLコメント`<!-- ... -->`は対応せず、`CommentSyntaxError`とする。

## 5. 日本語縮小版Markdown

### 5.1 文書順序

文書の順序は次のとおりである。

```text
[# サブジェクト]
[# 保持分析]
[# 共通プロンプト]
# シーン ...
...
```

- `# サブジェクト`は0又は1回。
- `# 保持分析`は0又は1回。存在する場合はSubjectの後、最初のSceneより前。
- `# 共通プロンプト`は0又は1回。存在する場合は、Subject又はRetentionも存在するならその後、最初のSceneより前。
- `# シーン`は1～128回。
- Subject、Retention、Commonの繰り返し、順序違反又はScene開始後の出現はエラー。

### 5.2 Subject

```text
# サブジェクト
* <Picture 1>を外観参照、<Audio 1>を明記された台詞の声質参照として使用する人物。
* <Picture 2>を外観参照として使用する人物。
```

箇条書き順に`<Subject 1>`、`<Subject 2>`を割り当てる。LLM翻訳結果は、後続の`subject_definitions`で`<Subject N> is ...`の右辺として使える単数形の英語名詞句とし、ASCIIピリオドで終える。

Subject定義の`<Picture N>`と`<Video N>`は出典を表す。別の独立参照として使用しない限り、独立したPicture/Video定義を追加しない。

### 5.3 Retention

```text
# 保持分析
* <Subject 1> 完全に保持: <Picture 1>由来の顔、髪型、髪色及び衣装を維持する。
* <Subject 2> 部分的に保持: 衣装を維持し、照明と質感は変更する。
* <Subject 3> 属性転送 -> <Subject 1>: <Picture 2>由来のフォトリアルな照明と質感を<Subject 1>へ転送する。
* <Subject 4> 弱い参照: <Picture 3>の構図と雰囲気だけを参照する。
```

構文は次である。

```text
* <Subject N> 完全に保持: 説明
* <Subject N> 部分的に保持: 説明
* <Subject N> 属性転送 -> <Subject M>: 説明
* <Subject N> 弱い参照: 説明
```

日本語マーカーは翻訳せず、JSONGENで次の固定値へ写像する。

| 入力 | 正規形及び出力 |
| --- | --- |
| 完全に保持 | `fully_preserved` |
| 部分的に保持 | `partially_preserved` |
| 属性転送 | `attribute_transfer` |
| 弱い参照 | `weak_reference` |

- コロンは`:`又は`：`を許容する。
- Subjectごとに規則は1個まで。
- 参照元Subjectは定義済みでなければならない。
- `attribute_transfer`だけが転送先を取る。
- 転送元と転送先は異なり、両方とも定義済みでなければならない。
- 各シーンでは、そのシーンにアクティブなSubjectの規則だけを使用する。
- アクティブな属性転送元の規則を適用する場合、転送先も同じシーンでアクティブでなければならない。
- 明示規則のないアクティブSubjectは`fully_preserved`へフォールバックする。

### 5.4 Common

```text
# 共通プロンプト
* 明るい昼の近代的なオフィス街を鮮やかな2Dアニメ調で描く。
* <Subject 1>と<Subject 2>の外観を混同しない。
```

Commonはグローバルに宣言するが、各Sceneの`detailed_description`へ次の規則で行単位に適用する。

- Subject又はAudio参照を含まない行は全Sceneへ適用する。
- `<Subject N>`を含む行は、その行が参照する全SubjectがScene preamble又はShot本文でアクティブな場合だけ適用する。
- `<Audio N>`を含む行は、その行が参照する全AudioがSceneの声質参照、リップシンク又はBGM再利用として別途アクティブな場合だけ適用する。
- SubjectとAudioの両方を含む行は、両方の条件を満たす場合だけ適用する。
- Commonの参照だけではSubject又はAudioをアクティブにしない。
- CommonのAudio参照は正規形の`<Audio 1>`～`<Audio 3>`だけを許可する。
- 適用後の順序はCommon、Scene preamble、Shot本文とする。
- ダイレクトスピーチ又は`(Sx)`話者IDを書けない。肯定的な英語発声指示はJSONGENの`speech_guard`で検査する。
- Commonは1個以上の箇条書きを持つ。

### 5.5 Scene

```text
# シーン [N秒] [継続]
```

- 秒数は省略可能で既定5秒。
- 明示値は1～60の10進整数。
- `継続`は省略可能。
- 正規例は`# シーン`、`# シーン 8秒`、`# シーン 継続`、`# シーン 8秒 継続`。
- `生成する`又は`継続する`を含む旧形式はエラー。
- 1件目のSceneに`継続`があり外部コンテキストがない場合は警告し、非継続へ変更する。
- Sceneには少なくとも1個の明示的なShotが必要。

Sceneディレクティブ直下から最初のShotまでの箇条書きはScene preambleである。これはFull-Referenceの`detailed_description`における`[Shot 1]`より前の文章となり、シーン全体のスタイル、環境、前提を書く。ダイレクトスピーチは書けない。

### 5.6 Shot

```text
## ショット
* 最初のショット本文。
## ショット 3.25秒
* 2番目のショット本文。
```

- Scene内の最初のShotは`## ショット`であり、開始時刻は0秒。
- 最初のShotへ時刻を書いてはならない。
- 2個目以降は`## ショット N秒`を必須とする。
- Nは0以上の整数又は小数1～3桁。
- 開始時刻は前Shotより大きく、Scene durationより小さい。
- 各Shotは1個以上の箇条書きを持つ。
- ShotはSoundscapeより前に置く。

2個目以降はJSON内で`[Shot N] At MM:SS.mmm, ...`へ変換する。ここで時刻はシーン先頭からの経過時刻である。

Shot内では、既存Audio信号を人物の発声として部分コピーし、口の動きを同期させる次の構造化バレットを使用できる。

```text
* リップシンク: <Subject 1> <- <Audio 1> 「こんにちは、よろしくお願いします。」
```

- 構文は`* リップシンク: <Subject N> <- <Audio N> 台詞`である。コロンは`:`又は`：`を許容する。
- Subjectは1～4、Audioは1～3の正規番号とする。
- 末尾には日本語鉤括弧又は既存`<d>...</d>`による、空でない正確な台詞を1個だけ必須とする。
- リップシンクバレットはLLMへ送らず、Pythonが正規形`Lip sync: <Subject N> <- <Audio N>: <d>...</d>`へ変換する。
- 構造化リップシンクとして認識するのは、本文が`リップシンク:`又は`リップシンク：`で始まるバレットだけとする。`リップシンク中は...`等のコロンを伴わない通常文は専用構文として扱わない。
- JSONGENは元Audio信号を使う`audio reuse`として扱い、Audio保持関係を`partially_copy`とする。
- 同じAudioを同一Scene内で声質参照と信号再利用へ同時に割り当ててはならない。
- 同じAudioを同一Scene内の複数Subjectへ割り当ててはならない。
- 通常台詞と同様に、Sceneの`発声: 指定台詞のみ`を必須とする。
- コンパイラはAudioから文字起こしを行わず、記載された台詞を正本とする。
- リップシンクバレット自体が発声又は歌唱指示を兼ねる。別行へダイレクトスピーチのない肯定的な発声又は歌唱指示を追加すると、未指定音声を防ぐ既存規則によりエラーとする。

### 5.7 Soundscape

```text
## 音響
* 環境音: 草原を吹く弱い風音。
* 効果音: 足音と衣服の擦れ音。
* 発声: 指定台詞のみ
* BGM: ゆっくりしたピアノと低い弦楽器。終盤で徐々に音量を下げる。
```

既存BGMを再利用する場合は、`BGM`の代わりに次を使用する。

```text
* BGM再利用: <Audio 1> 完全コピー
* BGM再利用: <Audio 2> 部分コピー
* BGM再利用: <Audio 3> 部分コピー 00:20.000-00:30.000
```

上の3行は選択肢の例であり、同一Sceneにはいずれか1行だけを書く。

- Soundscapeは各Sceneに0又は1回。
- 全Shotの後へ置く。
- 空のSoundscapeはエラー。
- 項目は`環境音`、`効果音`、`発声`、`BGM`、`BGM再利用`を各0又は1回。
- `BGM`と`BGM再利用`は相互排他。
- 環境音、効果音及び生成用BGMは任意の日本語本文又は`なし`。
- 発声は`なし`又は`指定台詞のみ`だけ。
- 項目を省略した場合、その音響層は無効。
- Soundscape全体を省略した場合、人物発声、環境音、効果音、BGM生成及びBGM再利用を全て無効化する。
- 環境音、効果音及び生成用`BGM`には`<Audio N>`又はダイレクトスピーチを書けない。Audio再利用は構造化された`BGM再利用`だけで指定する。
- BGMは登場人物には聞こえず視聴者だけに聞こえる非ダイジェティック音楽である。劇中で人物に聞こえる音楽はShot本文へ書く。
- BGM本文は楽器、テンポ、リズム及び音量変化を具体的に記述し、台詞又は具体的な歌詞を書かない。
- `BGM再利用`のAudioは1～3の正規番号で、関係は`完全コピー`又は`部分コピー`だけを許可する。このバレットはLLMへ送らず、Pythonが`Background music reuse: <Audio N> fully_copy|partially_copy [MM:SS.mmm-MM:SS.mmm]`へ変換する。
- `完全コピー`は元Audio全体を最終音声トラックとして1:1再利用する。元Audio全体とSceneの長さが一致する用途を前提とし、時間範囲は指定できない。この場合、別のEnvironment、Sound effects、生成台詞又は別Audioのリップシンクを同じSceneへ追加できない。同じAudio内のボーカルに対するリップシンクは追加音声ではないため許可する。
- `部分コピー`はBGM層をコピーしながら、Environment、Sound effects又は別の許可済み音声層を混在できる。
- `部分コピー`には任意で`MM:SS.mmm-MM:SS.mmm`形式の元音源時間範囲を1個指定できる。分は2桁以上、秒は`00`～`59`、ミリ秒は3桁、区切りはASCIIハイフンとする。
- 時間範囲の終端は始端より後でなければならず、範囲長はScene durationとミリ秒単位で厳密に一致しなければならない。範囲は対象Sceneの先頭から末尾へ1:1で割り当てる。
- 時間範囲付き部分コピーでは、元区間の音楽、ボーカル、編曲、楽器構成、テンポ、リズム、タイミング及び内部ミックスを保持し、再構成、再生成、画風変更、リタイミング、ループ、再開始及びクロスフェードを行わない固定英文をJSONの関連セクションへ出力する。
- BGMボーカルへ同期する場合、Shot内の`リップシンク`と`BGM再利用`へ同じAudio番号を指定し、正確な歌詞をリップシンク側へ記載する。
- SoundscapeはScene内部へ格納し、他Sceneへ継承しない。

## 6. 保護対象

### 6.1 参照タグ

```text
<Picture 1>～<Picture 9>
<Video 1>～<Video 9>
<Audio 1>～<Audio 3>
<Subject 1>～<Subject 4>
```

タグ名と番号間のASCII空白は必須である。範囲外又は`<Picture1>`のような非正規タグは警告するが、内容自体は保護して復元する。

### 6.2 話者ID

`(Sx)`は入力構文ではなくJSONGENが生成する内部表現である。ユーザー入力及び正規形Markdownのどの箇所に`(Sx)`があってもエラーとし、翻訳用プレースホルダの保護対象にはしない。

- 各ダイレクトスピーチの直前にある、同じ行で最も近い`<Subject N>`を話者とする。
- 話者IDはSubject番号と同一にし、`<Subject N>`へ`(SN)`を割り当てる。登場順又は発声順では変化しない。
- JSONGENは実際の台詞位置、対応するAudio定義及びAudio利用説明だけへ話者IDを生成する。
- 通常の動作参照及び`retention_analysis`へ話者IDを生成しない。
- 同じ行で台詞より前にSubject参照がなければエラーとする。

### 6.3 ダイレクトスピーチ

日本語鉤括弧を次へ変換する。

```text
「こんにちは」
```

```text
<d>[Japanese]こんにちは</d>
```

既存の`<d>...</d>`も1単位として保護する。`<`、`>`、`[`、`]`はダイレクトスピーチ内部で1回だけバックスラッシュエスケープする。

次はエラーである。

- 未閉鎖又は余分な`「`、`」`
- 鉤括弧の入れ子
- 未閉鎖又は余分な`<d>`、`</d>`
- `<d>`の入れ子

### 6.4 保護順序

各箇条書き本文について次の順に処理する。

1. 既存`<d>...</d>`
2. 参照タグ
3. 日本語鉤括弧台詞

各要素を区間固有の`CLJ...X`プレースホルダへ置換する。復元時は全プレースホルダがバイト単位で完全一致し、各1回でなければならない。

## 7. LLMJ2E

LLMJ2Eは最初に4.1のコメント走査を行い、コメントを翻訳レコード又はLLM要求へ含めない。コメント構文エラーはモデル推論前に返す。

### 7.1 正規形ディレクティブ

| 日本語 | 正規形 |
| --- | --- |
| `# サブジェクト` | `# Subjects` |
| `# 保持分析` | `# Retention` |
| `# 共通プロンプト` | `# Common` |
| `# シーン 8秒 継続` | `# Scene 8sec CONTINUE` |
| `## ショット 3.25秒` | `## Shot 3.25sec` |
| `## 音響` | `## Soundscape` |

Retentionの固定マーカー、リップシンクバレット、BGM再利用バレット、Soundscapeのラベルと固定値もPythonが正規化する。

### 7.2 翻訳ストリーム

文書ディレクティブと各翻訳区間を索引付き構造プレースホルダへ変換する。区間種別は次を用いる。

- `SUB`: Subject
- `RET`: Retention説明
- `COM`: Common
- `SCN`: Scene preamble及びShot本文
- `SND`: Environment、Sound effects又はBackground music

BGM再利用は固定構造としてPythonが変換し、`SND`翻訳区間へ含めない。

参照タグと台詞はさらに区間固有の保護プレースホルダとなる。構造化JSON転送は用いず、1本の生テキストストリームを`TRANSLATION_STREAM_BEGIN`と`TRANSLATION_STREAM_END`の間へ置く。話者IDは翻訳後に生成するためストリームへ含めない。

実効コンテキスト長に文書全体が収まる場合、推論要求は1回である。収まらない場合だけレコード境界でバッチ分割し、1レコードを分割しない。

### 7.3 システムプロンプト

システムプロンプトは`prompts/llmj2e_qwen3_8b_system_prompt.txt`からUTF-8で読み込む。

最低限、次をモデルへ要求する。

- US Englishへの翻訳だけを行う。
- JSON、Markdown fence、説明、推論を返さない。
- 全`CLJT...X`及び`CLJ...X`を翻訳、変更、移動、複製、削除しない。
- SUBは`<Subject N> is`の右辺となる単数名詞句にする。
- RET、COM、SCN、SNDは自然で簡潔な英語プロンプト文にする。
- 保護された参照タグと台詞を変更しない。
- `/no_think`を翻訳対象に含めない。

Qwen3では可能な場合、APIの`enable_thinking=False`等も使用する。正常に閉じた先頭`<think>...</think>`を1個だけ除去できる。途中、複数、未閉鎖又は翻訳本文のないthinkingはエラーである。

### 7.4 翻訳検証

次を検証する。

- 応答が存在し、`finish_reason`が長さ上限終了でない。
- コードフェンス又は不正thinkingがない。
- 構造プレースホルダの個数と順序が正しい。
- 各区間固有の保護プレースホルダが元区間内に各1回ある。
- 他区間の保護プレースホルダが混入しない。
- 通常本文が空でない。
- 保護したダイレクトスピーチ外に日本語が残らない。

構造マーカーを全て省略した応答は、段落数又は非空行数が区間数と完全一致するときだけ厳格な位置対応で検証する。長文の部分破損では、一意な保護プレースホルダをアンカーとして安全に確定できる区間だけを保持できる。

### 7.5 再試行

初回失敗後、検証済み区間を保持して未解決区間だけを再送する。再試行seedは初期seedと試行番号から決定論的に変える。

- `retry_max=0`: 再試行なし。
- 正数: 指定回数まで。
- `retry_max=-1`: 成功、バックエンドエラー又はComfyUI中断まで上限なし。

バックエンド例外は検証失敗として無限再試行しない。上限到達後は破損結果を返さず`TranslationError`とする。

## 8. 正規形英語Markdown

例を示す。

```text
# Subjects
* a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.

# Retention
* <Subject 1> fully_preserved: The face, hairstyle, hair color, and clothing from <Picture 1> are retained.

# Common
* A bright modern office district is rendered in a vivid 2D anime style.

# Scene 8sec
* The camera frames the character from the front.
## Shot
* <Subject 1> says <d>[Japanese]ようこそ！</d>.
## Shot 4.5sec
* Lip sync: <Subject 1> <- <Audio 2>: <d>[Japanese]次の台詞です。</d>
## Soundscape
* Environment: Distant city ambience.
* Vocalization: EXPLICIT_DIALOGUE_ONLY
* Background music reuse: <Audio 2> partially_copy 00:20.000-00:28.000
```

正規形は内部インターフェースであり、日本語入力の構造と件数を保持する。

## 9. MDPARSE中間表現

概念上の構造は次である。

```python
@dataclass
class Soundscape:
    environment: str | None
    sound_effects: str | None
    vocalization: str | None
    background_music: str | None
    background_music_reuse: BackgroundMusicReuse | None

@dataclass
class BackgroundMusicReuse:
    audio_number: int
    relationship: Literal["fully_copy", "partially_copy"]
    source_start_ms: int | None
    source_end_ms: int | None

@dataclass
class Shot:
    start_ms: int
    lines: list[str]

@dataclass
class RetentionRule:
    subject_number: int
    relationship: str
    description: str
    target_subject_number: int | None

@dataclass
class Scene:
    duration: int
    is_continue: bool
    preamble: list[str]
    shots: list[Shot]
    soundscape: Soundscape

@dataclass
class Emd:
    subjects: list[str]
    retention_rules: list[RetentionRule]
    common_prompt: list[str]
    scenes: list[Scene]
```

MDPARSEは翻訳済み本文を変更せず格納し、トップレベル順序、重複、Shot時刻、Soundscape値、空ブロックを再検証する。防御的に同じコメント走査を行い、正規形へコメントが渡された場合も同じ規則で除外する。

## 10. JSONGEN

### 10.1 トップレベル

```json
{
  "prompt_prefix": "",
  "defaults": {
    "duration_seconds": 5,
    "steps": 8
  },
  "shots": []
}
```

- `prompt_prefix`は必ず空文字列。Commonはこのキーへ格納せず、各Sceneの`detailed_description`へ条件付きで展開する。
- `defaults.duration_seconds`は5。
- `defaults.steps`はノード入力値。1～10000の整数。
- `shots`はScene順の1～128要素。

### 10.2 Sceneオブジェクト

- `id`は1始まりで`scene_1`、`scene_2`、...。
- `duration_seconds`はScene duration。
- 非継続Sceneは`context_length: 0`と`audio_context_length: 0`を持つ。
- 継続Sceneは上記2キーを持たず、`continuation_mode: "guide"`を持つ。

### 10.3 使用Subject

Scene preambleと全Shot本文から、ダイレクトスピーチ領域を除外して`<Subject N>`を抽出する。

- 抽出されたSubjectだけをアクティブとする。
- 未定義Subjectはエラー。
- Subject定義に書かれただけのSubjectはアクティブにしない。
- Commonに書かれただけのSubjectもアクティブにしない。
- SubjectがないSceneはエフェクト専用として固定文を出す。

アクティブSubject及びアクティブAudioを確定した後、Commonの各行を5.4の規則でSceneへ選択する。Commonに未定義Subject又は範囲外・非正規Audioがあればエラーとする。

### 10.4 発声許可

発声は次の全条件を必要とする。

1. Shot本文に保護済みダイレクトスピーチがある。
2. その台詞より前の同じ行に`<Subject N>`がある。
3. Scene Soundscapeが`EXPLICIT_DIALOGUE_ONLY`である。

JSONGENは各台詞の直前にある最も近いSubject参照から`(SN)`を生成する。入力に話者IDがある場合はエラーとする。

リップシンク正規形はSubject、Audio及び台詞の対応を構造として持つため、JSONGENが次の自然な記述へ展開する。

```text
<Subject 1> (S1) physically performs the directly reused spoken audio from <Audio 1> and lip-syncs exactly to <d>[Japanese]こんにちは。</d>.
```

元信号と正確な語句を保持し、置換、反復又は追加発声を行わない固定文も同じShotへ追加する。

肯定的な英語発声動詞があり同じ行に台詞がない場合は、別の明示台詞がScene内にあっても未保護発声キューとする。否定された`does not speak`、`without speaking`等は発声要求に数えない。

JSONGENは`speech_guard`として`strict`又は`warn`を受け取り、既定値は`strict`とする。

- `strict`: 未保護発声キューをエラーにする。
- `warn`: Common、Scene preamble又はShotの位置、検出語及び想定外の人物発声リスクをWARNINGログへ記録し、その英文を変更せずJSON生成を続行する。
- `warn`はAudio参照、話者ID又は発声許可を自動追加しない。Soundscapeの無音フォールバックも変更しない。このため、発声らしい`detailed_description`と人物発声を否定する`overall_soundscape`が同居し、MiniMax H3の生成結果が非決定的になり得る。

未保護発声キューの検出対象には会話、ナレーション、朗読、歌唱並びに笑い、息を呑む、溜め息、鼻歌及びうめき等の人物由来の非言語発声を含む。Environment及びSound effectsの非人物音はこのヒューリスティック検査の対象外とする。

`speech_guard`が緩和するのは英語発声動詞によるヒューリスティック検査だけである。保護台詞と`EXPLICIT_DIALOGUE_ONLY`の不一致、許可だけがあり保護台詞がない状態、不正な`<d>`、Subject又はAudio参照、リップシンク及びSoundscape構造は`warn`でもエラーとする。

### 10.5 Audio有効化

許可された実発声を持つアクティブSubjectについて、そのSubject定義内の`<Audio N>`だけを有効化する。

- Subject定義本体からAudio句を除去する。
- 有効なAudioごとに独立定義を追加する。
- `summary`を`[reference generation + audio reference]`にする。
- `retention_analysis`へ`<Audio N>: reference`を追加する。
- 該当ShotにAudioがなければ、声質とdeliveryだけを参照し元信号・元発話を導入しない固定文を追加する。
- 無発声Sceneでは全Audio参照を除去する。
- Environment又はSound effectsからAudioを有効化しない。

リップシンクバレットで使用するAudioは、Subject定義内の声質参照とは別に有効化する。

- `subject_definitions`へ、該当Audioが指定Subjectのリップシンク用に直接再利用されることを記述する。
- `summary`へ`audio reuse`を追加する。
- `retention_analysis`へ`<Audio N>: partially_copy`を追加する。
- 該当Shotと指定Subjectを明記し、他の音響層は別生成であることを記述する。
- `retention_analysis`へ`(Sx)`を書かない。
- 声質参照と信号再利用が同じAudio番号で競合する場合はエラーとする。
- Environment又はSound effectsのAudio参照は受理しない。

BGM再利用Audioは`subject_definitions`、`summary`、`retention_analysis`及び`non_diegetic_music`へ一貫して出力する。時間範囲がある場合は`detailed_description`を含む5セクションへ同じ範囲と非再構成制約を出力する。BGMボーカルのリップシンクにも使われる場合は、同じAudio定義へ対象Subject、話者ID及びShotを統合し、別Audio役割として重複させない。

### 10.6 6セクション

各Sceneの`prompt`は次の6文字列を正確にこの順序で持つ。

1. `subject_definitions:\n...`
2. `summary:\n...`
3. `retention_analysis:\n...`
4. `detailed_description:\n...`
5. `overall_soundscape:\n...`
6. `non_diegetic_music:\n...`

#### subject_definitions

アクティブSubjectを番号順に`<Subject N> is ...`で列挙する。有効なAudioは次の独立行とする。

```text
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
<Audio 2> is the directly reused spoken-audio signal performed by <Subject 1> (S1) for exact lip synchronization in [Shot 2].
<Audio 3> is the directly reused audience-only background-music signal whose original vocal layer is performed in exact lip synchronization by <Subject 2> (S2) in [Shot 3].
```

Subjectがない場合は次の固定文である。

```text
No character subject or reference-image person is active.
```

BGM再利用だけが有効なSubjectless Sceneでは、この固定文の後へ独立Audio定義を追加する。

#### summary

先頭にタスク種別を置く。

- 通常: `[reference generation]`
- Audio声質参照あり: `[reference generation + audio reference]`
- リップシンクAudio再利用あり: `[reference generation + audio reuse]`
- BGM Audio再利用あり: `[reference generation + audio reuse]`
- 両方あり: `[reference generation + audio reuse + audio reference]`

アクティブSubject、シーン内Shot数、Audio役割を簡潔に記述する。`継続`は公式の`video continuation`タスク種別とは見なさず、必要なら前Sceneを継続するという本文を追加する。

#### retention_analysis

アクティブSubjectごとに関係マーカーと適用Shotを記述する。Scene preambleだけで使う場合はscene全体への適用とする。声質参照Audioは`reference`、通常リップシンクで直接再利用するAudioは`partially_copy`、BGM再利用Audioは入力で指定された`fully_copy`又は`partially_copy`として記述する。時間範囲付き部分コピーでは正確な始端と終端及び非再構成制約も記述する。

公式ガイドに従い、`(Sx)`をこのセクションへ書かない。

参照がない場合は次の固定文である。

```text
No reference labels are active in this scene.
```

#### detailed_description

- 適用対象となったCommon行を先頭へ出す。
- Scene preambleをCommonの後、`[Shot 1]`より前に出す。
- 最初のShotは`[Shot 1] ...`。
- 後続Shotは`[Shot N] At MM:SS.mmm, ...`。
- 箇条書きは元順序で英文の文として結合する。
- Subject、Picture、Video、Audio、ダイレクトスピーチを必要位置に保持し、実発声位置へ話者IDを生成する。
- BGM再利用Audioと同じAudioを使うリップシンクは、元BGM内のボーカル、歌詞及びタイミングを保持し、置換ボーカルを生成しない歌唱演技として展開する。
- 時間範囲付きBGM再利用では、適用Common及びScene preambleの後、`[Shot 1]`より前に、元区間をScene全体へ1:1で割り当てて再構成しない固定文を出力する。

#### overall_soundscape

Environment、Sound effects、許可済み明示台詞だけを列挙し、最後に他の環境音、物理音及び人物発声がないことを置く。BGMも含め全音響が無効なら`Complete silence.`とする。生成BGMだけが有効なら環境音、物理音及び人物発声がないことを明示する。BGM再利用だけが有効なら、元トラックに含まれる可能性がある音を否定せず、別生成の環境音、物理音及び人物発声を追加しないことを明示する。台詞本文とBGMはここへ複製しない。

#### non_diegetic_music

SoundscapeのBGM及びBGM再利用が省略されるか、BGMが`NONE`なら`N/A`とする。生成BGMが指定されれば翻訳済み本文を出力し、末尾を英文句読点で閉じる。生成BGMにAudio参照及びダイレクトスピーチは含めない。

BGM再利用では`<Audio N>`、コピー関係及びaudience-only scoreであることを出力する。時間範囲付き部分コピーでは始端、終端、Scene先頭から末尾への1:1割当て及び非再構成制約を出力する。同じAudioのボーカルへリップシンクする場合は対象Subject、話者ID及びShotも記述し、元ボーカルを置換又は重複生成しない。

H3のBGM生成は結果のランダム性が高く、BGM再利用の固定文及び時間範囲も元波形の同一性を保証しない。再現性、楽曲品質又は波形同一性を重視する場合は、Suno等で用意した元音源を保持し、動画生成後にH3生成音声を元音源へ差し替える運用を推奨する。

### 10.7 最終検証

シリアライズ後に`json.loads()`し、少なくとも次を検証する。

- rootがobject。
- `prompt_prefix`が空。
- defaultsの型とsteps範囲。
- Scene数、ID一意性、duration範囲。
- promptが文字列6要素で、種類と順序が正しい。
- 各セクション本文が非空。
- detailed_description内のShot番号が1始まりの連番。
- 最終要素が非空の`non_diegetic_music:\n...`であり、BGM省略又は`なし`では正確に`N/A`。
- `non_diegetic_music`内のAudio参照が`subject_definitions`及び`retention_analysis`にも定義される。
- 継続とリセット用キーが排他的で正しい。
- 最終文字列末尾がLF1個。

## 11. エラーと警告

致命的エラーには次を含む。

- 空入力、未知行、未知ディレクティブ
- 不正なCスタイルコメント又はHTMLコメント
- 廃止構文
- 不正Retention、Shot、リップシンク、BGM再利用、元音源時間範囲又はSoundscape
- Commonの禁止要素、未定義Subject又は不正Audio参照
- 台詞、内部話者ID、発声許可の不一致
- 未定義Subject
- 属性転送の不正な転送先
- プレースホルダ欠落、重複、移動、他区間混入
- LLM応答の切断、コードフェンス、不正thinking、日本語残留
- JSONの型、キー関係、6セクション順序の不一致

警告して継続するものは次を含む。

- 非正規又は標準範囲外の参照タグをそのまま保護
- 1件目Sceneの無効な`CONTINUE`を非継続化
- Scene durationが正規範囲外又は整数でない場合の5秒フォールバック
- 安全に検証可能なマーカーレスLLM応答の復元
- `speech_guard=warn`で検出した、保護台詞のない肯定的な英語発声キュー。この場合は位置、検出語及び想定外の発声リスクを記録し、JSON生成を継続する。

## 12. ログとデバッグ

通常ログはモデル探索・ロード設定、ディレクティブ数、翻訳区間数、バッチ数、トークン数、再試行、アンロード、生成Scene数を記録する。プロンプト全文は通常ログへ出さない。

LLM応答は可能な場合にストリーミングで受信する。ComfyUIノードは各バッチの各attemptについて、受信した非空contentチャンク数を`max_tokens`に対する概算進捗として表示し、検証成功時に完了値へ進める。チャンク数は厳密なトークン数又は残り時間ではない。再試行又は次バッチでは新しい推論進捗を0から開始する。

各推論の実行中は10秒間隔のheartbeatをINFOで記録する。heartbeatはバッチ、attempt、経過秒及び受信済みcontentチャンク数だけを含み、最初のチャンク以前は0件とする。応答又は例外時に停止し、入力又は生成途中の本文を通常ログへ含めない。

任意のデバッグ出力は`ComfyUI/output/cl_japanese2json_debug/<実行ID>/`へ保存し、次を含み得る。

- `source.md`
- `system_prompt.txt`
- 保護翻訳ストリーム
- LLM要求と生応答
- 検証メタデータ
- `canonical.md`
- `result.json`
- `error.txt`

デバッグ出力は入力内容を含む。`ComfyUI/input`へは書かない。

## 13. 受入条件

- 新日本語構文を正規形へ変換できる。
- 長時間のLLM推論中にComfyUI進捗と本文を含まないheartbeatを提示できる。
- Cスタイルコメントを推論前に除外し、台詞内部の同じ記号は保持する。
- コメント構文エラーを行番号付きで拒否する。
- CommonをSubject及びAudio集合に応じて適用し、それだけでSubject又はAudioを有効化しない。
- 暗黙Shotを拒否する。
- Shot境界とミリ秒時刻を正しく出す。
- ユーザー入力の`(Sx)`を拒否し、話者Subject番号から`(SN)`を生成する。
- 保持分析をアクティブSubjectへだけ適用する。
- 6セクションを公式順で生成する。
- `retention_analysis`へ話者IDを書かない。
- 無発声SceneからAudioを除去する。
- 音響省略時は無音へフォールバックする。
- 通常リップシンクAudioを`audio reuse`及び`partially_copy`として出力する。
- BGM再利用Audioを指定された`fully_copy`又は`partially_copy`で出力し、時間範囲をScene全体へ1:1で割り当て、同一Audioのボーカルリップシンクと統合する。
- BGM生成又は再利用指定を`non_diegetic_music`へ出力し、省略時は`N/A`へフォールバックする。
- LLMが構造又は保護トークンを破壊した結果を受理しない。
- 実GGUFを必要としない単体テストが成功する。
