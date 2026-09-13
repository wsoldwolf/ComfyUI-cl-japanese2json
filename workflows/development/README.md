# Development Workflows

> [!WARNING]
> このディレクトリのワークフローは、ノード、プロンプト形式、コンパイラ及び接続構成の開発・検証用です。通常利用を想定したものではなく、必ずしも動作しません。

日付付きファイルは検証時点のスナップショットです。古いノード仕様、廃止済みの入力順序や既定値、実験中の構文、部分的な配線、保存時のデバッグ設定を含む場合があります。プロジェクト更新後の後方互換性は保証されず、モデル、素材、外部カスタムノードの不足によって停止することもあります。

通常利用では、先に [利用者向けワークフロー](../README.md) を使用してください。

## MV Planner 統合検証

### `minimax_h3_ref2va_20260912_integrated_mv_generator_torii_27b.json`

27B クラスの GGUF モデルを用いた MV Planner と MiniMax H3 連続生成の統合検証用ワークフローです。長いシーン計画、歌詞の視覚化及びカメラ設計の比較に使用した構成です。

### `minimax_h3_ref2va_20260912_integrated_mv_generator_torii_8b.json`

8B クラスの GGUF モデルと軽量プロファイルを用いた統合検証用ワークフローです。限られた VRAM・メインメモリ環境を想定した挙動及び 27B 構成との差を確認するためのものです。

## Vision・プロンプト結合検証

### `minimax_h3_ref2va_20260912_vision_test.json`

CL Image Analyzer (Vision GGUF) による画像解析、Picture 参照の割り当て及び MiniMax H3 参照画像接続を単独で確認するためのワークフローです。

### `minimax_h3_ref2va_vision_merge_test.json`

画像解析結果を縮小 Markdown に変換し、CL Prompt Merger でユーザープロンプトへ統合する処理の検証用ワークフローです。

## 音声・歌詞分割検証

### `minimaxh3_vocal2prompt_20260909_en.json`

英語歌詞とボーカル音声を使用し、Whisper の歌詞アラインメント、シーン分割、SRT 及び Prompt Segment 出力を確認するためのワークフローです。

### `minimaxh3_vocal2prompt_20260909_ja.json`

日本語歌詞とボーカル音声を使用し、Whisper の歌詞アラインメント、シーン分割、SRT 及び Prompt Segment 出力を確認するためのワークフローです。

## 旧コンパイラ・H3 チェーン検証

### `minimax_h3_ref2va_cl_japanese2json_20260829.json`

縮小 Markdown を CL Japanese to JSON でコンパイルし、MiniMax H3 の連続生成へ渡す基本構成の旧検証ワークフローです。

### `minimax_h3_ref2va_cl_japanese2json_complex_cut_20260829.json`

複数ショットや複雑なカット指定を含む縮小 Markdown のコンパイルと H3 連続生成を検証する旧ワークフローです。

### `minimax_h3_ref2va_cl_japanese2json_complex_cut_20260830.json`

複雑なカット指定の検証構成を更新した旧ワークフローです。前日の構成との差分確認を目的としたスナップショットです。

### `minimax_h3_ref2va_cl_japanese2json_no_continue_20260829.json`

Scene 間の継続指定を使用しない構成で、縮小 Markdown のコンパイルと H3 生成を確認する旧ワークフローです。
