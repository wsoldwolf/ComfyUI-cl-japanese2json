# CL String Combo仕様

## 1. 目的

`CL String Combo`は、ユーザーが右クリックのプロパティーパネルで定義した文字列一覧からノード上のコンボで一項目を選択し、その項目をComfyUIの`STRING`として出力する決定論的な補助ノードである。LLM、ファイルI/O及び外部モデルを使用しない。

出力は`CL MV Prompt Planner (GGUF)`の`model_name_override`及び`visual_enrichment_profile_override`へ接続できる。Planner側では空文字列を既存COMBOへのフォールバックとして扱う。

## 2. 公開契約

- ノード型名: `CLStringCombo`
- 表示名: `CL String Combo`
- カテゴリ: `MiniMax H3/Prompt Tools`
- ノードプロパティ: `string_list`（右クリック→プロパティーパネル）
- ノード上の入力widget: `selected_value`（動的コンボ）
- 非表示transport入力: `string_list`
- 出力: `selected_string` (`STRING`)
- 出力ノードではない。

`string_list`はノードを右クリックして「プロパティーパネル」を開いたときだけ編集する。ノード表面には`selected_value`コンボだけを表示し、一覧定義用テキストwidgetを表示してはならない。

ComfyUIの実行要求へノードプロパティを直接渡せないため、同名のバックエンド`STRING`入力を非表示transportとして保持し、Web拡張がプロパティ値を常に同期する。`string_list`プロパティ、非表示transport及び`selected_value`はワークフローへ保存される。`selected_value`はバックエンド定義上は自由な`STRING`だが、`web/cl_string_combo.js`は生成済みSTRING widgetの`type`だけを書き換えてはならない。現行ComfyUIでは描画実装がSTRINGのまま残るため、同じ名前、値及び`widgets_values`位置を持つLiteGraphの実COMBO widgetへ置換する。選択肢をPythonの静的COMBOとして宣言しないため、ユーザー定義値がComfyUIのサーバー検証で拒否されない。

String Comboノード表面には左側の入力ソケットを表示しない。右側の`selected_string`出力をPlannerの`model_name_override`又は`visual_enrichment_profile_override`入力ソケットへ接続する。Planner側のoverrideソケットは外部制御の接続先なので非表示にしない。

## 3. リスト構文

単一の`|`は項目区切り、連続した`||`は項目内のリテラル`|`を表す。左から順に解釈する。

| 入力 | 解釈結果 |
| --- | --- |
| `foo|bar|baz` | `foo`, `bar`, `baz` |
| `foo|bar||baz|qux` | `foo`, `bar|baz`, `qux` |
| `a|||b` | `a|`, `b` |
| `a||||b` | `a||b` |
| `||` | `|` |

各項目の先頭と末尾の空白は除去するが、項目内部の空白、文字種及び大文字小文字は保持する。比較は大文字小文字を区別する。

次は停止エラーとする。

- 空の`string_list`
- 先頭又は末尾の未エスケープ区切り
- `foo||bar`ではなく`foo| |bar`等によって生じた空白だけの項目
- 改行を含むリスト
- 外側空白の除去後に完全一致する重複項目
- 空でない`selected_value`が現在の一覧に存在しない状態

空の`selected_value`は新規ノード及びWeb拡張が無いAPI実行の初期状態としてだけ認め、先頭項目へ解決する。ブラウザではプロパティーパネルで一覧を編集した後も現在値が残っていれば保持し、存在しなければ先頭項目へ切り替える。

初期実装で`string_list`がノード表面のwidgetとして保存されたワークフローを読み込んだ場合、その値を自動的に新しいノードプロパティへ移し、transport widgetを非表示にする。

## 4. 実行とログ

選択値を一要素tupleとしてそのまま返し、内容の正規化、翻訳又は置換は行わない。成功時は選択文字列そのものをログへ出さず、次の形式をANSIシアン色で記録する。

```text
[cl_string_combo] success: selected item N/M
```

`N`は1始まりの選択位置、`M`は項目数である。構文又は選択が不正な場合はComfyUIの入力検証を失敗させ、出力を返さない。

## 5. 配置

```text
node_string_combo/
  node.py
  parser.py
  errors.py
web/
  cl_string_combo.js
tests/
  test_string_combo.py
```

PythonとJavaScriptは同じ走査規則を別々に実装する。正本は本仕様及びPythonパーサとし、テストはエスケープ、空項目、重複、選択値、ノード登録及びWeb拡張の契約を検証する。
