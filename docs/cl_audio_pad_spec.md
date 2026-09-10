# CL Audio Pad仕様書

## 1. 目的と適用範囲

本書は、ComfyUI標準`AUDIO`へPCM値`0.0`の無音を追加する次の2ノードの詳細仕様を定義する。

- `CL Audio Pad (PCM Silence)`: 1本の音声をUI指定尺、H3用フレーム尺又は別音声の尺まで延長する。
- `CL Audio Pad Pair (PCM Silence)`: 時刻原点が一致した2本の音声を、長い方を基準に同じ時間尺へ延長する。

両ノードは音声を切断、リサンプル、混合、連結、増幅又は正規化しない。必要な場合だけ、元波形と同じdtype、デバイス、バッチ数、チャンネル数及びサンプルレートを維持して無音を追加する。

`H3_CHAIN_PLAN`入力は持たない。音声パディングからPlan生成経路へ戻る依存辺を作れない構造とし、Vocal解析、Planner、Compiler、Contex-Loop Plan及びAudio Padの循環依存を防止する。

## 2. ComfyUI登録

| ノード型 | 表示名 | 関数 | カテゴリ |
| --- | --- | --- | --- |
| `CLAudioPad` | `CL Audio Pad (PCM Silence)` | `pad_audio` | `MiniMax H3/Audio Tools` |
| `CLAudioPadPair` | `CL Audio Pad Pair (PCM Silence)` | `pad_audio_pair` | `MiniMax H3/Audio Tools` |

いずれも`OUTPUT_NODE = False`とする。`llama-cpp-python`、Whisper及びContex-Loopを必須依存にしてはならない。

## 3. AUDIO入力契約

入力はComfyUI標準`AUDIO`辞書であり、少なくとも次を満たさなければならない。

- `waveform.shape`が`[batch, channels, samples]`の3次元である。
- `batch >= 1`、`channels >= 1`、`samples >= 0`である。
- `waveform`が同一dtype及びデバイスのゼロテンソルを作る`new_zeros()`を持つ。
- `sample_rate`がboolではない正の整数である。

無音追加時は`waveform.new_zeros()`で出力波形を作り、元サンプルを指定位置へコピーする。AUDIO辞書のその他のキーは浅いコピーで維持する。追加サンプル数が0の場合は、入力AUDIOを同一オブジェクトのまま返す。

## 4. Plan非依存のH3フレーム目標

H3のfpsは`24`固定とする。`h3_frame_mode`は次の3方式を持つ。

| 値 | 動作 |
| --- | --- |
| `auto_safe` | 最長の入力/UI基準尺を次の整数秒へ切り上げ、その24fpsフレーム数へ16フレームを加える |
| `exact_frames` | `h3_target_frames`を24fpsの絶対的な最小出力フレーム数として使用する |
| `disabled` | H3フレーム目標を使用せず、入力尺、UI指定尺及び任意の追加秒だけで処理する |

`CL Vocal to Prompt Segments`のSceneタイムラインは整数秒である。コアコンパイラは要求タイムライン以上のH3生フレームを割り当て、完成フレーム数と要求フレーム数との差を必ず`0..16`に制限する。従って、標準パイプラインでは次の`auto_safe`目標が、Planを参照せずに不足しない決定論的上限となる。

```text
whole_seconds = ceil(max(input_durations, target_duration_seconds))
h3_target_frames = whole_seconds * 24 + 16
h3_target_samples = round(h3_target_frames / 24 * sample_rate)
```

これはPlanの正確な完成フレーム数そのものではなく、安全な最小音声尺である。余分な末尾無音はContex-Loopの完成動画尺で切られる。手書きPlanなど、Scene合計尺が入力音声の整数秒切り上げより長い場合は`target_duration_seconds`へScene合計秒を指定する。正確な完成フレーム数が既知の場合は`exact_frames`を選び、`h3_target_frames`へその値を入力する。

`h3_target_frames`は0～2,073,600の整数とする。`exact_frames`では1以上を必須とし、他モードでは値を無視する。

## 5. CL Audio Pad (PCM Silence)

### 5.1 入力

required入力は次の順序とする。

| 名前 | 型 | 既定 | 範囲又は候補 | 意味 |
| --- | --- | --- | --- | --- |
| `audio` | AUDIO | 必須 | - | 延長する音声 |
| `target_duration_seconds` | FLOAT | `0.0` | 0～86400、step 0.001 | H3処理前の最小ソースタイムライン尺。0は入力尺を使用 |
| `extra_padding_seconds` | FLOAT | `0.0` | 0～3600、step 0.001 | 全目標を満たした後へ追加する任意マージン |
| `pad_position` | COMBO | `end` | `end`, `start`, `both` | 無音を追加する位置 |
| `h3_frame_mode` | COMBO | `auto_safe` | `auto_safe`, `exact_frames`, `disabled` | 第4章のH3フレーム処理 |
| `h3_target_frames` | INT | `0` | 0～2,073,600 | `exact_frames`用の24fps絶対目標 |

optional入力は`match_audio: AUDIO`だけである。`match_audio`の継続時間を別の最小目標として使い、波形自体は出力へ混ぜない。

### 5.2 目標サンプル数

```text
preliminary_target_samples = max(
    source_samples,
    round(target_duration_seconds * source_sample_rate),
    round(match_audio_duration * source_sample_rate),
)
h3_target_samples = resolve_h3_target(preliminary_target_samples)
base_target_samples = max(preliminary_target_samples, h3_target_samples)
output_samples = base_target_samples
               + round(extra_padding_seconds * source_sample_rate)
```

入力がどの目標より長くても切り詰めない。`extra_padding_seconds`は最大値を選んだ後に一度だけ加える。

### 5.3 pad_position

- `end`: 元音声の開始時刻を維持し、無音をすべて末尾へ追加する。
- `start`: 無音をすべて先頭へ追加し、元音声のタイムラインを後方へ移動する。
- `both`: `floor(padding_samples / 2)`を先頭へ、残りを末尾へ追加する。

H3のSource Timeline又はリップシンク用音声には`end`を使用する。

### 5.4 出力

| 順序 | 名前 | 型 | 意味 |
| --- | --- | --- | --- |
| 1 | `padded_audio` | AUDIO | パディング済み又は元のAUDIO |
| 2 | `original_duration` | FLOAT | 入力尺（秒） |
| 3 | `padded_duration` | FLOAT | 出力尺（秒） |
| 4 | `padding_added` | FLOAT | 追加された無音尺（秒） |
| 5 | `status` | STRING | フレーム目標及び処理結果の診断文字列 |

## 6. CL Audio Pad Pair (PCM Silence)

`audio_a`と`audio_b`は同じ時刻原点及び再生速度を持つ整列済みトラックでなければならない。本ノードはオフセット検出、同期、リサンプル又はミックスを行わない。

required入力は`audio_a`, `audio_b`, `target_duration_seconds`, `extra_padding_seconds`, `pad_position`, `h3_frame_mode`, `h3_target_frames`の順とする。optional入力は持たない。フレームモードと範囲は第4章及び第5.1章と同じである。

共通基準尺は次で求める。

```text
preliminary_duration = max(duration_a, duration_b, target_duration_seconds)
h3_duration = resolve_h3_target(preliminary_duration) / 24
output_duration = max(preliminary_duration, h3_duration)
                + extra_padding_seconds
target_samples_each = round(output_duration * each_sample_rate)
```

各出力は`max(original_samples, target_samples_each)`まで独立して無音補完する。同一サンプルレートならサンプル数を完全一致させる。異なるサンプルレートでは同じ名目時間尺へ個別に丸め、リサンプルしない。

出力は`padded_audio_a`, `padded_audio_b`, `original_duration_a`, `original_duration_b`, `aligned_duration`, `padding_added_a`, `padding_added_b`, `status`の順とする。

## 7. H3及びContex-Loopでの接続

単一音声ではPlanをAudio Padへ接続しない。

```text
Load Audio ──> CL Audio Pad.audio
                CL Audio Pad.padded_audio ──┬─> Loop Start.source_audio
                                             ├─> Current.source_audio
                                             └─> Assemble.source_audio
```

full mixとvocal stemではPairへ同時に入力し、同じ順序でH3 Audio Tracksへ渡す。

```text
Full Mix Load Audio ──> CL Audio Pad Pair.audio_a
Vocal Load Audio ─────> CL Audio Pad Pair.audio_b

padded_audio_a ───────> MiniMax H3 Audio Tracks.full_mix
padded_audio_b ───────> MiniMax H3 Audio Tracks.vocals

Vocal Load Audio ─────> MiniMax H3 Lip-Sync Options.voice
```

Lip-Sync Options及び`CL Vocal to Prompt Segments`には元のvocal stemを接続する。パディング済み出力を解析・Plan生成側へ戻してはならない。

## 8. エラーとログ

入力契約、秒数範囲、`pad_position`、`h3_frame_mode`又は`h3_target_frames`が不正な場合は処理を停止する。自動的な切断又はリサンプルは行わない。

Python logger名は`cl_audiopad`、ユーザー可視ログ接頭辞は`[cl_audiopad]`とする。正常終了ログはANSIシアン色の`[cl_audiopad] success:`とし、元サンプル数、出力尺、H3フレーム目標及び追加位置を含める。無変更の場合も正常終了として記録する。

## 9. 非目標

- 音声内容による同期点の推定。
- 音声の切断、クロスフェード、ループ又は速度変更。
- サンプルレート、チャンネル数又はラウドネスの統一。
- full mixとvocal stemの分離又は再合成。
- H3 Plan自体の生成又は変更。
- 任意の外部Planの正確な完成尺を、Plan情報なしに推測すること。

## 10. テスト要件

実GPUを使用せず、少なくとも次を自動テストする。

- ノード登録、表示名、入出力順序及び既定値。
- `auto_safe`の整数秒切り上げと16フレーム上限。
- `exact_frames`の24fpsサンプル換算。
- UI、`match_audio`及び最長トラックからの目標計算。
- `end`、`start`、`both`のサンプル配置。
- PCM値`0.0`、メタデータ及び入力形状の維持。
- Pairで短い方がどちらでも自動補完されること。
- 異なるサンプルレートの時間尺換算。
- 不正なAUDIO、秒数、フレームモード、フレーム数及び追加位置の拒否。
