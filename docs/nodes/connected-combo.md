# CL Connected Combo

`selected_string`の接続先にあるCOMBO候補を取得し、自身の動的COMBOへ自動反映します。サブグラフの入力・出力境界及びRerouteを経由する配線も追跡します。

Plannerの`model_name_override`、`visual_enrichment_profile_override`、Prompt Enhancerの各override等へ接続します。候補一覧の手入力欄と左側入力ソケットはありません。

複数の接続先へ分岐した場合は、全候補と順序が完全に一致するときだけ利用できます。未接続、異なる候補への分岐、候補元を宣言していない自由STRING入力は停止エラーです。入力名から候補を推測しません。

詳細は[Connected Combo仕様](../spec/cl_connected_combo_spec.md)を参照してください。
