# CL Prompt Merger（Reduced Markdown）仕様

## 1. 目的

`CL Prompt Merger (Reduced Markdown)`は、日本語縮小Markdownのグローバル指示断片をLLMなしで機械的に統合するComfyUIノードである。対象は`# サブジェクト`、`# 保持分析`及び`# 共通プロンプト`だけとし、Scene計画、翻訳、意味的な重複除去又は矛盾の推測を行わない。

本仕様の目的は、画像解析等で生成した追加情報を人間が記述したPlanningBriefへ安全に重ね、後段の`CL MV Prompt Planner (GGUF)`又はSceneを付加した`CL Japanese to JSON (GGUF)`へ渡せる決定論的な断片を生成することである。

## 2. 公開契約

- ノード型名: `CLPromptMerger`
- 表示名: `CL Prompt Merger (Reduced Markdown)`
- カテゴリ: `MiniMax H3/Prompt Tools`
- 出力ノードではない。
- 外部パッケージ、モデル、ネットワーク及びLLMを使用しない。

```python
RETURN_TYPES = ("STRING",)
RETURN_NAMES = ("merged_markdown",)
FUNCTION = "merge_prompts"
CATEGORY = "MiniMax H3/Prompt Tools"
OUTPUT_NODE = False
```

## 3. 入出力

| 名前 | 型 | 既定 | 意味 |
| --- | --- | --- | --- |
| `original_markdown` | STRING forceInput | なし | 統合の基準となるグローバル縮小Markdown断片 |
| `merge_markdown` | STRING forceInput | なし | originalへ追加するグローバル縮小Markdown断片。空を許可する |
| `common_omit_rules` | STRING multiline | 空 | original側Commonを除外するリテラル語。物理行及び`|`で区切る |
| `merged_markdown` | STRING | - | 検証・統合済み日本語縮小Markdown断片 |

入力STRINGは空を許可する。両方が空なら空文字列を返す。NUL又はSTRING以外は停止エラーとする。

## 4. 入力サブセット

両入力が認識するトップレベルディレクティブは次だけである。

```text
# サブジェクト
# 保持分析
# 共通プロンプト
```

各ディレクティブは0又は1回、存在する場合の順序は上記の通りとする。セクションが存在する場合は一個以上の`* `バレットを必要とする。3セクションのいずれも欠落できるため、保持分析だけ又は共通プロンプトだけの断片も許可する。

Scene、Shot、音響、未知の見出し、ディレクティブ外の本文、空バレット、重複ディレクティブ及び順序違反は停止エラーとする。`//`及び`/* ... */`はコア仕様と同じコメント走査を使用し、コメント内の偽ディレクティブは構造として扱わない。不正な入れ子、未閉鎖ブロック及びHTMLコメントは停止エラーである。

## 5. 空マージ

`merge_markdown`が空又はコメントだけであり、意味を持つ3セクションがない場合も、original及びmergeの構文検証とomit規則の検証は行う。検証成功後は`original_markdown`と同一のPython文字列オブジェクトを返し、改行コード、末尾改行、空白及びコメントを変更しない。

空マージは不正なoriginalを通過させるバイパスではない。originalにScene又は未知ディレクティブがあれば停止する。

## 6. Subject統合

original側のSubject番号はコア仕様どおり`# サブジェクト`のバレット順で1から割り当てる。最大4 Subjectとする。

merge側の各Subjectバレットは次の順で対象番号を決める。

1. コメント除去後のバレットに一種類だけ`<Subject N>`があればNを対象とする。
2. `<Subject N>`がなければmerge側のバレット順を対象とする。
3. 異なる複数のSubject番号を一行に含む場合は対象が曖昧なので停止する。

次の先頭セレクタ形式は、疎なSubject番号へ追加するためのマージ入力として許可する。セレクタとコロンは出力から除去する。

```text
* <Subject 2>: 青い外套と銀色の留め具を持つ。
```

originalに対象Subjectが存在する場合は、original本文、半角空白一個、merge本文の順に一行へ連結する。複数のmerge行が同じSubjectを対象とする場合も入力順に連結する。本文の句読点、参照タグ、単語又は意味は変更しない。

originalに対象Subjectがなければmerge本文を新規Subject定義として追加する。ただし最終Subject番号は1から欠番なく連続しなければならない。範囲外又は欠番は停止エラーとする。

機械的統合では同義文の削除、優先度判定、種族、性別、色、個数又は衣装の矛盾解決を行わない。

## 7. Retention統合

保持分析はコア仕様の次の構文だけを受理する。

```text
* <Subject N> 完全に保持: 説明
* <Subject N> 部分的に保持: 説明
* <Subject N> 属性転送 -> <Subject M>: 説明
* <Subject N> 弱い参照: 説明
```

一つの入力内ではSubjectごとに規則一個までとする。originalとmergeの同じSubjectに規則がある場合、関係種別と属性転送先が完全に一致するときだけ、original説明、半角空白一個、merge説明の順に一規則へ統合する。

関係種別又は属性転送先が異なる場合、Pythonはどちらを優先するか推測せず停止する。属性転送元と転送先が同一、番号が1～4以外又は構文不正の場合も停止する。

出力にSubjectセクションが存在する場合、保持元及び属性転送先は出力Subjectとして定義済みでなければならない。Subjectセクションそのものがない保持分析断片は、後段でSubjectと統合する用途のため許可する。

## 8. Common統合

`# 共通プロンプト`は次の順に出力する。

1. merge側の全バレットを元の順序で配置する。
2. omitに一致しなかったoriginal側バレットを元の順序で配置する。

この順序により追加指示をCommon先頭へ置く。完全一致する行も自動削除せず、重複したまま保持する。将来の意味解析を除き、機械的処理は重複又は矛盾を解決しない。

### 8.1 omit規則

`common_omit_rules`は、空白でない各物理行を`|`で分割して検索語とする。

```text
画風|作画|レンダリング
照明
```

上記は`画風`、`作画`、`レンダリング`又は`照明`を含むoriginal側Commonバレットを除外する。merge側Commonには適用しない。これにより、新しい画風を追加しながら古い画風だけを削除できる。

一致判定だけにUnicode NFKC正規化とUnicode casefoldを使用する。出力本文自体は正規化しない。部分文字列のリテラル一致とし、正規表現、否定、AND条件及びエスケープは実装しない。空文字列ならomit無効、同一語の重複は一個へ正規化する。`画風||作画`、行頭又は行末の`|`等、空フィールドを含む規則は入力ミスとして停止する。

omitですべてのoriginal Commonが消え、merge Commonもない場合は`# 共通プロンプト`自体を出力しない。

## 9. コメントと再構成

構文判定はコメント除去後の影テキストで行い、出力本文には元のコメントを残す。独立したコメント及び空行は後続のセクション又はバレットへ付随させ、統合対象のコメントは同じ統合行の直前へ集める。omitされたoriginal Commonバレットにだけ付随するコメントも、そのバレットとともに除外する。

インラインブロックコメントは、再構成に使う元のバレット本文内で保持する。実マージ時の改行コードはoriginalが非空ならoriginal、空ならmergeのCRLF又はLFを使用し、非空出力は一個の末尾改行を持つ。

## 10. エラー条件

- STRING以外又はNULを含む入力
- 未知、重複又は順序違反のディレクティブ
- Scene、Shot又は音響の混入
- ディレクティブ外本文又は空バレット
- `# 共通プロンプト`内の`「」`又は`<d>...</d>`による直接話法
- 不正なCスタイルコメント又はHTMLコメント
- 5個以上のSubject、1～4以外又は欠番のSubject
- merge Subject一行に複数の異なる`<Subject N>`がある
- 不正又は重複した保持規則
- 競合する保持関係又は属性転送先
- 定義済みSubjectがある文書から未定義Subjectを参照する保持規則
- 空フィールドを含むomit規則

エラー時に部分結果を返してはならない。

## 11. ログ

正常終了時だけ`common/logging.py`を使用し、ANSIシアン色で次の件数を記録する。

- Subject数
- Retention規則数
- Common行数
- omitされたoriginal Common行数
- 出力文字数

空マージでは、入力を変更せず返したことと文字数を記録する。例外終了時は成功ログを出さない。プロンプト全文は通常ログへ出力しない。

## 12. 実装配置

```text
node_prompt_merger/
  node.py
  merger.py
  errors.py
tests/
  test_prompt_merger.py
```

専用パーサと中間状態は`node_prompt_merger/`が所有する。確定済みコアコメント構文だけを`node_japanese_to_json.compiler.comments`から再利用し、他ノードのランタイム又はLLM実装をimportしない。

## 13. テスト要件

- ノード型名、表示名、入出力順序及び既定値
- 空マージの同一文字列返却と検証継続
- 欠落セクション及び両入力空
- positional Subjectと明示Subjectの統合
- Subject範囲、欠番及び複数タグ拒否
- Retention説明統合、関係競合及び転送先競合
- merge Common先頭配置
- omitのoriginal限定、NFKC/casefold及び空フィールド拒否
- コメント内の偽ディレクティブとURL保持
- CRLF再構成
- シアン成功ログ
- 有効Sceneを後置した結果が既存コンパイラとJSON生成を通過すること
