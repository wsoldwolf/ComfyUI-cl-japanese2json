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
- `semantic_guard=global`（既定）はSubject・保持分析・共通プロンプトの訳文を原文と照合し、形状、属性の所有者、比較対象、否定等の意味を監査します。`all`はショット・音響も対象、`off`は従来の構造検証のみです。
- `continuation_context_length`はH3 Generation Profileと同じ値にします。
- 問題解析時だけ`save_debug_output=True`を使用します。

意味監査には追加のLLM推論が必要です。同じ原文の制約は訳文を共有します。不一致の指摘は原文と現在の訳文で個別に再確認し、同一表現への誤った指摘なら訳文を保持します。文字列が異なる修正案にも、採用前に1回の変更レビューを行います。同義の属性説明を重ねるだけで現行訳が既に原文を満たす場合は、修復回数を消費せず元の訳を保持します。実際の訳抜け等は該当箇所だけ最大2回修復し、参照タグ・台詞と意味を再検証します。LLMの判定なので誤検出・見逃しを完全には防げません。変更を試す際は、以前の結果を固定する`keep_last_prompt=False`にしてください。詳細は[意味・情景保全仕様](../spec/prompt_semantic_preservation_spec.md)を参照してください。

LLMが本文を空欄で返した場合は、正常な訳文を保持したまま、その文だけを`retry_max`の範囲で再試行します。空の箇条書きを出力したり、元の指示を削除して継続したりはしません。復旧できない場合は、翻訳段階で該当レコード番号を示して停止します。

モデルのロード条件、再試行及び全パラメータは[ノード実装仕様](../spec/cl_japanese2json_comfyui_node_spec.md)、コンパイル規則は[コンパイラ仕様](../spec/cl_japanese2json_spec.md)を参照してください。
