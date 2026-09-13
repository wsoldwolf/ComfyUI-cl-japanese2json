# CL Vocal to Prompt Segments

フルミックスと同じ開始時刻・同じ全長のボーカルステムとSuno Lyricsから、編集可能なSceneテンプレート、SRT及び検証JSONを生成します。

```text
Vocal AUDIO ──> vocal_audio
Lyrics STRING ─> lyrics_text

prompt_text ──> Scene Limiter又はMV Prompt Planner
srt_text ─────> Preview/Save Text
```

`[Intro]`、`[Verse]`、`[Chorus]`等はSRT本文から除外し、後続歌詞の楽曲セクションとして保持します。Lyrics本文をWhisperの実word timestampへ上から順に対応付けます。

## 主な設定

- `whisper_model`: `ComfyUI/models/whisper/`以下のローカル`.pt`
- `language`: 日本語`ja`、英語`en`、自動検出`auto`
- `condition_on_previous_text`: 長尺認識では通常`True`
- `include_lyrics_comments`: Plannerへ歌詞を渡す場合は`True`
- `keep_whisper_loaded`: 既定`False`
- `max_scene_seconds`: Sceneの最大秒数
- `lyrics_match_threshold` / `lyrics_neighbor_threshold`: Lyrics照合閾値
- `cache_mode`: 正常結果の再利用方針
- `seed`: Whisperと再試行の再現性に使用

出力は`prompt_text`、`srt_text`、`segments_json`、`status`です。入力LyricsとSRT本文が完全一致した場合だけ`self test passed`となります。不一致は停止し、不完全な結果を返したり正常キャッシュへ登録したりしません。

末尾が整数秒でない場合は、フルミックスとボーカルを`CL Audio Pad Pair`へ接続してH3 Source Timeline側を補完します。解析とLip-Sync Optionsには元ボーカルを使用します。

詳細は[Vocalノード仕様](../spec/cl_vocal2promptseg_spec.md)を参照してください。
