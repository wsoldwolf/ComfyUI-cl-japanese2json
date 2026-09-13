# CL String Combo

ノードを右クリックしてプロパティーパネルを開き、`string_list`へ候補一覧を設定します。ノード表面の動的COMBOから一つを選び、`selected_string`として出力します。

```text
day|night|rain
```

区切りには`|`を使います。項目内でリテラル`|`を使う場合は`||`と書きます。たとえば`foo|bar||baz|qux`は`foo`、`bar|baz`、`qux`の3候補です。

空項目、改行及び重複候補は停止エラーです。一覧更新後も現在値が残っていれば選択を維持し、なければ先頭へ切り替えます。詳細は[String Combo仕様](../spec/cl_string_combo_spec.md)を参照してください。
