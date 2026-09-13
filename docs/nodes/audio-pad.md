# CL Audio Pad / CL Audio Pad Pair

MiniMax H3のSource Timelineへ渡す音声を切断、リサンプル又は音量変更せず、同じdtype、デバイス、バッチ、チャンネル及びサンプルレートのPCM値`0.0`で延長します。

## Pairの推奨接続

```text
Full Mix ──> CL Audio Pad Pair.audio_a ──> H3 Audio Tracks.full_mix
Vocal ─────> CL Audio Pad Pair.audio_b ──> H3 Audio Tracks.vocals
元Vocal ────────────────────────────────> H3 Lip-Sync Options.voice
```

基本設定は`h3_frame_mode=auto_safe`、`pad_position=end`、`extra_padding_seconds=0`です。短い側だけを最長入力、UI目標及びH3安全フレーム目標の最大尺まで延長します。Lip-Sync OptionsとVocal解析には元ボーカルを使い、パディング済み音声はSource Timeline側だけへ渡します。

`auto_safe`は最長入力又はUI目標を整数秒へ切り上げ、24fps換算後に最大16フレームの安全余裕を加えます。`exact_frames`は`h3_target_frames`を絶対目標とし、`disabled`はH3フレーム計算を行いません。

Plan出力をAudio Padへ戻す配線は依存サイクルになるため使用しません。詳細は[Audio Pad仕様](../spec/cl_audio_pad_spec.md)を参照してください。
