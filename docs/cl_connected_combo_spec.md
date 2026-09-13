# CL Connected Combo仕様

## 1. 目的

`CL Connected Combo`は、出力配線の接続先が公開している文字列COMBOの列挙値を取得し、自身の動的コンボへ反映して、選択した一項目を`STRING`として返す決定論的なUI補助ノードである。手作業で同じ一覧を二重管理せず、モデル、画風又はPlannerプロファイルの追加・削除を接続元へ追従させる。

既存の`CL String Combo`はユーザーが任意の一覧を右クリックのプロパティーパネルで定義する役割を維持し、変更しない。接続先追従は別ノード`CL Connected Combo`として実装する。

## 2. 公開契約

- ノード型名: `CLConnectedCombo`
- 表示名: `CL Connected Combo`
- カテゴリ: `MiniMax H3/Prompt Tools`
- 表示widget: `selected_value`（動的COMBO）
- 非表示transport: `enum_values_json` (`STRING`)
- 出力: `selected_string` (`STRING`)
- 左側入力ソケットを持たない。
- LLM、モデルロード、ファイルI/O及び候補文字列の意味解析を行わない。

Web拡張は接続先の列挙値をJSON配列として`enum_values_json`へ保存する。Pythonは配線を推測せず、そのスナップショットが空でない一意な文字列配列であることと`selected_value`の所属だけを検証する。これにより保存済みworkflow及びAPI promptでも実行時の値を再検証できる。

## 3. 候補の発見

出力リンクから次の順で接続先を調べる。

1. 接続先入力に対応する実COMBO widgetの現在の`options.values`
2. widgetが入力ソケットへ変換済みの場合、登録済みノード定義にあるCOMBO配列
3. 接続先の自由`STRING`入力が`connected_combo_source`を宣言する場合、その名前の同一ノード内COMBO
4. Rerouteを通過した先
5. サブグラフ入力から内部ノードへ続く全リンク
6. ノードがサブグラフ内にあり出力境界へ接続される場合、当該サブグラフの全ホストインスタンスから外側へ続くリンク

サブグラフは入れ子でも同じ走査を繰り返す。走査深度には上限を設け、訪問済みリンクを再訪しない。サブグラフ定義が複数箇所で使用される場合は、全ホストの実接続先を対象とする。

`connected_combo_source`は自由入力へ暗黙の命名規則を適用しないための明示メタデータである。例えばPlannerの`model_name_override`は`model_name`を、`visual_enrichment_profile_override`は`visual_enrichment_profile`を宣言する。Prompt Enhancerのモデル、画風及び背景overrideも対応する内部COMBOを宣言する。第三者ノードの自由`STRING`入力にこのメタデータが無い場合、名前が似ていても候補を推測しない。

列挙値は空でない一意な文字列だけを対象とする。候補生成関数を発見処理中に実行して副作用を起こしてはならず、現在のwidget配列又は登録済み定義だけを読む。

## 4. 複数接続と未接続

出力が複数のCOMBOへ分岐した場合、列挙値と順序が完全一致するときだけ一つの候補として扱う。いずれかが異なる場合は交差集合や先勝ちを使用せず、コンボに不一致表示を出して`enum_values_json=[]`とする。実行時は停止エラーとなる。

COMBOが見つからない場合も未接続表示と`enum_values_json=[]`を保存し、実行時は接続方法を示す停止エラーとする。一般の自由`STRING`入力、型変換ノード又は任意の処理ノードの先まで推測して走査しない。

接続、切断、サブグラフ編集、ノード定義再読込及び動的候補更新を追従するため、接続イベントに加えて低頻度の署名付き再走査を行う。候補又は接続先が変化しない限りwidget値とworkflow状態を書き換えない。現在の選択が新しい候補にも存在すれば保持し、存在しなければ先頭項目へ切り替える。

## 5. 実行とログ

選択値は一要素tupleとして変更せず返す。成功時は値そのものを漏らさず、ANSIシアン色で次を記録する。

```text
[cl_connected_combo] success: selected connected item N/M
```

不正JSON、空配列、文字列以外、空文字列、重複、過大な候補及び候補外選択は入力検証又は実行を停止する。

## 6. 配置

```text
node_connected_combo/
  node.py
  parser.py
  errors.py
web/
  cl_connected_combo.js
tests/
  test_connected_combo.py
```
