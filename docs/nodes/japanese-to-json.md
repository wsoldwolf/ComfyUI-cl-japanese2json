# CL Japanese to JSON (GGUF)

日本語縮小MarkdownをMiniMax H3 Contex-Loop Plan JSONへコンパイルします。LLMは日本語本文の英訳だけを行い、構造とJSONはPythonが生成・検証します。

## 接続

```text
MV Prompt Planner.planned_markdown ──> plain_text
json_text ──> MiniMax H3 Contex-Loop Plan.plan_json_input
```

手書きMarkdown、Vocalノード又はScene Limiterから直接接続することもできます。文法は[コンパイラガイド](../compiler-guide.md)を参照してください。

## 推奨設定

- `keep_model_loaded=False`でH3生成前にGGUFを解放します。
- `speech_guard=strict`を通常設定とし、誤検出を確認しながら通す場合だけ`warn`を使います。
- `continuation_context_length`はH3 Generation Profileと同じ値にします。
- 問題解析時だけ`save_debug_output=True`を使用します。

モデルのロード条件、再試行及び全パラメータは[ノード実装仕様](../spec/cl_japanese2json_comfyui_node_spec.md)、コンパイル規則は[コンパイラ仕様](../spec/cl_japanese2json_spec.md)を参照してください。
