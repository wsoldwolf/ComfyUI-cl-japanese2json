# cl_vocal2promptseg ComfyUIカスタムノード仕様書

## 1. 文書の位置付け

本書は、PCMボーカルステムから人声のない区間と人声のある区間を検出し、`cl_japanese2json`へ入力できる日本語縮小版Markdownのテンプレートを生成するComfyUIカスタムノードを定義する。

本書は次の既存仕様を補足する。

- `docs/cl_japanese2json_spec.md`: 日本語縮小版Markdown及びSource Timelineリップシンクの正本
- `docs/cl_japanese2json_comfyui_node_spec.md`: ノードの独立性、ComfyUI標準`AUDIO`及び`CLAudioPad`の正本

本書と既存仕様が競合する場合は既存仕様を優先する。本ノードは既存文法を拡張せず、既存コンパイラがそのまま受理できるテキストだけを生成する。

元ドラフトの「有性区間」は、音声を含む意味が明確になるよう本書では「有声区間」又は`voiced`と呼ぶ。

## 2. 目的

本ノードは次を自動化する。

1. 汎用の音声ローダー等からComfyUI標準`AUDIO`形式のPCMボーカルステムを受け取る。
2. Suno Lyrics形式の既知歌詞をSTRING入力ソケットから受け取る。
3. PCM振幅から有声フレームと無音フレームを決定する。
4. OpenAI Whisperでボーカルステムを書き起こし、単語時刻を得る。
5. Lyricsの歌詞行を正本として、Whisper書き起こしへ先頭から順に単調整列する。
6. 短い誤検出を除去し、サンプル位置、開始時刻、終了時刻及び継続時間を記録する。
7. UIで指定したScene上限秒数以内になるよう、時間軸を整数秒のSceneへ分割する。
8. 各Sceneを有声又は無音に分類し、解決済み歌詞をCスタイルコメントとして含むSource Timeline方式の日本語プロンプトテンプレートを出力する。
9. Lyricsを本文、Whisperを時刻の根拠とするSRT字幕を文字列として出力する。
10. 検出区間、歌詞整列、生成Scene及び末尾パディング要否を検証用JSONとして出力する。

本ノードはWhisperによる音声認識を行うが、Whisper書き起こしを歌詞本文の正本にはしない。歌詞の生成、翻訳、ビート検出、話者分離及び音源分離は行わない。

## 3. 適用範囲と責務境界

- 入力は、フルミックスと同じ開始時刻を持つボーカルステムを想定する。
- ボーカルステム内の人声らしいエネルギーの有無だけを検出する。検出結果が歌詞又は音素の境界と一致することは保証しない。
- Lyricsの非見出し行を歌詞本文の正本とし、Whisperは対応する音声時刻を求めるためだけに使用する。
- Lyrics全文をWhisperの`initial_prompt`へ渡さない。冒頭の認識脱落を抑えるため、セクション見出しを除いた先頭Lyricsだけを12行かつ160文字以内で渡す。Whisperのプロンプト容量を占有し続けたり、後半の無音部へ架空の歌詞を誘発したりしないよう、`condition_on_previous_text=False`を維持する。
- 出力Markdownは`<Subject 1>`だけを使用する。
- 有声Sceneは`ソースボーカル`で口形を駆動し、無音Sceneは人物発声を禁止する。
- 全Sceneで`ソース音声: 完全維持`を指定する。最終音声の正本はContex-Loopの`source_timeline`へ接続したロック済みフルミックスである。
- 入力ボーカルステム自体を最終ミックスへ追加しない。ボーカルステムは`lip_sync_voice`の口形駆動専用である。
- 本ノードは入力PCMを変更、パディング、切り詰め、リサンプル、正規化、保存又は出力しない。
- 必要な末尾無音は既存`CLAudioPad`で追加する。本ノードは必要量だけを報告する。
- `llama-cpp-python`及びGGUFモデルを使用しない。
- ComfyUI本体、Contex-Loop及び他の`custom_nodes`へ依存したimportを行わない。
- Pythonパッケージを自動インストール、更新又はダウンロードしない。
- Whisper実装は`openai-whisper`パッケージを実行時に遅延importする任意依存とする。パッケージがなくてもカスタムノード全体のimportと登録を成功させる。
- Whisperモデルはローカルチェックポイントだけを使用し、公式モデル名を`load_model()`へ渡して暗黙にダウンロードさせない。

## 4. ノード定義

### 4.1 登録名

```python
NODE_CLASS_MAPPINGS = {
    "CLVocalToPromptSegments": CLVocalToPromptSegments,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLVocalToPromptSegments": "CL Vocal to Prompt Segments",
}
```

既存マッピングへ上記要素を追加し、既存ノードを変更又は削除しない。

### 4.2 クラスメタデータ

```python
RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
RETURN_NAMES = ("prompt_text", "srt_text", "segments_json", "status")
FUNCTION = "build_prompt_segments"
CATEGORY = "MiniMax H3/Prompt Tools"
OUTPUT_NODE = False
```

`build_prompt_segments()`は`(prompt_text, srt_text, segments_json, status)`の4要素tupleを返す。

## 5. INPUT_TYPES

requiredは次の順序とする。

| 名前 | 型 | 既定 | 範囲 | 用途 |
| --- | --- | --- | --- | --- |
| `vocal_audio` | AUDIO | 接続必須 | ComfyUI標準AUDIO | 同期済みPCMボーカルステム |
| `lyrics_text` | STRING | 接続必須 | `forceInput=True` | Suno Lyrics形式の既知歌詞。multiline STRING出力から接続する |
| `whisper_model` | COMBO | 最初の検出モデル | ローカル`.pt`モデルID | OpenAI Whisperチェックポイント |
| `language` | COMBO | `ja` | `ja`, `en`, `auto` | Whisper認識言語。`en`は米国英語を含む英語、`auto`はWhisperの言語検出を使用 |
| `device` | COMBO | `auto` | `auto`, `cuda`, `cpu` | Whisper推論デバイス |
| `keep_whisper_loaded` | BOOLEAN | True | True/False | 同一モデルとデバイスのWhisperインスタンスを再利用する |
| `max_scene_seconds` | INT | 10 | 1～60、step 1 | 生成する1 Sceneの最大整数秒 |
| `silence_threshold_dbfs` | FLOAT | -45.0 | -100.0～0.0、step 0.5 | RMSがこの値以上の解析フレームを有声候補にする |
| `analysis_window_ms` | INT | 20 | 5～200、step 1 | RMS解析窓の長さ |
| `min_voiced_ms` | INT | 120 | 0～5000、step 10 | これ未満の孤立した有声候補を無音へ戻す |
| `min_silence_ms` | INT | 300 | 0～10000、step 10 | 有声区間間にある、これ未満の無音を有声へ結合する |
| `voice_padding_ms` | INT | 80 | 0～2000、step 10 | 確定有声区間の前後へ加える検出余白 |
| `lyrics_match_threshold` | FLOAT | 0.55 | 0.0～1.0、step 0.01 | Lyrics行とWhisper候補範囲を確定する最小類似度 |
| `lyrics_neighbor_threshold` | FLOAT | 0.45 | 0.0～`lyrics_match_threshold`、step 0.01 | 両隣が通常閾値で確定した未解決Lyricsだけに使う救済類似度 |
| `lyrics_search_seconds` | FLOAT | 60.0 | 1.0～600.0、step 1.0 | 現在の整列カーソルから1歌詞行を探索する最大時間 |

optional入力は持たない。

`lyrics_text`は入力ソケット専用で、ノード内へ大きな編集widgetを重複表示しない。空白だけ、又は見出し以外の歌詞行が0件の場合は実行時エラーとする。

`silence_threshold_dbfs`はフルスケールPCM値`1.0`を`0 dBFS`として扱う。分離残留音が有声と判定される場合は値を0へ近づけ、弱い歌声を取りこぼす場合は値を`-100`へ近づける。

## 6. AUDIO入力検証

`vocal_audio`は辞書であり、次を満たさなければならない。

- `waveform`が存在し、テンソル互換の`shape`及び基本的な算術・縮約操作を持つ。
- `waveform.shape`が`[batch, channels, samples]`の3次元である。
- batchが正確に1、channels及びsamplesが1以上である。
- `sample_rate`が1以上のBooleanでない整数である。
- PCMにNaN又は無限大を含まない。
- UI入力が指定範囲内であり、Booleanを数値として受理しない。

複数channelを受理するが、複数batchは複数の独立時間軸を単一Lyricsへ対応付けられないため拒否する。各解析窓についてchannelごとにRMSを計算し、その最大値を窓のRMSとする。いずれかのchannelに人声があれば有声として保護するため、平均によって小さい人声を希釈してはならない。

## 7. 有声・無音検出

### 7.1 解析窓

```text
window_samples = max(1, round(analysis_window_ms * sample_rate / 1000))
```

時間軸を重複しない解析窓へ分ける。末尾窓は実在するサンプルだけでRMSを計算し、ゼロ埋めによって値を低下させない。

batch `b`、channel `c`、解析窓`w`のRMSを次で計算する。

```text
rms[b,c,w] = sqrt(mean(pcm[b,c,w]^2))
window_rms[w] = max(rms[b,c,w] for every b,c)
window_dbfs[w] = 20 * log10(max(window_rms[w], 1e-12))
```

`window_dbfs >= silence_threshold_dbfs`なら有声候補、それ以外は無音候補とする。PCM値の符号、チャンネル配置及びsample rateは変更しない。

### 7.2 デバウンス

解析窓の候補列へ次をこの順で適用する。

1. 両側が有声区間に接する、`min_silence_ms`未満の無音runを有声へ変更する。
2. `min_voiced_ms`未満の孤立した有声runを無音へ変更する。
3. 各有声runを前後へ`voice_padding_ms`だけサンプル単位で拡張し、入力範囲へクリップする。
4. 拡張後に重なる又は接する有声runを結合する。
5. 有声runの補集合を無音runとする。

時間比較は、各ms値を`round(ms * sample_rate / 1000)`でサンプル数へ変換して行う。同値境界で実装依存の浮動小数点比較を行ってはならない。

### 7.3 検出区間

検出区間は入力全体を隙間なく、重複なく、昇順に被覆する。各区間は次を持つ。

```text
state: "silent" | "voiced"
start_sample: 0以上の整数
end_sample: start_sampleより大きい整数
start_seconds: start_sample / sample_rate
end_seconds: end_sample / sample_rate
duration_seconds: (end_sample - start_sample) / sample_rate
```

隣接する同一stateの区間を残してはならない。

## 8. Whisper書き起こしとLyrics整列

### 8.1 Whisperモデル探索と任意依存

標準モデル探索先は次とする。

```text
ComfyUI/models/whisper/
```

- `.pt`を大文字小文字を区別せず再帰探索する。
- 実パスの重複を除去し、表示IDを辞書順で安定させる。
- COMBOの値は、実行直前に再探索して既存ローカルファイルへ解決する。
- モデルがない場合は`(no Whisper models found)`を表示し、実行時に探索先を含むエラーとする。
- `openai-whisper`の`whisper.load_model()`へ解決済みローカルファイルパスを渡す。
- `tiny`、`turbo`等の公式モデル名を渡して自動ダウンロードを開始してはならない。
- `whisper`をimportできない場合、名称が類似する別パッケージを自動導入せず、`openai-whisper`の手動導入が必要であることを実行時エラーで示す。
- `keep_whisper_loaded=True`では、実パスと解決済みdeviceの組をキーに1モデルだけ保持する。別モデル又はdeviceへ切り替える前に以前の参照を解放する。
- 同一モデルインスタンスに対する`word_timestamps=True`の推論はロックで直列化し、同時実行で一時的なアラインメント状態を共有しない。

本プロジェクトは`openai-whisper`又はモデルを自動インストール、更新若しくはダウンロードしない。READMEへ手動導入方法を追加する場合も、実行前にユーザー自身がパッケージとローカルモデルを用意する運用とする。

### 8.2 Suno Lyrics解析

`lyrics_text`は改行をLFへ正規化し、物理行順に処理する。

- 前後空白を除去した空行は無視する。
- 前後空白を除去した行全体が正規表現`^\[[^\]\r\n]+\]$`に一致する場合、`[Intro]`、`[Verse 1]`、`[Chorus]`等のSunoセクション見出しとして無視する。
- それ以外の非空行を1歌詞行とする。
- 出力本文には前後空白を除去したLyricsの原文を使用し、Whisper書き起こしへ置換しない。
- 同じ歌詞行が繰り返されても統合又は重複除去しない。
- 各歌詞行に元の1始まり物理行番号と、見出しを除外した1始まり歌詞番号を保持する。

照合専用文字列は、歌詞行とWhisper候補の両方へ次の順序で同じ処理を施す。

1. Unicode NFKC正規化。
2. Unicode case folding。
3. カタカナを対応するひらがなへ変換する。ただし長音記号及び変換範囲外の文字は維持する。
4. UnicodeカテゴリがSeparator又はPunctuationの文字を除去する。

正規化結果が空になる歌詞行はエラーとする。正規化は照合だけに使い、コメント及びSRT本文を変更してはならない。

### 8.3 Whisper入力と推論

Whisper用音声は元PCMを変更せず、次の一時データとして作る。

1. batch次元0を取り出す。
2. 複数channelをfloat32で算術平均してmono化する。
3. sample rateが16000 Hzでなければ、PyTorchの1次元linear interpolationを使用して16000 Hzへ変換する。
4. 変換後サンプル数を`round(original_samples * 16000 / sample_rate)`とし、時刻は常に元PCMのsample rateと総尺へ換算する。

ファイルへ一時保存せず、mono float32波形を直接Whisperへ渡す。推論引数は次を固定する。

```text
task = "transcribe"
temperature = 0.0
beam_size = 5
word_timestamps = True
condition_on_previous_text = False
initial_prompt = section headingsを除く先頭Lyricsのうち最大12行かつ160文字
verbose = None
language = "ja" when language=ja, "en" when language=en, otherwise None
fp16 = True on CUDA, False on CPU
```

`device=auto`はCUDAが利用可能なら`cuda`、それ以外は`cpu`へ解決する。明示`cuda`でCUDAが利用できない場合はCPUへ黙って変更せずエラーにする。

Whisperの英語言語コードは`en`である。`us`又は`en-US`はWhisperが受理する言語コードではないためUIへ追加せず、米国英語も`en`を指定する。

入力全体を1回の`transcribe()`へ渡し、独自に各有声runを別推論へ分割しない。これによりWhisper内部の連続時間軸と絶対時刻を維持する。VAD結果は推論の切り出しには使わず、後段で無音部の幻覚候補を除外するために使う。

`initial_prompt`は行の途中を避け、先頭から12歌詞行又は改行を含む160文字のどちらか先に達する範囲へ制限する。最初の1行だけで160文字を超える場合に限り、その1行を160文字で切る。これは冒頭の固有語と歌詞順をWhisperの最初の復号窓へ示すヒントであり、認識結果、SRT本文又は時刻の正本ではない。後続窓へ繰り返し注入する`carry_initial_prompt`相当の挙動は使用しない。

Whisper結果の各`segments[].words[]`から、空でない`word`、有限な`start`及び`end`を抽出する。`0 <= start < end <= audio_duration_seconds`を満たさないwordはWARNING付きで除外する。word配列が存在しない場合は、segment単位へ黙ってフォールバックせずエラーにする。

### 8.4 上から順の歌詞解決

Whisper wordを開始時刻順へ安定整列し、同時刻では元のsegment・word順を維持する。各wordの中点が7章の確定有声区間に含まれるものだけを照合候補とし、無音部にあるWhisper幻覚をLyricsへ対応付けない。

Lyrics行を先頭から1回ずつ処理し、確定したWhisper word範囲より前へ戻らない単調整列とする。

1. 最初の探索カーソルは候補word 0とする。
2. 現在のカーソル以降で、カーソル時刻から`lyrics_search_seconds`以内に開始する連続word範囲を候補とする。
3. 候補文字列は範囲内wordを空白なしで連結し、8.2の照合正規化を行う。
4. 正規化Lyricsを`a`、正規化候補を`b`として、類似度を`1 - levenshtein(a,b) / max(len(a), len(b))`で求める。
5. `b`の文字数が`max(1, floor(len(a) * 0.4))`未満、又は`ceil(len(a) * 2.5) + 8`を超える候補は比較しない。
6. 類似度が最大の候補を選ぶ。同値では開始が早い候補、それも同じならword数が少ない候補を選ぶ。
7. 最大類似度が`lyrics_match_threshold`以上なら解決済みとし、カーソルを候補終端の次へ進める。
8. 閾値未満なら未解決とし、カーソルを動かさず次のLyrics行を処理する。

この規則により、Whisperが1歌詞行を複数wordへ分割した場合及び複数歌詞行を一続きに認識した場合を吸収する。繰り返されるChorusはカーソルより後の出現だけに一致し、過去の同一文へ戻らない。

通常整列の後、未解決Lyricsの連続runについて次の限定的な救済を1回行う。

1. runの直前と直後に、`lyrics_match_threshold`以上で解決済みのLyricsが存在しなければ救済しない。
2. 探索範囲を、直前Lyricsが使用した最後のWhisper wordの次から、直後Lyricsが使用した最初のWhisper wordの直前までに限定する。
3. run内を上から順に再照合し、`lyrics_neighbor_threshold`以上の候補だけを解決済みにする。
4. 救済で確定したword範囲は同じrun内の後続行から再利用せず、時刻は前後アンカーの内側に収める。

`lyrics_neighbor_threshold`は`lyrics_match_threshold`以下でなければエラーとする。この救済は前後の確定アンカーで検索空間を限定できる場合だけ誤認識の表記差を許容するものであり、先頭又は末尾の未解決Lyrics、Whisper wordのない空間、時刻内挿及びLyrics本文からの架空word生成には使用しない。

LyricsとWhisperのどちらにも存在しない文字列を補完してはならない。Lyrics行の順序変更、Whisper時刻だけに基づく未解決行の均等配置及び前後行からの時刻内挿は禁止する。

### 8.5 確定歌詞時刻

解決済み歌詞行の生時刻は、対応word範囲の最初の`start`から最後の`end`までとする。SRT用ミリ秒は次で決める。

```text
raw_start_ms = floor(first_word.start * 1000)
raw_end_ms = ceil(last_word.end * 1000)
start_ms = max(raw_start_ms, previous_resolved_end_ms)
end_ms = max(start_ms + 1, raw_end_ms)
```

`end_ms`は`ceil(audio_duration_seconds * 1000)`を超えないようクリップする。クリップ後に`end_ms <= start_ms`となる候補は未解決へ戻し、WARNINGを出す。解決済み行はLyrics順、時刻昇順かつ相互に重ならない。

Whisper本文は診断用に保持できるが、プロンプトコメント及びSRT本文には必ずLyrics原文を使用する。

歌唱に対するWhisperの単語時刻は近似値であり、音楽的な母音伸長、コーラス、重唱、リバーブ及びステム分離残留によって境界がずれる可能性がある。本ノードのSRTは行単位の初期同期データであり、カラオケ用途の音素又は文字単位タイミングを保証しない。全体へ適用される`lyrics_match_threshold`を先に下げず、前後アンカーに挟まれた取りこぼしだけを`lyrics_neighbor_threshold`で救済する。いずれの閾値も下げるほど誤対応が増えるため、SRTは動画生成又は編集前に確認する。

## 9. Scene分割

### 9.1 整数秒への安全側量子化

現行の日本語縮小版MarkdownはScene durationに1～60の整数秒だけを許可する。このため、サンプル精度の検出区間をそのまま`# シーン N秒`へ出力してはならない。

```text
audio_duration_seconds = total_samples / sample_rate
timeline_duration_seconds = ceil(audio_duration_seconds)
trailing_padding_seconds = timeline_duration_seconds - audio_duration_seconds
```

各確定有声区間`[start_seconds, end_seconds)`を次の整数秒範囲へ外向きに量子化する。

```text
quantized_voiced_start = floor(start_seconds)
quantized_voiced_end = ceil(end_seconds)
```

結果を`[0, timeline_duration_seconds]`へクリップし、重なる又は接する有声範囲を結合する。補集合を無音範囲とする。この規則により、検出済みの有声PCMを`発声: なし`のSceneへ割り当ててはならない。最大で開始側1秒未満、終了側1秒未満の無音が有声Sceneへ含まれることは許容する。その実際の無音部分ではSource Vocal信号に従い口を閉じる。

入力末尾の端数を切り捨ててはならない。`trailing_padding_seconds > 0`の場合、出力Markdownは切り上げ後の全時間を含み、status及び`segments_json`で`CLAudioPad`による末尾無音追加が必要であることを示す。

### 9.2 上限による分割

量子化済みの各連続範囲を、各Sceneが`max_scene_seconds`以下になるよう分割する。

長さ`L`秒の範囲に対し次を用いる。

```text
scene_count = ceil(L / max_scene_seconds)
base_length = floor(L / scene_count)
remainder = L % scene_count
```

先頭から`remainder`件を`base_length + 1`秒、残りを`base_length`秒とする。これにより末尾だけが極端に短くなる分割を避ける。各長さは1以上`max_scene_seconds`以下でなければならない。

分割後も隙間、重複及び並べ替えを許可しない。1件目のSceneは非継続、2件目以降はすべて`継続`とする。最大Scene数は現行コンパイラに合わせて128とし、超過時はエラーとする。

### 9.3 Scene状態

- 元が量子化済み有声範囲のSceneは`voiced`。
- 元がその補集合のSceneは`silent`。
- 有声Sceneの一部に実際の無音PCMが含まれても、Source Vocal以外の発声を許可しない。
- silent Sceneは確定有声区間とサンプル単位で重なってはならない。

## 10. 日本語プロンプトテンプレート

### 10.1 共通規則

- 改行はLFとし、末尾にLFを1個付ける。
- Cスタイル行コメント`//`を編集案内及び検出時刻の表示に使用できる。コメントは現行コンパイラによって推論前に除去される。
- HTMLコメント及び旧Scene構文を出力しない。
- 疑似参照と誤認され得る`<ここに...>`等の山括弧プレースホルダを出力しない。
- 生成結果は未編集でも`CLJapaneseToJSONGGUF`が受理できる完全なMarkdownでなければならない。
- Lyricsにない歌詞、台詞、`<Audio N>`、ユーザー話者ID`(Sx)`及びRetentionを生成又は推測しない。
- 解決済みLyricsは意味を持つバレット又はダイレクトスピーチへ変換せず、Cスタイル行コメントだけへ出力する。

### 10.2 文書先頭

文書先頭は次の固定テンプレートとする。

```markdown
# サブジェクト
// 次の行を、必要な外観参照と人物説明を含むSubject 1の定義へ編集できます。
* 人物。

# 共通プロンプト
// 次の行を、全Sceneに共通する画風、背景、照明及び制約へ編集できます。
* 全シーンで一貫した画風、照明、背景及び人物の外観を維持する。
```

`<Picture 1>`及び`<Audio 1>`を自動追加しない。`ソースボーカル`は番号付きAudio参照ではなく、Contex-LoopのSource Vocal入力を表す。

### 10.3 有声Scene

```markdown
# シーン 10秒 継続
// 検出状態: voiced。ソース範囲 00:10.000-00:20.000。
// 赤い林檎を　ひとつ頬張り
// おまえの勘定を　笑ってやろう
## ショット
// 次の行を、この区間の具体的な人物動作とカメラワークへ編集できます。
* <Subject 1>はソースボーカルの抑揚に合わせて自然に演技する。
* リップシンク: <Subject 1> <- ソースボーカル
## 音響
* 発声: ソースボーカルのみ
* ソース音声: 完全維持
```

1件目の場合だけ`継続`を省略する。Scene秒数及びコメント内の範囲は実際のScene計画へ置換する。

解決済み歌詞行は、その`start_ms`を含むSceneの検出状態コメント直後へ、Lyrics順で`// `と原文を連結して1回だけ出力する。ここで仕様記述上の`// <歌詞>`にある`<歌詞>`はメタ変数であり、実出力へ山括弧を追加しない。歌詞がScene境界をまたいでも、開始Sceneだけへコメントする。未解決Lyrics及びWhisperだけが認識した文はコメントへ出力しない。

`リップシンク`行はScene内に1個だけ置く。Source Vocalの実際の有声区間、無音区間、音素、持続音及びフレーズ境界を正本とし、追加の歌声又は台詞を生成する許可として扱わない。

### 10.4 無音Scene

```markdown
# シーン 10秒 継続
// 検出状態: silent。ソース範囲 00:20.000-00:30.000。
## ショット
// 次の行を、この区間の具体的な人物動作とカメラワークへ編集できます。
* <Subject 1>は口を閉じ、発話を示す口の動きを行わず、自然に演技する。
## 音響
* 発声: なし
* ソース音声: 完全維持
```

無音Sceneへ`リップシンク`、`<Audio N>`、台詞、生成BGM、環境音又は効果音を追加しない。ロック済みSource Timelineに元から含まれる音は`ソース音声: 完全維持`によって保持する。

### 10.5 時刻表記

編集案内コメントの時刻は絶対Source Timeline時刻であり、`MM:SS.mmm`形式とする。分は2桁以上、秒は`00`～`59`、ミリ秒は3桁とする。コメントはコンパイラ出力へ影響しない。

Scene本文へ歌唱開始秒又は終了秒を通常文として重複記載しない。時間同期の正本はボーカルステムである。

## 11. SRT出力

`srt_text`は解決済みLyrics行だけを、Lyrics順の1始まり連番でSRTへ変換する。字幕本文にはWhisper書き起こしではなく、前後空白だけを除いたLyrics原文を使用する。

```srt
1
00:00:13,020 --> 00:00:16,180
赤い林檎を　ひとつ頬張り

2
00:00:16,340 --> 00:00:19,900
おまえの勘定を　笑ってやろう

```

- 時刻形式は`HH:MM:SS,mmm`とし、時間は最低2桁、分・秒は2桁、ミリ秒は3桁とする。
- 区切りはASCII文字列` --> `とする。
- 改行はLFとし、各エントリの後へ空行を1個置く。非空SRTはLF2個で終わる。
- 字幕番号は解決済み行だけで欠番のない1始まり連番とする。
- 1歌詞行を1字幕エントリとし、歌詞行を結合又は再改行しない。
- Sunoセクション見出し、未解決Lyrics行及びWhisperだけが認識した文を出力しない。
- 解決済み行が0件の場合は空文字列を返し、WARNINGを出す。
- SRT時刻はボーカルステム先頭を`00:00:00,000`とする絶対時刻であり、各Scene先頭からの相対時刻ではない。

## 12. segments_json

`segments_json`はUTF-8相当のJSON文字列で、可読性のため2スペースインデント、キー順固定、末尾LF1個とする。浮動小数点秒は小数6桁へ丸めるが、サンプル位置を正本とする。

```json
{
  "schema_version": 3,
  "sample_rate": 48000,
  "total_samples": 12470400,
  "audio_duration_seconds": 259.8,
  "timeline_duration_seconds": 260,
  "trailing_padding_seconds": 0.2,
  "whisper": {
    "model": "large-v3.pt",
    "language_requested": "ja",
    "language_detected": "ja",
    "device": "cuda"
  },
  "settings": {
    "max_scene_seconds": 10,
    "silence_threshold_dbfs": -45.0,
    "analysis_window_ms": 20,
    "min_voiced_ms": 120,
    "min_silence_ms": 300,
    "voice_padding_ms": 80,
    "lyrics_match_threshold": 0.55,
    "lyrics_neighbor_threshold": 0.45,
    "whisper_initial_prompt_lines": 8,
    "whisper_initial_prompt_characters": 116,
    "lyrics_search_seconds": 60.0
  },
  "detected_intervals": [
    {
      "state": "silent",
      "start_sample": 0,
      "end_sample": 624000,
      "start_seconds": 0.0,
      "end_seconds": 13.0,
      "duration_seconds": 13.0
    }
  ],
  "lyrics": [
    {
      "lyrics_index": 1,
      "source_line": 5,
      "text": "赤い林檎を　ひとつ頬張り",
      "status": "resolved",
      "match_score": 0.82,
      "match_method": "primary",
      "whisper_text": "赤いりんごを一つ頬張り",
      "candidate_whisper_text": "",
      "candidate_start_ms": null,
      "candidate_end_ms": null,
      "start_ms": 13020,
      "end_ms": 16180,
      "scene_index": 2
    },
    {
      "lyrics_index": 2,
      "source_line": 6,
      "text": "おまえの勘定を　笑ってやろう",
      "status": "unresolved",
      "match_score": 0.41,
      "match_method": null,
      "whisper_text": "",
      "candidate_whisper_text": "おまえの感情を笑ってやろ",
      "candidate_start_ms": 18120,
      "candidate_end_ms": 21940,
      "start_ms": null,
      "end_ms": null,
      "scene_index": null
    }
  ],
  "scenes": [
    {
      "index": 1,
      "state": "silent",
      "start_seconds": 0,
      "end_seconds": 7,
      "duration_seconds": 7,
      "lyrics_indices": []
    }
  ]
}
```

`detected_intervals`はサンプル精度の検出結果、`lyrics`は入力行ごとの整列結果、`scenes`は整数秒へ量子化・分割したMarkdown生成結果である。`whisper.model`には絶対パスではなく選択された表示IDを保存する。`match_method`は`primary`、`neighbor`又はnullとする。未解決行の`whisper_text`、時刻とSceneは従来どおり空文字列又はnullとし、最良候補が存在した場合だけ`candidate_whisper_text`と候補時刻を診断用に保存する。候補本文をコメント又はSRTへ出力してはならない。例示値は説明用であり、各配列が入力全体を表すとは限らない。

## 13. statusとログ

statusは1行の英数字中心の文字列とし、少なくとも次を含む。

- 入力サンプル数、sample rate及び入力秒数
- 確定有声区間数
- Whisperモデル、推論device及び検出言語
- Lyrics総行数、解決数及び未解決数
- 生成Scene数と有声・無音Scene数
- 計画秒数
- 必要な末尾パディング秒数

例:

```text
analyzed 12470400 samples at 48000 Hz (259.800000s); whisper=large-v3.pt on cuda language=ja; lyrics=42 resolved (2 neighbor-recovered), 3 unresolved; detected 14 voiced interval(s); generated 28 scene(s): 22 voiced, 6 silent; timeline=260s; end padding required=0.200000s
```

logger名及びユーザー可視ログ接頭辞は`cl_vocal2promptseg`とする。

```text
[INFO] [cl_vocal2promptseg] analyzed ...
```

通常ログへPCM、プロンプト全文又は区間JSON全文を出力しない。ファイルへの自動保存及びComfyUI `input`/`output`への書き込みを行わない。

## 14. エラーと警告

### 14.1 エラー

次は実行を停止する。

- 不正又は空のAUDIO
- 不正shape、複数batch、sample rate、NaN又は無限大PCM
- 空Lyrics又は有効な歌詞行を含まないLyrics
- `openai-whisper`をimportできない、又は互換APIを持たない
- ローカルWhisperモデルがない、消失した、読み込めない、又は選択deviceで実行できない
- Whisper推論の失敗、中断又はword timestampの欠落
- UI値の型又は範囲違反
- `lyrics_neighbor_threshold`が`lyrics_match_threshold`を超える
- 内部区間の隙間、重複、逆転又はゼロ長
- 量子化後Sceneの0秒、60秒超過又は上限違反
- 生成Scene数が128を超える
- 生成Markdownを既存字句解析規則で自己検証できない
- 非空SRTの番号、時刻形式、時刻順又は空行区切りが不正
- `segments_json`を`json.loads()`できない

### 14.2 警告

次はWARNINGを出して継続する。

- `trailing_padding_seconds > 0`: `CLAudioPad`の`pad_position=end`でフルミックスとボーカルステムを計画尺まで補完する必要がある。
- 有声区間が0件: 全Sceneを無音として生成する。
- 無音区間が0件: 全Sceneを有声として生成する。
- 最も大きい解析窓RMSが閾値未満又は閾値との差が1 dB以下で、設定調整が必要と推定できる。
- 不正な時刻を持つWhisper wordを除外した。
- 通常整列と前後アンカー限定救済の後も、1件以上のLyrics行を解決できなかった。
- Whisperが文字を認識したが、その中点が確定有声区間外にあり照合候補から除外した。
- 解決済みLyrics行が0件で、空のSRTを返した。

有声検出結果の品質が低いことを理由に、入力PCM又は閾値を自動変更してはならない。

## 15. 性能、進捗及び決定性

- 計算量はサンプル数に対してO(n)、追加メモリは解析窓数に対してO(w)を上限とする。
- 長時間音声で全PCMの二重コピーを作らず、必要なら時間軸をチャンク処理する。
- 入力テンソルをin-place変更しない。
- Whisper以外の生成モデル、乱数及びネットワークを使用しない。モデル推論時にもネットワークアクセスを行わない。
- Whisperを`temperature=0.0`で実行し、同じPCM、Lyrics、モデル、device及びUI値からは同じ整列規則と出力形式を使用する。異なるハードウェア又はWhisper版による認識差まで同一とは保証しない。
- テンソルの元dtype及びデバイスにかかわらず、RMS計算は少なくともfloat32相当の精度で行う。
- UIを長時間無応答にしないよう、VAD、Whisper、Lyrics整列及び出力生成の各段階でComfyUI進捗を更新し、中断要求を定期的に確認できる構造にする。
- 通常ログへWhisper開始を記録し、推論中は10秒ごとに経過秒数をINFOで記録する。公開APIから安全に取得できない内部30秒窓の進捗率を推測して表示してはならない。完了時はLyrics解決数を含むstatusをINFOで記録する。歌詞本文と生書き起こしは通常ログへ出さない。

## 16. ワークフロー接続

推奨接続は次である。

```text
Load Audio (full mix) ──> CLAudioPad ──> Contex-Loop source_timeline
Load Audio (vocal stem) ─> CLVocalToPromptSegments.vocal_audio
Suno Lyrics STRING ──────> CLVocalToPromptSegments.lyrics_text
Load Audio (vocal stem) ─> CLAudioPad ──> H3 Audio Tracks / lip_sync_voice
CLVocalToPromptSegments.prompt_text ─> CL Japanese to JSON (GGUF).plain_text
CLVocalToPromptSegments.srt_text ─────> Preview/Save Text or subtitle workflow
```

- full mixとvocal stemは同一開始時刻、同一速度及び同一楽曲長で書き出す。
- vocal stemの先頭無音を削除しない。
- Lyricsの`[Intro]`等は維持したまま入力できるが、見出し自体は歌詞及びSRTへ出力しない。
- full mixを時間軸の基準にし、短い方だけを`CLAudioPad`の`pad_position=end`で補完する。
- `trailing_padding_seconds`が0より大きい場合は、少なくともその端数を両トラックの末尾へ確保する。
- `CLAudioPad`を相互に`match_audio`接続しない。
- Plan依存のパディング済みvocalをGeneration Profileへ戻して循環依存を作らない。
- 最終音声はsource timelineを使用し、Source Vocalを重ねて二重ボーカルにしない。

## 17. 単体テスト要件

実GGUF及びContex-Loopを使わず、少なくとも次を検証する。

- mono/stereoを受理し、複数batchを拒否する。
- 不正AUDIO、空PCM、NaN、無限大、空Lyrics及び不正UI値を拒否する。
- 既知振幅のPCMから閾値どおりの候補列を作る。
- 末尾の短い解析窓を実サンプル数で計算する。
- 短い無音gapを結合し、短い有声islandを除去する。
- voice paddingをサンプル境界でクリップし、重複runを結合する。
- 検出区間が入力全体を隙間なく一度だけ覆う。
- 有声範囲を`floor/ceil`で外向き量子化し、検出済み人声をsilent Sceneへ入れない。
- 端数尺を`ceil`し、正確な`trailing_padding_seconds`を報告する。
- 長い範囲をScene上限内で均等に分割する。
- 全無音、全有声、先頭だけ有声、末尾だけ有声及び交互区間を処理する。
- Suno見出しと空行を除外し、Lyrics原文と元行番号を維持する。
- NFKC、case folding、カタカナ・ひらがな及び空白・句読点の照合正規化を検証する。
- fake Whisper結果からword timestampを抽出し、不正wordを除外する。
- Lyricsを上から順に単調整列し、繰り返し歌詞を過去の出現へ戻して割り当てない。
- 閾値以上の行だけを解決し、未解決行の時刻を補間しない。
- 無音部のWhisper幻覚を照合候補へ使わない。
- SRT本文にLyrics原文を使い、Whisper表記へ置換しない。
- SRT番号、`HH:MM:SS,mmm`、区切り、非重複時刻及び空行を検証する。
- 解決済み歌詞を開始Sceneへ`// `コメントとして1回だけ出力する。
- 未解決歌詞及びWhisperだけの文をコメントとSRTへ出力しない。
- 128 Scene超過を拒否する。
- 最初のSceneだけ`継続`を省略する。
- voiced SceneだけへSource Vocalリップシンクを出力する。
- silent Sceneへ`発声: なし`を出力する。
- 全Sceneへ`ソース音声: 完全維持`を出力する。
- 生成Markdownを現行`LLMJ2E`の字句解析が受理する。
- `segments_json`のschema、キー順、サンプル値及びScene値を検証する。
- Whisper未導入及びモデル未配置でもパッケージimportとノード登録が成功し、実行時に明確なエラーを返す。
- ローカルモデルパスだけをロードし、自動ダウンロードを開始しない。
- 同一fake Whisper入力で全出力が決定的に一致する。

## 18. 受入条件

- PCMボーカルステムから、LLMを使わず有声・無音区間を検出できる。
- Whisperでボーカルステムを書き起こし、単語時刻を取得できる。
- Suno Lyricsを本文の正本として上から順にWhisper wordへ対応付けられる。
- 解決済みLyricsだけを元表記の`// 歌詞`コメントとして対応Sceneへ出力できる。
- 解決済みLyricsだけを元表記と絶対時刻を持つSRTとしてSTRINGソケットへ出力できる。
- 未解決Lyricsへ推測時刻を生成しない。
- サンプル精度の検出結果と整数秒のScene計画を区別して出力できる。
- 既存のScene、Shot、Soundscape、Source Vocal及びSource Timeline文法へ完全に適合する。
- 生成テンプレートを未編集のまま`CLJapaneseToJSONGGUF`へ入力できる。
- 1 Sceneは1 Shotだけを持つ。
- Scene durationは1～`max_scene_seconds`の整数である。
- 検出済みの有声PCMを`発声: なし`のSceneへ割り当てない。
- 無音Sceneではリップシンクと人物由来音声を無効にする。
- 有声SceneではSource Vocalだけを口形駆動に使用し、別の声を生成しない。
- ロック済みSource Timelineを全Sceneで完全維持する。
- 端数秒を切り捨てず、必要な末尾パディング量を明示する。
- Whisperパッケージ及びモデルを自動インストール、更新又はダウンロードしない。
- 入力PCM、ComfyUI本体及び他のcustom nodeを変更しない。

## 19. 参照資料

- [OpenAI Whisper README](https://github.com/openai/whisper/blob/main/README.md): Pythonからのモデルロードと音声書き起こし、内部の30秒スライディングウィンドウ処理
- [OpenAI Whisper transcribe.py](https://github.com/openai/whisper/blob/main/whisper/transcribe.py): PCM波形入力、`word_timestamps`、`condition_on_previous_text`及び推論結果の構造
- [OpenAI Whisper load_model実装](https://github.com/openai/whisper/blob/main/whisper/__init__.py): ローカルチェックポイントパスの受理と、公式モデル名指定時のダウンロード動作
