# CL Scene Limiter 仕様

## 1. 目的

`CL Scene Limiter (Reduced Markdown)`は、動画生成テスト用に日本語縮小Markdownの指定開始番号から連続した指定数のSceneだけを残す。LLMは使用せず、入力本文、参照タグ、ディレクティブ及びコメントを翻訳又は再構成しない。

## 2. ノード契約

- 登録名: `CLSceneLimiter`
- 表示名: `CL Scene Limiter (Reduced Markdown)`
- カテゴリ: `MiniMax H3/Prompt Tools`
- 入力`reduced_markdown`: 日本語縮小MarkdownのSTRINGソケット
- 入力`scene_limit_count`: `scene_start_number`から最大何Sceneを残すか。整数1～128、既定1
- 入力`disable`: Boolean、既定False。Trueでは制限とMarkdown検証をバイパスする
- 入力`scene_start_number`: 最初に残すSceneの1始まり通し番号。整数1～128、既定1
- 出力`limited_markdown`: Scene制限後のSTRING

要求範囲の終端が実際の最終Sceneを越える場合は、エラーにせず最終Sceneで抽出を打ち切る。`scene_start_number`自体が実際のScene範囲を越える場合は停止エラーとする。開始番号が1で要求範囲が全Sceneを覆う場合だけ、改行コードと末尾改行を含めて入力文字列をそのまま返す。

`disable=True`では`scene_limit_count`と`scene_start_number`を参照せず、Scene検出、Cコメント解析及びMarkdown字句検証を一切行わない。空文字列又は未完成の編集途中テキストも含め、STRING入力をそのまま返す。`disable`自体がBooleanでない場合及び入力がSTRINGでない場合だけエラーとする。

`CL MV Prompt Planner (GGUF)`の直前へ接続してよい。この場合、元の`// シーン N`とソース音声上の絶対範囲を変更せず渡すため、Plannerは選択範囲だけをLLMで計画し、出力にも同じScene番号を維持する。Scene 2以降から始めたとき、先頭Sceneの`継続`は「切り出し前のSceneから続く」という固定メタデータとして受理される。ただし、切り出された入力だけでは前Sceneの映像状態を利用できないため、継続性の再現はPlanningBriefと当該Scene内の記述に依存する。

## 3. Scene境界

コメント除去後の物理行が`# シーン`又は`# シーン ...`である場合だけSceneディレクティブとして数える。Cスタイルコメント内に記載された偽のScene、台詞又は通常本文中の同名文字列はSceneとして数えない。入力全体は既存の日本語縮小Markdown字句解析器で先に検証する。

現在のテンプレートが出力する`// シーン N`が対応する`# シーン`の直前のコメント領域にあり、Nが実際の1始まりScene順と一致する場合、その番号コメントをScene開始境界に含める。したがって保持Sceneの番号・検出状態・Sunoセクション・歌詞等のコメントは残り、最初に除外するSceneの番号コメントは残らない。

明示的な`// シーン N`がない場合は`# シーン`行を境界とする。関連先を機械的に判定できない直前コメントを推測して削除しない。

## 4. 保持規則

- `# サブジェクト`、`# 保持分析`及び`# 共通プロンプト`と、その本文・コメントをそのまま残す。
- `scene_start_number`より前のSceneは除外し、指定Sceneの開始境界から連続する最大`scene_limit_count` Sceneをそのまま残す。
- 保持対象Scene内部の`//`、`/* ... */`、歌詞、検出状態、Shot及び音響を削除又は変更しない。
- 選択範囲より後の最初のScene開始境界以後は出力しない。
- CRLF/LF、空白、参照タグ及び末尾改行を不要に正規化しない。

## 5. ログとエラー

正常終了時はPython logger名`cl_scene_limiter`から、ANSIシアン色の`[cl_scene_limiter] success:`ログを出す。通常の制限時は開始Scene番号、指定上限及び出力文字数、`disable=True`ではバイパスと無変更返却を記録し、本文自体はログへ出さない。

空入力、1未満又は128を超える開始番号・limit、Boolean、実際のScene数を越える開始番号、Cコメント構文エラー、日本語縮小Markdown構文エラー又はSceneなしは停止エラーとする。
