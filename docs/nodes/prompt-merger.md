# CL Prompt Merger (Reduced Markdown)

二つのグローバル縮小Markdown断片をLLMなしで統合します。入力に使用できるのは`# サブジェクト`、`# 保持分析`及び`# 共通プロンプト`だけです。

```text
元のPlanningBrief ──> original_markdown ┐
                                        ├─> merged_markdown
Vision等の追加Brief ─> merge_markdown ──┘
```

- どのセクションも省略できます。
- `merge_markdown`が空なら、検証後にoriginalをそのまま返します。
- Subjectと保持分析は`<Subject N>`単位で統合します。
- Commonはmerge側を先頭にし、その後へoriginal側を置きます。
- Scene、Shot、音響又は未知ディレクティブがある入力は停止します。

`common_omit_rules`は`画風|作画|レンダリング`のように`|`又は物理行でリテラル語を指定します。一致するoriginal側Commonバレットだけを除外し、merge側には適用しません。意味的な重複や矛盾は自動解決しません。

詳細は[Prompt Merger仕様](../spec/cl_prompt_merger_spec.md)を参照してください。
