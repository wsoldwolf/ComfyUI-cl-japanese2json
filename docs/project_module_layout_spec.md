# プロジェクト・モジュール配置仕様

## 1. 目的

本仕様は、ComfyUIノードを追加してもルート直下へ実装モジュールが増殖せず、各ノードの変更範囲と共有基盤の依存方向を明確に保つための配置規則を定義する。

## 2. 配置規則

- ルート`__init__.py`はノード登録、表示名及び`WEB_DIRECTORY`だけを所有する。
- 各ノードはルート直下の`node_<機能名>/`に置く。
- ノード固有のパーサ、データ構造、例外、プロンプト及びランタイムは、その`node_*`ディレクトリが所有する。
- 2個以上のノードが実際に利用する安定した基盤だけを`common/`へ昇格する。
- `common/`は各`node_*`をimportしない。依存方向は常に`node_* -> common`とする。
- ノード同士の直接importは、既存の確定済み構文を読み取るなど所有関係が明確な場合に限定する。循環importは禁止する。
- ブラウザ拡張はComfyUIの単一`WEB_DIRECTORY`制約によりルート`web/`に置き、ファイル名又はサブディレクトリで所有ノードを区別する。
- テストは`tests/`へ集約し、公開ノード型名と保存済みワークフローの互換性も検証する。

## 3. 現在の構成

```text
common/
  errors.py
  logging.py
  suno.py
  gguf/
    discovery.py
    runtime.py
node_japanese_to_json/
  node.py
  debug_output.py
  compiler/
    comments.py
    errors.py
    structures.py
    protected_text.py
    term_dictionary.py
    llmj2e.py
    mdparse.py
    jsongen.py
    system_prompt.py
    prompts/
      llmj2e_qwen3_8b_system_prompt.txt
  dictionaries/
    prompt_terms.csv
node_vocal_to_prompt_segments/
  node.py
  errors.py
  whisper_discovery.py
  whisper_runtime.py
node_mv_prompt_planner/
  node.py
  debug_output.py
  brief_parser.py
  timeline_parser.py
  placeholders.py
  structures.py
  camera_policy.py
  planning.py
  validation.py
  renderer.py
  prompt_loader.py
  visual_profiles.py
  prompts/
    core/
      song_bible_system_prompt.txt
      lyric_action_system_prompt.txt
      scene_plan_system_prompt.txt
      auxiliary_visual_repair_system_prompt.txt
    profiles/
      performance_only/
        profile.json
        song_bible.txt
        scene_plan.txt
      lyric_visuals_light_8b/
        profile.json
        song_bible.txt
        scene_plan.txt
      lyric_visuals_full/
        profile.json
        song_bible.txt
        scene_plan.txt
node_scene_limiter/
  node.py
  errors.py
node_audio_pad/
  node.py
node_text_file/
  node.py
  errors.py
node_vision_analyzer/
  node.py
  errors.py
  discovery.py
  runtime.py
  image_io.py
  cache.py
  graph_binding.py
  hinting.py
  structures.py
  validation.py
  renderer.py
  debug_output.py
  prompts/
    core/
      observation_system_prompt.txt
tests/
web/
workflows/
```

## 4. 公開互換境界

Pythonファイルの旧パスは内部実装であり互換対象にしない。保存済みComfyUIワークフローが参照する次のノード型名は互換境界として維持する。

- `CLJapaneseToJSONGGUF`
- `CLVocalToPromptSegments`
- `CLAudioPad`
- `CLAudioPadPair`
- `CLLoadTextFile`
- `CLMVPromptPlannerGGUF`
- `CLSceneLimiter`
- `CLImageAnalyzerVisionGGUF`

ディレクトリ変更だけを理由にワークフローJSONへPythonモジュールパスを書き込まない。ワークフローは上記型名と入出力契約によって読み込まれる。

## 5. テスト基準

構成変更の基準点では、次をすべて満たす。

1. 全Pythonファイルが`compileall`を通過する。
2. `python -m unittest discover -v`で全回帰テストが通過する。
3. `tests/test_workflows.py`が全同梱ワークフローを読み取り、現行入力名、縮小Markdown構文及びPlan JSON生成を検証する。
4. ルート登録から既存の全ノード型名を取得できる。

`common/logging.py`は、正常終了したCLノードをComfyUIコンソール上で識別するANSIシアン色の成功ログを共有する。各`node_*`は出力を返す直前だけこのヘルパーを呼び、途中経過を成功として表示してはならない。
