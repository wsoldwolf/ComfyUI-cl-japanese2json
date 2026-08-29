# cl_japanese2json 仕様書

## 1. 概要

`cl_japanese2json` は、縮小版Markdownで記述された日本語のMiniMax H3 Contex-Loopプロジェクトを英語へ翻訳し、MiniMax H3 Contex-Loop Planが受理できる厳格なJSONを生成する。

本システムは、LLMにJSON生成やMarkdown構造の判断を行わせない。LLMは通常文章の日本語から英語への翻訳だけを担当し、ディレクティブ、箇条書き、参照タグ、日本語読み上げ文字列、シーン構造及びJSON構文はPythonコードが決定論的に処理する。

これにより、次の問題を防止する。

- LLMによるディレクティブの欠落、追加、改名
- 箇条書きの結合、分割、並べ替え
- 参照タグの翻訳、削除、番号変更
- 日本語読み上げ文字列の翻訳または欠落
- シーンの欠落または重複
- 不正なJSON、末尾カンマ、未エスケープ文字列の生成
- シーンで使用していないSubject定義の混入
- 非継続シーンに前シーンのコンテキストが継承される問題

本仕様では、リポジトリ名及びノード名に合わせて `Contex-Loop` と表記する。

## 2. 対象範囲

### 2.1 対象

- UTF-8で記述された縮小版Markdownの読み込み
- 日本語の通常文章から英語への翻訳
- MiniMax H3参照タグの保持
- 日本語読み上げ文字列の `<d>[Japanese]...</d>` 形式への変換
- 既存のダイレクトスピーチタグの完全保持
- 共通プロンプト、Subject定義及びシーンのパース
- シーンごとの使用Subject定義の生成
- Contex-Loop Plan用JSONの生成
- LF及びCRLF入力への対応

### 2.2 対象外

- MiniMax H3プロンプト内容の創作、要約または意味の追加
- シーン時間に合わせた動作内容の自動削減または追加
- 参照メディアが実際に接続されているかの検証
- `<Subject N>` の人物同定や意味解析
- MiniMax H3のサンプリング実行
- JSONから縮小版Markdownへの逆変換

## 3. モジュール構成

`cl_japanese2json` は、次の3モジュールから構成する。

1. `LLMJ2E`
   - 日本語縮小版Markdownを字句単位で読み込む。
   - ディレクティブをPythonで正規化する。
   - 参照タグ及び読み上げ文字列を保護する。
   - 箇条書き本文だけを `llama-cpp-python` で日本語から英語へ翻訳する。
   - 翻訳結果を検証し、英語縮小版Markdownを再構築する。
2. `MDPARSE`
   - `LLMJ2E` が生成した正規形の英語縮小版Markdownをパースする。
   - Python上のデータ構造 `Emd` を構築する。
3. `JSONGEN`
   - `Emd` をContex-Loop Plan用の厳格なJSONへ変換する。

処理順は次のとおりとする。

```text
Japanese reduced Markdown
    -> LLMJ2E lexical preprocessing
    -> protected payload translation
    -> canonical English reduced Markdown
    -> MDPARSE
    -> Emd
    -> JSONGEN
    -> strict JSON
```

## 4. 入出力の共通規則

### 4.1 文字コード

- 入力Markdown、システムプロンプト及び出力JSONはUTF-8とする。
- UTF-8 BOMは入力時に除去してよい。
- 内部の改行コードはLF `\n` に正規化する。
- JSONファイルの末尾には1個のLFを付与する。

### 4.2 行の定義

実装は文字単位で `\n` や `\r\n` の連続数を数えず、Pythonの `splitlines()` 相当で行に分割する。

空行とは、行から空白文字を除去した結果が空文字列になる行である。

```python
is_blank = line.strip() == ""
```

連続する複数の空行は、1個のセクション終端として扱い、残りを読み飛ばしてよい。

### 4.3 行頭

- ディレクティブは行頭の `#` から始まる場合だけ認識する。
- 箇条書きは行頭の `* ` から始まる場合だけ認識する。
- `#` または `* ` の前にあるインデントは許可しない。
- 箇条書きマーカーはアスタリスクと1個のASCII空白 `0x20` で構成する。

### 4.4 順序

- ディレクティブ及び箇条書きの入力順序を保持する。
- LLMは行の追加、削除、結合、分割及び並べ替えを行ってはならない。
- 同じ種類のディレクティブが複数回登場した場合、既存配列の末尾へ追記する。
- 各 `# Scene` は出現するたびに新しい `Scene` を生成する。

## 5. 日本語縮小版Markdownの文法

### 5.1 ディレクティブ

日本語入力では、次の3種類のディレクティブを使用する。

```text
# サブジェクト
# 共通プロンプト
# シーン [N秒] [継続]
```

角括弧 `[]` は省略可能要素を示す仕様上の表記であり、実際の入力には記述しない。

### 5.2 サブジェクトディレクティブ

```text
# サブジェクト
```

後続する箇条書きは、配列の出現順に `<Subject 1>`、`<Subject 2>`、`<Subject 3>`、`<Subject 4>` に対応する。

各箇条書きはSubject名を含まない定義本文として記述する。JSONGENが `<Subject N> is ` を付与するため、LLMJ2Eはこのセクションの本文を `is` に続けられる英語の名詞句へ翻訳しなければならない。

入力例：

```text
# サブジェクト
* <Picture 1>を外観参照、<Audio 1>を声質参照として使用する人物。
* <Picture 2>を外観参照、<Audio 2>を声質参照として使用する人物。
```

翻訳後の内部表現例：

```text
# Subjects
* a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.
* a character whose appearance is based on <Picture 2> and whose voice is based on <Audio 2>.
```

### 5.3 共通プロンプトディレクティブ

```text
# 共通プロンプト
```

後続する箇条書きは、全シーンに共通して適用するプロンプトを表す。

共通プロンプトには、画風、場所、時間帯、継続性、恒常的な外観及び全シーン共通の制約を記述する。

### 5.4 シーンディレクティブ

```text
# シーン [N秒] [継続]
```

- `N` は1以上60以下の10進整数とする。
- 秒数を省略した場合の既定値は5秒とする。
- `継続` を指定した場合は前シーンから継続する。
- オプションの順序は秒数、継続指定の順に固定する。
- `# シーン 継続` は有効であり、既定値5秒を使用する。
- シーン1の `継続` は、外部の既存動画コンテキストを使用する場合を除いて意味を持たない。通常は警告を出して非継続として扱う。
- 旧形式の `N秒生成する` 及び `継続する` は受理せず、無効なシーンオプションとして警告し、5秒・非継続へフォールバックする。

入力例：

```text
# シーン
# シーン 8秒
# シーン 継続
# シーン 8秒 継続
```

### 5.5 セクションの終端

セクションは、次のいずれかで終了する。

1. 空行
2. 次の認識済みディレクティブ
3. EOF

認識済みディレクティブが空行なしで現れた場合、現在のセクションを終了し、そのディレクティブの処理へ移る。

### 5.6 未知の行

- セクション外の未知の行は警告して無視する。
- セクション内で `* ` から始まらない行は警告して無視する。
- 未知の `#` ディレクティブは警告して無視する。
- 未知の行をLLMへ渡してはならない。

## 6. LLMJ2E

### 6.1 役割

`LLMJ2E` は、日本語縮小版Markdownを正規形の英語縮小版Markdownへ変換する。

LLMは通常文章の翻訳だけを担当する。次の処理はPythonコードが実行する。

- 入力の行分割
- ディレクティブの認識と正規化
- 箇条書きマーカーの除去と復元
- セクション種別の追跡
- 参照タグの保護と復元
- 既存の `<d>...</d>` の保護と復元
- `「...」` の検出と日本語読み上げタグへの変換
- 翻訳前後の行数及び保護トークンの検証

### 6.2 正規形ディレクティブへの変換

ディレクティブはLLMへ翻訳させず、次のようにPythonで変換する。

| 日本語入力 | 正規形出力 |
| --- | --- |
| `# サブジェクト` | `# Subjects` |
| `# 共通プロンプト` | `# Common` |
| `# シーン` | `# Scene` |
| `# シーン 5秒` | `# Scene 5sec` |
| `# シーン 継続` | `# Scene CONTINUE` |
| `# シーン 5秒 継続` | `# Scene 5sec CONTINUE` |

正規形シーンディレクティブは、次の文法に一致しなければならない。

```regex
^# Scene(?: ([1-9]|[1-5][0-9]|60)sec)?(?: CONTINUE)?$
```

単位は値にかかわらず常に `sec` とし、`secs` は使用しない。

### 6.3 参照タグ

次の参照タグを認識する。

| タグ | 有効範囲 |
| --- | --- |
| `<Picture N>` | N = 1～9 |
| `<Video N>` | N = 1～9 |
| `<Audio N>` | N = 1～3 |
| `<Subject N>` | N = 1～4 |

タグ名と番号の間には1個のASCII空白を使用する。

有効な参照タグは翻訳前に一時トークンへ置換し、翻訳後に入力と完全に同じ文字列へ復元する。大文字小文字、空白及び番号を変更してはならない。

参照形式に見えるが範囲外のタグは警告し、文字列自体は変更せず保護する。構文上の対応範囲は、実際にMiniMax H3へ接続できる参照数を保証しない。

### 6.4 既存ダイレクトスピーチ

既存の次の形式を検出した場合、開始タグから終了タグまでを一つの保護領域として扱う。

```text
<d>[Language]text</d>
```

- `<d>` から対応する `</d>` までを一切変更しない。
- 内部の文字列、言語指定、空白、句読点及びエスケープを変更しない。
- 内部にある `「」` を再変換しない。
- 本仕様では1個のダイレクトスピーチ領域は同一行内で完結しなければならない。
- 閉じていない `<d>` または対応しない `</d>` は致命的エラーとする。

### 6.5 日本語読み上げ文字列

通常文章中の日本語鉤括弧 `「...」` を検出し、次の形式へ変換する。

```text
<d>[Japanese]...</d>
```

例：

```text
「よろしくおねがいします」
```

変換結果：

```text
<d>[Japanese]よろしくおねがいします</d>
```

規則は次のとおりとする。

- 鉤括弧内部の文字列は一切翻訳しない。
- 鉤括弧そのものは出力しない。
- 一行に複数の `「...」` が存在する場合、左からすべて変換する。
- 鉤括弧の入れ子は許可しない。
- 読み上げ領域は同一行内で完結しなければならない。
- 閉じていない鉤括弧、余分な閉じ鉤括弧または入れ子は致命的エラーとする。
- 既存の `<d>...</d>` を先に保護し、その内部の鉤括弧には触れない。

### 6.6 読み上げ文字列内部のエスケープ

新たに生成する `<d>[Japanese]...</d>` の本文中に、エスケープされていない次の文字が存在する場合、直前へバックスラッシュを追加する。

| 入力文字 | タグ内部の論理文字列 |
| --- | --- |
| `<` | `\<` |
| `>` | `\>` |
| `[` | `\[` |
| `]` | `\]` |

既にバックスラッシュでエスケープされた文字を二重エスケープしてはならない。

実装例に相当する規則：

```python
re.sub(r"(?<!\\)([<>\[\]])", r"\\\1", dialogue_text)
```

JSONファイル上では、JSONの文字列エスケープにより1個の論理バックスラッシュが `\\` と表示される。手作業でJSON用の追加エスケープを行わず、JSONシリアライザへ任せる。

### 6.7 保護処理の優先順位

同一行では、必ず次の順序で処理する。

1. 既存の `<d>...</d>` の検出と保護
2. 参照タグの検出と保護
3. `「...」` の検出、内部エスケープ及び読み上げタグ化
4. 保護済み読み上げ領域の一時トークン化
5. 残った通常文章のLLM翻訳
6. 一時トークンの完全復元

一時トークンは入力本文と衝突しないASCII文字列を使用する。入力に同じトークンが存在する場合は別のトークンを生成する。

### 6.8 LLMへ渡す翻訳単位

- LLM呼び出し前に入力文書全体を字句解析する。
- 認識済みディレクティブは文書順の一意なディレクティブプレースホルダへ置換する。ディレクティブ原文及び正規形はPython側に保持し、LLMへ翻訳させない。
- 各箇条書き本文の先頭は、セクション種別を表す `SUB`、`COM` または `SCN` と出現順の番号を含む、一意な区間プレースホルダへ置換する。区間は次の構造プレースホルダ直前までとし、区間末尾専用プレースホルダは設けない。箇条書きマーカー自体はLLMへ翻訳させない。
- 参照タグ、既存の直接発話及び日本語鉤括弧内の発話は、一意な短い保護プレースホルダへ置換する。日本語鉤括弧内の発話は `<d>[Japanese]...</d>` へ変換した値をPython側の復元辞書に保持する。
- ディレクティブ、区間先頭、参照タグ、発話及び停止用の各プレースホルダは入力本文と衝突しないASCII文字列とする。
- 置換後の文書は、文書順を維持した単一の保護済み翻訳ストリーム文字列として表現し、末尾へ一意な `END` 停止プレースホルダを付ける。LLMへレコード配列、IDと本文のオブジェクト配列、原文断片の辞書またはプレースホルダ一覧を重複して渡してはならない。
- LLMへのuser messageは、保護済み翻訳ストリームを `TRANSLATION_STREAM_BEGIN` と `TRANSLATION_STREAM_END` で囲んだ生テキストとする。転送用JSONへ格納してはならない。
- LLMの応答は翻訳後の生ストリーム文字列だけとする。JSON、引用符、レコード配列及び説明文を返させてはならない。
- `create_chat_completion()` へ `stop=[END停止プレースホルダ]` を渡す。モデルには停止プレースホルダを複写させ、llama.cppが応答へ含めず停止してよい。バックエンドが停止プレースホルダを応答へ残した場合も末尾に1回だけ存在するときは受理する。
- 箇条書き本文は復元及び検証上の論理区間であり、1区間を複数行へ分割してはならない。ただし通常時は各区間を個別推論せず、全区間を含む単一ストリームを1回で翻訳する。
- 入力メッセージと設定上限 `max_tokens` の合計が実効コンテキスト長へ収まる場合、区間数にかかわらず文書全体を1回の `create_chat_completion()` で翻訳する。固定の区間件数上限を設けてはならない。
- 1回に収まらない場合だけ、文書順を保持したまま、各推論へ収まる最大数の箇条書き区間で複数ストリームへ分割する。箇条書き本文及び保護領域の途中で分割してはならない。
- 翻訳後はすべての区間先頭プレースホルダの個数と順序、次の構造プレースホルダまでの区間対応、存在するディレクティブプレースホルダの個数と順序、ディレクティブ直後への文章追加、各区間が所有する保護プレースホルダを検証する。すべての区間先頭プレースホルダが完全一致して文書順に各1回存在し、未知又は重複した構造プレースホルダがなく、最初の構造プレースホルダより前に文章がない場合、ディレクティブプレースホルダだけの欠落を許容し、Python側に保持したブロック情報から復元してよい。
- 先頭のSubjects区間でモデルが区間先頭プレースホルダを削除し、代わりに番号付きSubjectタグ、ASCII空白、`is`、ASCII空白、翻訳本文という完全一致形式を1区間1行で返した場合に限り、行数、1始まりの連番及び各区間の保護プレースホルダ所有関係を検証して、欠落した先頭SUBプレースホルダを復元してよい。`refers to`等の別動詞、ラベルだけ、非連番、余分な行又はSubjects以外の欠落には適用しない。
- 検証済みの英訳を元の区間へ割り当て、Python側に保持したディレクティブ及び復元辞書を使って正規形Markdownを決定論的に再構築する。
- 個別区間だけが検証に失敗した場合、正常な区間の翻訳を保持し、失敗区間だけを1回再試行する。構造プレースホルダが欠落しても、開始位置と直後の構造プレースホルダを確認でき、内容検証に通った区間は保持する。欠落区間及び境界を確定できない隣接区間だけを1回再試行する。説明、thinking、重複または順序変更によって安全に部分復旧できない場合は推論単位全体を1回再試行する。
- 構造プレースホルダの一部が残る応答を、段落数又は行数だけで復旧してはならない。構造プレースホルダが完全に0件の場合に限り、翻訳対象が2区間以上、文書内に保護プレースホルダが1個以上あり、空行で区切られた非空段落数が区間数と完全一致するとき、出力段落順を区間順として検証してよい。段落数が一致しない場合は、非空出力行数が区間数と完全一致するときだけ行順で検証してよい。段落内の物理改行は空白へ正規化する。この場合も各区間が所有する保護プレースホルダ、別区間からの移動、Subject文末及び日本語残留を通常どおり検証し、1項目でも失敗すれば採用しない。
- 構造プレースホルダが完全に0件で段落数及び行数も区間数と一致しない場合は、完全に検証できた保護プレースホルダを区間アンカーとして使用してよい。アンカーは応答順と原文区間順がともに厳密な昇順でなければならない。隣接アンカー間、先頭及び末尾について、応答候補数と原文区間数が完全一致する範囲だけを順番に個別検証して保持する。件数が異なる範囲、保護プレースホルダが欠落した区間及び個別検証に失敗した区間は採用せず、その区間だけを1回再試行する。この復旧で位置を推測してはならない。
- 再試行では対象を未解決区間だけに限定し、自然な英文では主語又は参照の反復を省略できる場合でも、各CLJ保護プレースホルダを英文中へ明示するよう指示する。診断ログには応答本文を含めず、段落数、非空行数、保護プレースホルダ検出数及び安全に保持できた区間数だけを記録する。
- 1回の再試行後、Scene区間で欠落している保護プレースホルダが `<d>...</d>` の直接発話を保持するものだけであり、他の保護プレースホルダが各1回存在し、構造、所有関係、日本語残留及びその他の検証に合格する場合に限り、Python側の復元辞書から欠落した直接発話プレースホルダを英文末尾の明示的な発話内容として補完し、区間全体を再検証してよい。Subject、Picture、Video、Audio又はその他のプレースホルダ欠落、重複、別区間からの移動と同時に補完してはならない。補完後の再検証に失敗した場合は例外とする。

### 6.9 セクション別翻訳規則

#### Subjects

- `<Subject N> is ` の後へ接続できる単数名詞句として翻訳する。
- Subject番号を本文へ追加しない。
- LLMへの指示には最終JSON側のSubject接頭辞を例示せず、SUBプレースホルダ直後から名詞句だけを出力させる。モデルが追加したSubjectラベル、番号、コピュラ、コロン又はその他の枠付けは翻訳本文として採用しない。
- 外観参照、声質参照、衣装及び識別特徴を省略しない。
- 文末はピリオドで終了させる。

例：

```text
a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.
```

#### Common及びScene

- 命令または描写として自然なUS Englishへ翻訳する。
- 意味を追加、削除、要約または強調しない。
- 数値、方向、時刻、回数、左右及び同時性を保持する。
- 読み上げタグの直前へ不要なダブルクォートを追加しない。
- 読み上げタグの前後に文法上必要な `says,` などを置いてよいが、タグ内部へ変更を加えない。

### 6.10 システムプロンプトファイル

実装は、Qwen3 8B向けシステムプロンプトをPythonコードへ直接埋め込まず、UTF-8テキストファイルとして生成する。

推奨パス：

```text
prompts/llmj2e_qwen3_8b_system_prompt.txt
```

`cl_japanese2json` は起動時またはモデル初期化時にこのファイルを読み込み、LLMのsystem messageとして使用する。

システムプロンプトには少なくとも次の規則を含める。

```text
You are a deterministic Japanese-to-US-English translator for one MiniMax H3 protected text stream.
Return only the translated raw stream. Do not return JSON, quotes, Markdown fences, commentary, or explanations.
Translate only Japanese prose following each SUB, COM, or SCN segment placeholder.
Never translate, alter, move, reorder, duplicate, or delete any placeholder token.
Preserve every protected placeholder byte-for-byte and exactly once.
Never invent a placeholder and never move one into another segment.
Protected placeholders are required content, not optional labels. Even when natural English could omit a repeated subject or reference, copy its placeholder and express it explicitly in that segment.
Each segment ends immediately before the next structural placeholder.
Do not add text after a directive placeholder.
Do not translate text represented by protected placeholders.
Some protected placeholders contain the only copy of an exact spoken line. Never summarize, paraphrase, or omit them even when surrounding prose already describes the speech.
Preserve numbers, timing, counts, directions, left/right relationships, simultaneity, and negation.
For a SUB segment, output only a singular English noun phrase ending with an ASCII period.
Begin directly with the noun phrase and do not prepend a label, reference, index, copula, colon, or framing text.
For COM and SCN segments, output concise and natural US English prompt text.
Use English outside protected placeholders.
Copy the final CLJT...ENDX stop placeholder immediately after the final translated segment.
```

実際のシステムプロンプトには、ディレクティブプレースホルダ、区間先頭プレースホルダ、停止プレースホルダ及び保護プレースホルダを翻訳せず、改変、移動、並べ替え、複製または削除しない規則を明示する。

### 6.11 llama-cpp-python

LLM呼び出しには `llama-cpp-python` の `Llama.create_chat_completion()` を使用する。

モデルロード、GGUF検索及びVRAM解放処理は、次の実装を参考にしてよい。

```text
https://github.com/huchukato/ComfyUI-QwenVL-Mod/blob/main/AILab_QwenVL_GGUF_PromptEnhancer.py
```

ただし、参照実装にある写真プロンプト用の出力クリーナー、写真プロンプト用の再試行処理及びMarkdown禁止処理をそのまま流用してはならない。

特に次の処理は今回の縮小版Markdown構造を破壊する可能性があるため、新しい構造検証処理へ置き換える。

- `clean_model_output(..., mode="prompt")`
- `prompt_output_guard()`
- 単一の写真プロンプト段落を要求するフォールバック
- 箇条書き、見出しまたはMarkdownを禁止するフォールバック

### 6.12 推奨生成パラメータ

翻訳の再現性を優先し、既定値を次のようにする。設定ファイルまたは呼び出し側から上書き可能としてよい。

| パラメータ | 既定値 |
| --- | --- |
| `temperature` | `0.1` |
| `top_p` | `0.9` |
| `repeat_penalty` | `1.05` |
| `seed` | 固定値またはユーザー指定値 |
| `n_ctx` | モデル設定値。入力と出力の合計が収まること |
| `max_tokens` | バッチ長から算出し、設定上限以内とする |

Qwen3のchat templateが対応している場合はthinkingを無効化する。対応していない場合は `/no_think` をユーザーメッセージへ追加する方式を使用してよい。ただし `/no_think` を翻訳対象本文へ混入させてはならない。それでも応答先頭へ正常に閉じた `<think>...</think>` が1個付加された場合だけ、そのブロックを制御ラッパーとして破棄し、後続の翻訳ストリームへ通常の完全検証を行ってよい。途中にあるthinking、未閉鎖、複数ブロック及び後続翻訳が空の応答は失敗とする。

### 6.13 翻訳結果の検証

LLMJ2Eは、翻訳結果を採用する前に次を検証する。

- 入力本文と出力本文が一対一で対応している。
- 本文の追加、欠落、重複及び順序変更がない。
- すべての区間先頭プレースホルダが完全一致し、入力と同じ順序で各1回だけ出現する。ディレクティブプレースホルダは存在するものが入力順に各1回以下でなければならず、それだけが欠落した場合はPython側のブロック情報から復元してよい。
- 各一時トークンが完全一致し、入力と同じ回数だけ出現する。
- 別区間の保護プレースホルダが移動していない。
- 最初の構造プレースホルダより前及びディレクティブ直後へ文章が追加されていない。
- 未解決の一時トークンが残っていない。
- コードフェンス、説明、分析及び前置きが付加されていない。
- 正規形Markdown再構築後のディレクティブ数が入力と一致する。
- 正規形Markdown再構築後の箇条書き数が入力と一致する。
- 保護領域を除く日本語文章が残っていない。

最初の検証に失敗した場合、同じ保護済み入力に対して1回だけ制約を強めた再試行を行ってよい。再試行にも失敗した場合は `LLMJ2ETranslationError` 相当の例外を発生させ、破損したMarkdownをMDPARSEへ渡してはならない。

## 7. 正規形英語縮小版Markdown

LLMJ2EがMDPARSEへ渡すMarkdownは、次の形式だけを使用する。

```text
# Subjects
* SUBJECT_DEFINITION

# Common
* COMMON_PROMPT

# Scene 5sec
* SCENE_PROMPT

# Scene 5sec CONTINUE
* SCENE_PROMPT
```

正規形ディレクティブは次の3種類とする。

```text
# Subjects
# Common
# Scene [Nsec] [CONTINUE]
```

## 8. MDPARSE

### 8.1 役割

`MDPARSE` は正規形英語縮小版Markdownを行単位でパースし、Python上の `Emd` を生成する。

MDPARSEは翻訳、タグ変換、読み上げ文字列変換及びJSON生成を行わない。

### 8.2 Pythonデータ構造

実装上の論理構造を次のように定義する。

```python
from dataclasses import dataclass, field


@dataclass
class Scene:
    duration: int = 5
    is_continue: bool = False
    shots: list[str] = field(default_factory=list)


@dataclass
class Emd:
    common_prompt: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
```

Python内部の配列インデックスは0ベースとする。

- `subjects[0]` は `<Subject 1>` に対応する。
- `scenes[0]` はJSONの `scene_1` に対応する。
- `shots[0]` はJSON中の `[Shot 1]` に対応する。

MiniMax H3向けの番号へ変換する責務はJSONGENが持つ。

### 8.3 パーサーステート

パーサーは次の状態を持つ。

```text
OUTSIDE
SUBJECTS
COMMON
SCENE
```

認識済みディレクティブが現れた場合、現在の状態を終了して新しい状態へ遷移する。

### 8.4 Subjectsパース

行頭が完全一致で `# Subjects` の場合、`SUBJECTS` 状態へ遷移する。

`SUBJECTS` 状態で `* ` から始まる行を検出した場合、`* ` を除去した残りの文字列を `emd.subjects` の末尾へ追加する。

入力順序を変更してはならない。

5個以上のSubject定義を読み込んでもよいが、標準の `<Subject N>` 対応範囲は1～4であるため、5個目以降について警告する。

### 8.5 Commonパース

行頭が完全一致で `# Common` の場合、`COMMON` 状態へ遷移する。

`COMMON` 状態で `* ` から始まる行を検出した場合、`* ` を除去した残りの文字列を `emd.common_prompt` の末尾へ追加する。

入力順序を変更してはならない。

### 8.6 Sceneパース

`# Scene` から始まり、正規形シーンディレクティブの文法に一致する行を検出した場合、次の既定値で新しい `Scene` を生成する。

```python
Scene(duration=5, is_continue=False, shots=[])
```

#### duration

`Nsec` が存在する場合、`N` を10進整数として読み込み `Scene.duration` に設定する。

- 有効範囲は1～60とする。
- 数値としてJSONへ出力し、文字列にはしない。
- 正規形外の値はLLMJ2Eで除外されるべきだが、MDPARSEでも防御的に検証する。
- 不正値の場合は警告し、既定値5を使用する。

#### is_continue

`CONTINUE` が存在する場合、`Scene.is_continue = True` とする。存在しない場合は `False` とする。

シーン1で `CONTINUE` が指定され、外部動画コンテキスト対応が実装されていない場合は警告し、`False` へ変更する。

#### shots

`SCENE` 状態で `* ` から始まる行を検出した場合、`* ` を除去した残りの文字列を現在の `Scene.shots` の末尾へ追加する。

### 8.7 タグの扱い

MDPARSEはタグの内部構造を解釈しない。

次を含むすべての文字列を入力どおり格納する。

- `<Picture N>`
- `<Video N>`
- `<Audio N>`
- `<Subject N>`
- `<d>...</d>`
- `\<`、`\>`、`\[`、`\]` のようなエスケープ済み文字

MDPARSEでタグ、空白、句読点及びバックスラッシュを変更してはならない。

### 8.8 繰り返しディレクティブ

ディレクティブは任意の順序で複数回記述できる。

- 複数の `# Subjects` は `emd.subjects` の末尾へ追加する。
- 複数の `# Common` は `emd.common_prompt` の末尾へ追加する。
- `# Scene` は出現するたびに新しいシーンを `emd.scenes` の末尾へ追加する。

この仕様はパーサーを単純な行ループとして実装できるようにするためのものである。

## 9. JSONGEN

### 9.1 役割

`JSONGEN` は、MDPARSEが生成した `Emd` をMiniMax H3 Contex-Loop Plan用JSONへ変換する。

JSONを文字列連結で手書きしてはならない。Pythonの `json.dumps()` または同等の標準JSONシリアライザを使用する。

推奨設定：

```python
json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
```

### 9.2 トップレベル構造

JSONGENは次のトップレベル構造を必ず生成する。

```json
{
  "prompt_prefix": "...",
  "defaults": {
    "duration_seconds": 5,
    "steps": 8
  },
  "shots": []
}
```

### 9.3 prompt_prefix

`prompt_prefix` は、`emd.common_prompt` の各要素をLFで結合した1個の文字列とする。

```python
prompt_prefix = "\n".join(emd.common_prompt)
```

要素間へ内容の追加、要約、句読点の補完または空白の挿入を行わない。

`emd.common_prompt` が空の場合は空文字列 `""` を設定する。

### 9.4 defaults

JSONGENは `defaults` を必ず生成し、次の値だけを設定する。

```json
"defaults": {
  "duration_seconds": 5,
  "steps": 8
}
```

本バージョンのJSONGENは、Contex-Loop仕様に存在するその他のdefaultsパラメータを生成しない。

`duration_seconds` は固定値 `5` とする。`steps` は呼び出し側から1～10000の整数で指定でき、未指定時は `8` とする。Boolean及び整数以外の値を許可しない。

### 9.5 shots

`shots` は、`emd.scenes` と同じ順序のJSONオブジェクト配列とする。

各シーンには、最低限次を設定する。

```text
id
prompt
duration_seconds
```

### 9.6 id

`emd.scenes` の0ベースインデックスを `i` とした場合、次の値を設定する。

```python
scene_id = f"scene_{i + 1}"
```

例：

```text
scene_1
scene_2
scene_3
```

ゼロ埋めは行わない。

### 9.7 duration_seconds

`Scene.duration` の整数値をそのまま `duration_seconds` に設定する。

文字列へ変換してはならない。

### 9.8 継続とリセット

`continuation_mode` の省略はContex-Loop Planノード側の設定継承を意味するため、非継続シーンを表す目的には使用できない。

#### 非継続シーン

`Scene.is_continue == False` の場合、前シーンから映像及び生成音声コンテキストが継承されないよう、次を出力する。

```json
"context_length": 0,
"audio_context_length": 0
```

`continuation_mode` は出力しない。

#### 継続シーン

`Scene.is_continue == True` の場合、次を出力する。

```json
"continuation_mode": "guide"
```

`context_length` 及び `audio_context_length` は出力せず、Contex-Loop Planノード側の設定を継承する。

シーン1は通常、非継続シーンとして出力する。

### 9.9 使用Subjectの抽出

各シーンについて、そのシーンの `Scene.shots` だけから使用Subject番号を抽出する。

`emd.common_prompt` からSubject番号を抽出してはならない。これは、あるシーンで使用していない参照Subjectの定義が混入することを防ぐためである。

共通プロンプトにしか登場しないSubjectは、そのシーンの `subject_definitions` 生成対象にならない。定義が必要なSubjectは各シーンのショット本文にも明示することを入力文書の作成規則とする。

抽出対象は、エスケープされていない次の形式とする。

```text
<Subject N>
```

抽出時は次の領域を除外する。

- `<d>...</d>` の内部
- `\<Subject N\>` のようにエスケープされた文字列

同じSubjectが複数回登場しても、定義は1回だけ出力する。

抽出したSubject番号は重複を除去し、数値の昇順に並べる。

```python
referenced_subjects = sorted(set(referenced_subjects))
```

### 9.10 未定義Subject

参照された `<Subject N>` に対応する `emd.subjects[N - 1]` が存在しない場合、次のように処理する。

- 警告ログを出力する。
- Subject定義の挿入をスキップする。
- ショット本文中の `<Subject N>` は変更せず残す。
- JSON生成全体は中止しない。

これにより、未定義Subjectの最終的な扱いをMiniMax H3へ委ねつつ、Pythonの `IndexError` を防ぐ。

### 9.11 subject_definitions

使用Subject番号ごとに、次の文字列を生成する。

```python
definition = f"<Subject {n}> is {emd.subjects[n - 1]}"
```

生成前に、そのシーンの `Scene.shots` だけを検査して発声指示の有無を決定する。`emd.common_prompt` はこの判定に使用しない。

次のいずれかが1個以上存在するシーンは発声ありとする。

- エスケープされていない有効な `<d>...</d>` 発話領域
- 否定されていない `say`、`says`、`said`、`saying`、`speak`、`talk`、`utter`、`whisper`、`shout`、`yell`、`murmur`、`groan`、`grumble`、`chant`、`sing`、`announce`、`exclaim`、`reply`、`respond`又は`vocalize`の活用形

`does not speak`、`without speaking`、`no one says`等、発声動詞が直前の否定表現に支配される場合は発声ありとして扱わない。

シーン内の全ショットに発声指示がない場合、そのシーンへ挿入する各Subject定義から、エスケープされていない `<Audio N>` とそれを含む音声参照句を削除する。外観、衣装、身体的特徴、`<Picture N>`、`<Video N>`及び`<Subject N>`の記述は保持する。音声参照以外の定義が空になった場合は `a character.` を使用する。これにより、無発声シーンでMiniMax H3がAudio参照を契機にランダムな音声を生成することを防ぐ。

シーン内に1個でも発声指示がある場合は、そのシーンのSubject定義に含まれるAudio参照を変更しない。

定義の順序はSubject番号の昇順とする。

すべての定義をLFで結合し、先頭へ `subject_definitions:` を付けた1個の文字列を生成する。

```python
subject_block = "subject_definitions:"
if definitions:
    subject_block += "\n" + "\n".join(definitions)
```

参照Subjectが存在しない場合も、`subject_definitions:` だけの文字列を `prompt` の先頭要素として出力する。

### 9.12 ショット文字列

各 `Scene.shots` の0ベースインデックスを `i` とした場合、次の接頭辞を付与する。

```python
shot_text = f"[Shot {i + 1}] {scene.shots[i]}"
```

例：

```text
[Shot 1] <Subject 1> turns around.
[Shot 2] <Subject 2> bows.
```

`[Shot2]` のように空白を省略してはならない。

### 9.13 prompt配列

各シーンの `prompt` は次の順序で構成する。

1. `subject_definitions:` を含むSubject定義ブロック
2. `[Shot 1] ` を付けた最初のショット
3. `[Shot 2] ` 以降のショット
4. BGM生成を無効化する固定文字列 `non_diegetic_music:\nN/A`

```python
prompt = [subject_block]
prompt.extend(numbered_shots)
prompt.append("non_diegetic_music:\nN/A")
```

各要素はJSON文字列としてシリアライズする。手作業でダブルクォートやバックスラッシュを追加してはならない。

固定文字列はショット数や継続設定にかかわらず全シーンへ1回だけ追加し、必ず `prompt` の最終要素とする。ここでのLFはPython文字列中の改行であり、JSONシリアライズ時には `\n` として表現される。

### 9.14 JSONの厳格性

生成JSONは次を満たさなければならない。

- キー及び文字列をダブルクォートで囲む。
- コメントを含めない。
- 末尾カンマを含めない。
- 文字列中の改行はJSONとして正しくエスケープする。
- `duration_seconds` 及び `steps` は整数として出力する。
- `json.loads()` で再読込できる。
- `shots` は1個以上128個以下とする。

シーンが0個の場合、または128個を超える場合は致命的エラーとする。

## 10. エラー処理

### 10.1 致命的エラー

次の場合は処理を中止し、不完全なJSONを出力しない。

- 入力ファイルをUTF-8として読み込めない。
- 閉じていない、余分な、または入れ子になった日本語鉤括弧がある。
- 閉じていない、または対応しない `<d>` / `</d>` がある。
- LLM出力と入力した箇条書き区間の対応を検証できない。
- 保護トークンが欠落、重複または変更された。
- LLM再試行後も日本語の通常文章が残っている。
- シーン数が0または129以上である。
- 生成結果を `json.loads()` で再読込できない。

### 10.2 警告して継続

次の場合は警告を出し、定義されたフォールバックで処理を継続する。

- 未知のディレクティブまたは未知の行
- セクション内の非箇条書き行
- 範囲外のシーン秒数：5秒へフォールバック
- 旧形式または不正なシーンオプション：5秒・非継続へフォールバック
- シーン1の無効な `CONTINUE`：非継続へフォールバック
- 5個目以降のSubject定義
- 未定義の `<Subject N>`：定義挿入をスキップ
- 有効範囲外の参照番号：文字列を保持したまま警告

## 11. 入出力例

### 11.1 日本語入力

```text
# サブジェクト
* <Picture 1>を外観参照、<Audio 1>を声質参照として使用する人物。
* <Picture 2>を外観参照、<Audio 2>を声質参照として使用する人物。

# 共通プロンプト
* <Subject 1>と<Subject 2>は左右に並んでいる。
* 背景は近代的なオフィスビル街。

# シーン 5秒
* <Subject 1>が回転し、「ようこそ！ミニマックス エイチスリーへ」と言う。
* <Subject 2>が御辞儀し、「よろしくおねがいします」と言う。

# シーン 5秒 継続
* <Subject 1>が手を上げ、「日本語で書いたら」と言う。
* <Subject 2>が手を上げ、「楽ですね」と言う。
```

### 11.2 LLMJ2E正規形出力

```text
# Subjects
* a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.
* a character whose appearance is based on <Picture 2> and whose voice is based on <Audio 2>.

# Common
* <Subject 1> and <Subject 2> stand side by side.
* The background is a modern office-building district.

# Scene 5sec
* <Subject 1> turns around and says, <d>[Japanese]ようこそ！ミニマックス エイチスリーへ</d>.
* <Subject 2> bows and says, <d>[Japanese]よろしくおねがいします</d>.

# Scene 5sec CONTINUE
* <Subject 1> raises a hand and says, <d>[Japanese]日本語で書いたら</d>.
* <Subject 2> raises a hand and says, <d>[Japanese]楽ですね</d>.
```

### 11.3 JSON出力

```json
{
  "prompt_prefix": "<Subject 1> and <Subject 2> stand side by side.\nThe background is a modern office-building district.",
  "defaults": {
    "duration_seconds": 5,
    "steps": 8
  },
  "shots": [
    {
      "id": "scene_1",
      "prompt": [
        "subject_definitions:\n<Subject 1> is a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.\n<Subject 2> is a character whose appearance is based on <Picture 2> and whose voice is based on <Audio 2>.",
        "[Shot 1] <Subject 1> turns around and says, <d>[Japanese]ようこそ！ミニマックス エイチスリーへ</d>.",
        "[Shot 2] <Subject 2> bows and says, <d>[Japanese]よろしくおねがいします</d>.",
        "non_diegetic_music:\nN/A"
      ],
      "duration_seconds": 5,
      "context_length": 0,
      "audio_context_length": 0
    },
    {
      "id": "scene_2",
      "prompt": [
        "subject_definitions:\n<Subject 1> is a character whose appearance is based on <Picture 1> and whose voice is based on <Audio 1>.\n<Subject 2> is a character whose appearance is based on <Picture 2> and whose voice is based on <Audio 2>.",
        "[Shot 1] <Subject 1> raises a hand and says, <d>[Japanese]日本語で書いたら</d>.",
        "[Shot 2] <Subject 2> raises a hand and says, <d>[Japanese]楽ですね</d>.",
        "non_diegetic_music:\nN/A"
      ],
      "duration_seconds": 5,
      "continuation_mode": "guide"
    }
  ]
}
```

## 12. 受入テスト

実装は、最低限次のテストを通過しなければならない。

### 12.1 改行とセクション

1. LF入力とCRLF入力から同じ `Emd` 及び同じJSONが生成される。
2. 最終改行がなくても最後の箇条書きとシーンが確定する。
3. 最終空行がなくても最後のシーンが確定する。
4. 3個以上連続する空行を1個のセクション終端として扱える。
5. 空行なしで次の認識済みディレクティブが登場しても状態遷移できる。
6. `Subjects`、`Common` 及び `Scene` が任意の順序で再登場しても末尾へ正しく追加される。

### 12.2 シーンオプション

1. `# シーン` は5秒、非継続になる。
2. `# シーン 1秒` を受理する。
3. `# シーン 60秒` を受理する。
4. `0秒`、`61秒` 及び非数値は警告され、5秒へフォールバックする。
5. `# シーン 継続` は5秒、継続になる。
6. シーン1の `継続` は外部コンテキスト未対応時に警告され、非継続になる。
7. 非継続シーンに `context_length: 0` と `audio_context_length: 0` が出力される。
8. 継続シーンだけに `continuation_mode: "guide"` が出力される。

### 12.3 タグと台詞

1. すべての有効な参照タグが1文字も変化しない。
2. 既存の `<d>...</d>` が1文字も変化しない。
3. 日本語鉤括弧内部が翻訳されず `<d>[Japanese]...</d>` になる。
4. 一行中の複数の日本語台詞がすべて変換される。
5. 台詞内部の `<`、`>`、`[`、`]` が1回だけエスケープされる。
6. 既にエスケープされた特殊文字が二重エスケープされない。
7. 台詞内部の `\<Subject 1\>` が使用Subjectとして抽出されない。
8. 閉じていない鉤括弧及びダイレクトスピーチタグで処理が中止される。

### 12.4 Subject定義

1. 同じSubjectが複数ショット及び同一ショット内で複数回使用されても定義は1回だけになる。
2. Subject定義は番号の昇順になる。
3. シーンで使用していないSubject定義が混入しない。
4. Commonにだけ存在するSubjectは自動的に定義へ追加されない。
5. 未定義Subjectで `IndexError` が発生しない。
6. 未定義Subjectタグはショット本文へ残る。
7. Subjects翻訳結果が `<Subject N> is ` に文法的に接続できる。
8. 発声指示のないシーンではSubject定義からAudio参照句が削除され、外観参照及び身体的特徴が維持される。
9. `<d>...</d>`又は否定されていない発声動詞があるシーンではAudio参照が維持される。
10. `does not speak`、`without speaking`及び`no one says`は発声指示として扱われない。

### 12.5 LLM出力検証

1. LLMがコードフェンスを付けた場合に検証エラーになる。
2. LLMが区間先頭プレースホルダを追加、削除または並べ替えた場合に検証エラーになる。
3. LLMが保護トークンを変更した場合に検証エラーになる。
4. 1回の再試行後も不正な場合は例外になり、JSONを出力しない。
5. 通常文章は英語になり、日本語は保護された読み上げ領域内にだけ残る。
6. 長い応答が途中で欠落した場合、検証できた区間を保持し、欠落区間及び境界未確定の隣接区間だけを再試行する。
7. 再試行後にScene区間の直接発話プレースホルダだけが欠落した場合、復元辞書から補完して完全検証できる。
8. 再試行後にSubject、Picture、Video又はAudioプレースホルダが欠落した場合は補完せず例外になる。

### 12.6 JSON

1. `json.loads()` で生成JSONを再読込できる。
2. 末尾カンマが存在しない。
3. 日本語台詞が `ensure_ascii=False` により人間が読める状態で保存される。
4. 論理バックスラッシュがJSON上で正しくエスケープされる。
5. `scene_1` から連番で一意のIDが生成される。
6. `[Shot 1]` からシーン内で連番になる。
7. `duration_seconds` 及び `steps` がJSON整数になる。
8. シーン数0及び129以上を拒否する。
9. 各シーンの `prompt` 最終要素が `non_diegetic_music:\nN/A` になる。

## 13. ログ

ログには最低限次を含める。

- 読み込んだディレクティブ数
- 読み込んだSubject数、Common行数及びシーン数
- LLMへ渡した翻訳対象区間数及び推論回数
- LLM検証失敗と再試行の有無
- 範囲外オプション、未知の行及び未定義Subjectの警告
- JSON出力先

ログへモデルの機密情報、認証情報または不要なプロンプト全文を出力してはならない。デバッグモードでは、保護領域を伏せた翻訳単位を出力してよい。

## 14. 依存関係とライセンス

主な依存関係は次のとおりとする。

- Python 3.11以降
- `llama-cpp-python`
- Python標準ライブラリの `json`、`re`、`dataclasses`、`pathlib`、`logging`

`ComfyUI-QwenVL-Mod` のコードを複製または改変して組み込む場合は、同プロジェクトのGPL-3.0ライセンス、著作権表示及び配布条件に従う。

APIの使用方法だけを参考にして独自実装する場合でも、参考元URLをドキュメントへ記載することを推奨する。

## 15. 完了条件

次をすべて満たした場合、`cl_japanese2json` の初期実装を完了とする。

1. 本仕様の3モジュールが分離して実装されている。
2. Qwen3 8B用システムプロンプトが独立したUTF-8テキストファイルとして存在する。
3. LLMが構文及びJSONを生成せず、通常文章の翻訳だけを担当する。
4. タグ及び日本語読み上げ領域が決定論的に保護される。
5. 非継続と継続がJSON上で明確に区別される。
6. 使用していないSubject定義が各シーンへ混入しない。
7. 生成JSONがMiniMax H3 Contex-Loop Planへ入力可能である。
8. 第12章の受入テストがすべて成功する。
