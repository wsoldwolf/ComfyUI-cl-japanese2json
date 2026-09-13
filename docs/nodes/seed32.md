# CL 32-bit Seed

GGUFノードとMiniMax H3/Context Loopへ同じseedを分岐して接続するためのseed供給ノードです。出力は、両方が受理できる`1～2147483647`に限定されます。

## 使い方

1. 出力`seed`をImage Analyzer、Prompt Enhancer、MV Prompt Planner、Japanese to JSON及びH3/Context Loop側のseed入力へ必要なだけ分岐します。
2. 同じ結果を再現する場合はmodeを`fixed`にするか、`fixed`又は`reuse`ボタンを押します。
3. 実行ごとに変える場合はmodeを`random`にします。
4. `random`ボタンを押すと次に使う値が直ちに表示され、その一回は変更されません。以降は実行ごとに新しいseedになります。

`seed=-1`は次回実行時のランダム生成を意味します。`0`は使用できません。正常実行後は実際に使用した正のseedがウィジェットへ表示され、ワークフローへ保存できます。

H3/Context Loopは64-bit範囲を受理しますが、32-bitの正整数もその有効範囲内です。このノードを使うことでH3のseed空間は狭くなりますが、LLM系ノードと一つの値を共有できます。

詳細は[CL 32-bit Seed仕様](../spec/cl_seed32_spec.md)を参照してください。
