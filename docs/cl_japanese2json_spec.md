# cl_japanese2json 仕様書

## 1. 目的

本仕様は、日本語の縮小版MarkdownをMiniMax H3 Full-Reference形式のContex-Loop Plan JSONへ変換するコンパイラ`cl_japanese2json`を定義する。

処理は次の3段階からなる。

1. LLMJ2E: 日本語Markdownを字句解析し、通常文章だけをUS Englishへ翻訳する。
2. MDPARSE: 正規形英語Markdownを型付き中間表現へ変換する。
3. JSONGEN: 中間表現から厳格なPlan JSONを決定論的に生成し、再検証する。

LLMは最終JSON、ディレクティブ、ショット構造、参照関係、話者ID、保持関係を決定しない。これらはPythonが処理する。

本版はドラフトの破壊的改訂であり、旧`# 共通プロンプト`、暗黙ショット、`N秒生成する`、`継続する`との後方互換性を持たない。

## 2. 対象範囲

対象は次のとおりである。

- UTF-8日本語縮小版Markdown
- Subject、Picture、Video、Audio参照タグ
- 日本語ダイレクトスピーチ
- 文書全体で安定した`(Sx)`話者ID
- シーンとシーン内ショット
- シーンローカルな音響許可リスト
- Subject単位のグローバル保持分析規則
- MiniMax H3 Full-Referenceの6セクション
- Contex-Loopの継続・リセット設定

対象外は次のとおりである。

- LLMによる最終JSON生成
- LLMによる保持マーカー、話者ID、ショット時刻の推測
- `llama-cpp-python`の自動インストール又は更新
- GGUFの自動ダウンロード
- ComfyUI本体又は他の`custom_nodes`の変更
- BGM生成
- Audio信号の直接コピー、BGM/環境音のAudio参照
- 旧入力構文の互換変換

## 3. ファイル構成

```text
compiler/
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

`structures.py`は中間表現、`protected_text.py`は保護と復元、`llmj2e.py`は字句解析・翻訳、`mdparse.py`は正規形パース、`jsongen.py`はPlan生成と最終検証を担当する。

## 4. 文字列共通規則

- 入力はPython`str`である。
- UTF-8として扱う。先頭BOMは許容する。
- LF、CRLF、最終改行の有無は意味を変えない。
- ディレクティブと箇条書きは行頭から始める。
- 箇条書きマーカーはASCIIの`* `である。
- 同一ブロックのディレクティブと箇条書きの途中へ空行を置かない。
- 空行はブロック間の区切りとして使用できる。
- 未知のディレクティブ、未知のサブディレクティブ、非箇条書き本文はエラーである。

## 5. 日本語縮小版Markdown

### 5.1 文書順序

文書の順序は次のとおりである。

```text
[# サブジェクト]
[# 保持分析]
# シーン ...
...
```

- `# サブジェクト`は0又は1回。
- `# 保持分析`は0又は1回。存在する場合はSubjectの後、最初のSceneより前。
- `# シーン`は1～128回。
- SubjectとRetentionの繰り返し又はScene開始後の出現はエラー。
- `# 共通プロンプト`は廃止済みでありエラー。

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

### 5.4 Scene

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

### 5.5 Shot

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

### 5.6 Soundscape

```text
## 音響
* 環境音: 草原を吹く弱い風音。
* 効果音: 足音と衣服の擦れ音。
* 発声: 指定台詞のみ
```

- Soundscapeは各Sceneに0又は1回。
- 全Shotの後へ置く。
- 空のSoundscapeはエラー。
- 項目は`環境音`、`効果音`、`発声`を各0又は1回。
- 環境音と効果音は任意の日本語本文又は`なし`。
- 発声は`なし`又は`指定台詞のみ`だけ。
- 項目を省略した場合、その音響層は無効。
- Soundscape全体を省略した場合、完全無音へフォールバックする。
- 環境音と効果音には`<Audio N>`又はダイレクトスピーチを書けない。
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

`(S1)`、`(S2)`以降を保護対象とする。`<Subject N> (Sx)`又は`<Subject N>(Sx)`として隣接している場合は、Subject参照と話者IDを合わせた1個の不可分プレースホルダとして保護する。LLMは話者ペアを分離したり、その間へ語句を挿入したりできない。

話者IDの意味規則はJSONGENで検証する。

- 実際の発声が文書に現れる順に、新規話者をS1、S2、...と割り当てる。
- 同じSubjectは同じIDを再利用する。
- 同じIDを異なるSubjectへ割り当てない。
- 話者IDは同じ行で`<Subject N> (Sx)`又は`<Subject N>(Sx)`と隣接させる。
- 話者IDはダイレクトスピーチより前に置く。
- ダイレクトスピーチのない行へ話者IDを書かない。

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
2. `<Subject N> (Sx)`話者ペア
3. 残りの参照タグ
4. 残りの話者ID
5. 日本語鉤括弧台詞

各要素を区間固有の`CLJ...X`プレースホルダへ置換する。復元時は全プレースホルダがバイト単位で完全一致し、各1回でなければならない。

## 7. LLMJ2E

### 7.1 正規形ディレクティブ

| 日本語 | 正規形 |
| --- | --- |
| `# サブジェクト` | `# Subjects` |
| `# 保持分析` | `# Retention` |
| `# シーン 8秒 継続` | `# Scene 8sec CONTINUE` |
| `## ショット 3.25秒` | `## Shot 3.25sec` |
| `## 音響` | `## Soundscape` |

Retentionの固定マーカー、Soundscapeのラベルと固定値もPythonが正規化する。

### 7.2 翻訳ストリーム

文書ディレクティブと各翻訳区間を索引付き構造プレースホルダへ変換する。区間種別は次を用いる。

- `SUB`: Subject
- `RET`: Retention説明
- `SCN`: Scene preamble及びShot本文
- `SND`: Environment又はSound effects

参照タグ、話者ID、台詞はさらに区間固有の保護プレースホルダとなる。構造化JSON転送は用いず、1本の生テキストストリームを`TRANSLATION_STREAM_BEGIN`と`TRANSLATION_STREAM_END`の間へ置く。

実効コンテキスト長に文書全体が収まる場合、推論要求は1回である。収まらない場合だけレコード境界でバッチ分割し、1レコードを分割しない。

### 7.3 システムプロンプト

システムプロンプトは`prompts/llmj2e_qwen3_8b_system_prompt.txt`からUTF-8で読み込む。

最低限、次をモデルへ要求する。

- US Englishへの翻訳だけを行う。
- JSON、Markdown fence、説明、推論を返さない。
- 全`CLJT...X`及び`CLJ...X`を翻訳、変更、移動、複製、削除しない。
- SUBは`<Subject N> is`の右辺となる単数名詞句にする。
- RET、SCN、SNDは自然で簡潔な英語プロンプト文にする。
- 保護された台詞と話者IDを変更しない。
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

# Scene 8sec
* A bright modern office district is rendered in a vivid 2D anime style.
## Shot
* <Subject 1> (S1) says <d>[Japanese]ようこそ！</d>.
## Shot 4.5sec
* The camera slowly approaches <Subject 1>.
## Soundscape
* Environment: Distant city ambience.
* Vocalization: EXPLICIT_DIALOGUE_ONLY
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
    scenes: list[Scene]
```

MDPARSEは翻訳済み本文を変更せず格納し、トップレベル順序、重複、Shot時刻、Soundscape値、空ブロックを再検証する。

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

- `prompt_prefix`は必ず空文字列。廃止した共通プロンプトを格納しない。
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
- SubjectがないSceneはエフェクト専用として固定文を出す。

### 10.4 発声許可

発声は次の全条件を必要とする。

1. Shot本文に保護済みダイレクトスピーチがある。
2. その台詞より前の同じ行に`<Subject N> (Sx)`がある。
3. Scene Soundscapeが`EXPLICIT_DIALOGUE_ONLY`である。
4. 話者IDが文書全体の割当規則を満たす。

肯定的な英語発声動詞があり同じ行に台詞がない場合は、別の明示台詞がScene内にあってもエラーとする。否定された`does not speak`、`without speaking`等は発声要求に数えない。

### 10.5 Audio有効化

許可された実発声を持つアクティブSubjectについて、そのSubject定義内の`<Audio N>`だけを有効化する。

- Subject定義本体からAudio句を除去する。
- 有効なAudioごとに独立定義を追加する。
- `summary`を`[reference generation + audio reference]`にする。
- `retention_analysis`へ`<Audio N>: reference`を追加する。
- 該当ShotにAudioがなければ、声質とdeliveryだけを参照し元信号・元発話を導入しない固定文を追加する。
- 無発声Sceneでは全Audio参照を除去する。
- Environment又はSound effectsからAudioを有効化しない。

### 10.6 6セクション

各Sceneの`prompt`は次の6文字列を正確にこの順序で持つ。

1. `subject_definitions:\n...`
2. `summary:\n...`
3. `retention_analysis:\n...`
4. `detailed_description:\n...`
5. `overall_soundscape:\n...`
6. `non_diegetic_music:\nN/A`

#### subject_definitions

アクティブSubjectを番号順に`<Subject N> is ...`で列挙する。有効なAudioは次の独立行とする。

```text
<Audio 1> is the voice-timbre reference for <Subject 1> (S1).
```

Subjectがない場合は次の固定文である。

```text
No character subject or reference-image person is active.
```

#### summary

先頭にタスク種別を置く。

- 通常: `[reference generation]`
- Audio声質参照あり: `[reference generation + audio reference]`

アクティブSubject、シーン内Shot数、Audio役割を簡潔に記述する。`継続`は公式の`video continuation`タスク種別とは見なさず、必要なら前Sceneを継続するという本文を追加する。

#### retention_analysis

アクティブSubjectごとに関係マーカーと適用Shotを記述する。Scene preambleだけで使う場合はscene全体への適用とする。Audioは`reference`として記述する。

公式ガイドに従い、`(Sx)`をこのセクションへ書かない。

参照がない場合は次の固定文である。

```text
No reference labels are active in this scene.
```

#### detailed_description

- Scene preambleを`[Shot 1]`より前に出す。
- 最初のShotは`[Shot 1] ...`。
- 後続Shotは`[Shot N] At MM:SS.mmm, ...`。
- 箇条書きは元順序で英文の文として結合する。
- Subject、Picture、Video、Audio、話者ID、ダイレクトスピーチを必要位置に保持する。

#### overall_soundscape

Environment、Sound effects、許可済み明示台詞だけを列挙し、最後に`No other sound is present.`を置く。何も許可されない場合は`Complete silence.`とする。台詞本文はここへ複製しない。

#### non_diegetic_music

常に`N/A`である。BGMは別のAIで作成し後編集する運用を前提とする。

### 10.7 最終検証

シリアライズ後に`json.loads()`し、少なくとも次を検証する。

- rootがobject。
- `prompt_prefix`が空。
- defaultsの型とsteps範囲。
- Scene数、ID一意性、duration範囲。
- promptが文字列6要素で、種類と順序が正しい。
- 各セクション本文が非空。
- detailed_description内のShot番号が1始まりの連番。
- 最終要素が正確な`non_diegetic_music:\nN/A`。
- 継続とリセット用キーが排他的で正しい。
- 最終文字列末尾がLF1個。

## 11. エラーと警告

致命的エラーには次を含む。

- 空入力、未知行、未知ディレクティブ
- 廃止構文
- 不正Retention、Shot、Soundscape
- 台詞、話者ID、発声許可の不一致
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

## 12. ログとデバッグ

通常ログはモデル探索・ロード設定、ディレクティブ数、翻訳区間数、バッチ数、トークン数、再試行、アンロード、生成Scene数を記録する。プロンプト全文は通常ログへ出さない。

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
- 旧共通プロンプトと暗黙Shotを拒否する。
- Shot境界とミリ秒時刻を正しく出す。
- `(Sx)`を保護し、全体で一貫性を検証する。
- 保持分析をアクティブSubjectへだけ適用する。
- 6セクションを公式順で生成する。
- `retention_analysis`へ話者IDを書かない。
- 無発声SceneからAudioを除去する。
- 音響省略時は無音へフォールバックする。
- BGMを常に無効化する。
- LLMが構造又は保護トークンを破壊した結果を受理しない。
- 実GGUFを必要としない単体テストが成功する。
