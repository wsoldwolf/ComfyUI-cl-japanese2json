# 実装仕様

このディレクトリの文書は、各ノードと縮小Markdown処理の入出力契約、検証規則及び決定論的処理の正本です。利用方法は[ノードガイド](../nodes/README.md)を参照してください。

## コンパイラと共通実装

- [縮小Markdown・JSONコンパイラ仕様](cl_japanese2json_spec.md)
- [ComfyUIカスタムノード共通実装仕様](cl_japanese2json_comfyui_node_spec.md)
- [プロンプト用語辞書仕様](cl_prompt_term_dictionary_spec.md)
- [プロンプトの意味・情景保全](prompt_semantic_preservation_spec.md): 翻訳の意味監査、背景の固定要素、Plannerへの受け渡し
- [プロジェクト・モジュール配置仕様](project_module_layout_spec.md)

## MV計画

- [MV Prompt Plannerコア仕様](cl_mv_prompt_planner_spec.md)
- [MV Prompt Plannerノード仕様](cl_mv_prompt_planner_comfyui_node_spec.md)
- [Song Bible仕様](cl_mv_prompt_planner_song_bible_spec.md)
- [視覚拡張プロファイル仕様](cl_mv_prompt_visual_profiles_spec.md)

## 入力・補助ノード

- [Image Analyzer Vision GGUF仕様](cl_image_analyzer_vision_gguf_spec.md)
- [Vocal to Prompt Segments仕様](cl_vocal2promptseg_spec.md)
- [Prompt Merger仕様](cl_prompt_merger_spec.md)
- [Prompt Enhancer仕様](cl_prompt_enhancer_spec.md)
- [Scene Limiter仕様](cl_scene_limiter_spec.md)
- [Audio Pad仕様](cl_audio_pad_spec.md)
- [Load Text File仕様](cl_text_file_spec.md)
- [String Combo仕様](cl_string_combo_spec.md)
- [Connected Combo仕様](cl_connected_combo_spec.md)
- [CL 32-bit Seed仕様](cl_seed32_spec.md)
