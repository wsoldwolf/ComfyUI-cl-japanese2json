# 導入ガイド

## 必要環境

- Python 3.11以降を使用するComfyUI
- Qwen3 8B等のテキスト用GGUFモデル
- 使用環境に合う`llama-cpp-python`
- Visionノードを使う場合は`MTMDChatHandler`を含む`llama-cpp-python`と、対応する本体GGUF・`mmproj` GGUF
- Vocalノードを使う場合は、ComfyUI環境へ手動導入した`openai-whisper`とローカル`.pt`チェックポイント
- MiniMax H3の生成ノード及び統合ワークフローが参照するカスタムノード

本プロジェクトは`llama-cpp-python`、OpenAI Whisper、GGUF及びWhisperモデルを自動インストール、更新又はダウンロードしません。CUDA対応wheelが依存解決によってCPU版へ置き換わる事故を避けるため、Python依存関係も空にしています。

## 動作確認済みバージョン

統合MVワークフローは、次のコミットを基準に動作確認しています。

| プロジェクト | 動作確認済みコミット | バージョン |
| --- | --- | --- |
| ComfyUI | `8fed37813848259fbdd2548ae3cd9f14df7fd68b` | `v0.34.6` |
| Context Loop | `136db5dbbf25405063a96e898ae880e8785b7f29` | コミット固定 |

Context Loopは、以前は`Contex Loop`というプロジェクト名で公開されていました。本プロジェクト内の既存文書、ワークフロー、ノード名又はログに旧名が残っている場合がありますが、現在のプロジェクト名は`Context Loop`です。

特にContext Loopは更新頻度が高く、最新コミットではノードの入出力、Plan形式又は継続生成の挙動が変わり、本プロジェクトのワークフローが直ちには追従できない可能性があります。再現性を優先する場合は上記コミットを使用してください。より新しい版を使用する場合は、先に同梱の短縮ワークフローで互換性を確認することを推奨します。

既に各リポジトリを取得済みで、作業中の変更がない場合は、対象リポジトリ内で次のように確認・切り替えできます。

```bash
git rev-parse HEAD
git switch --detach 8fed37813848259fbdd2548ae3cd9f14df7fd68b
```

Context Loop側では、最後のコミットIDを`136db5dbbf25405063a96e898ae880e8785b7f29`へ置き換えます。`git switch --detach`はdetached HEADにするため、独自の修正を継続する場合はそのコミットから作業用ブランチを作成してください。

## ComfyUIへ配置

1. このリポジトリ全体を`ComfyUI/custom_nodes/ComfyUI-cl-japanese2json/`へ配置します。
2. テキストGGUFを`ComfyUI/models/LLM/GGUF/`以下へ配置します。
3. Visionモデルはモデル系列ごとのサブディレクトリへ本体GGUFと対応する`mmproj` GGUFを一緒に配置します。
4. Whisperを使う場合は公式`.pt`を`ComfyUI/models/whisper/`以下へ配置します。
5. ComfyUIを再起動します。

モデルディレクトリは再帰探索されます。例:

```text
ComfyUI/models/LLM/GGUF/
  Qwen3-8B-Abliterated/
    qwen3-8b-abliterated-Q4_K_M.gguf
  Qwen3-VL-4B-Instruct/
    Qwen3-VL-4B-Instruct-Q4_K_M.gguf
    mmproj-F16.gguf
```

追加のComfyUI `LLM`モデルパスが設定されている場合は、そのルートと`GGUF`サブディレクトリも探索します。

## Windowsで`llama-cpp-python`をビルドする

WindowsではComfyUIの仮想環境へCUDA対応wheelを手動ビルドします。以下はCUDA 13.0、Visual Studio 2022、RTX 4070 Ti（Compute Capability 8.9）及びRTX 5090（12.0）向けの例です。

`GGML_NATIVE=OFF`とAVX-512/AMX無効化により、AVX-512対応CPUで作成したwheelを非対応CPUでも利用できるCPUバックエンドにします。必ず「x64 Native Tools Command Prompt for VS 2022」で実行してください。

```bat
cd /d C:\Software\ComfyUI
venv\Scripts\activate

set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.0"
set "PATH=%CUDA_PATH%\bin;%CUDA_PATH%\lib\x64;%PATH%"

set "CMAKE_GENERATOR=Visual Studio 17 2022"
set "CMAKE_GENERATOR_PLATFORM=x64"
set "CMAKE_ARGS=-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=89;120 -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF -DGGML_AVX512_BF16=OFF -DGGML_AMX_TILE=OFF -DGGML_AMX_INT8=OFF -DGGML_AMX_BF16=OFF"
set "FORCE_CMAKE=1"

python -m pip install --upgrade pip setuptools wheel
if not exist dist mkdir dist
python -m pip -vvv wheel "llama-cpp-python==0.3.34" --no-deps --no-cache-dir --no-binary=llama-cpp-python -w dist
python -m pip install --force-reinstall --no-deps dist\llama_cpp_python-0.3.34-*.whl
```

ビルド後にバックエンドを確認します。

```bat
python -c "import llama_cpp; print(llama_cpp.__version__); print(llama_cpp.llama_print_system_info().decode())"
```

## ノードの場所

- プロンプト関連: `MiniMax H3/Prompt Tools`
- 音声パディング: `MiniMax H3/Audio Tools`

正常終了したCLノードは、ComfyUIコンソールへ`[cl_*] success: ...`形式のシアン色ログを出力します。VocalノードはLyricsとSRT本文が完全一致した場合に`self test passed`を同じ成功色で表示します。

問題がある場合は[トラブルシューティング](troubleshooting.md)を参照してください。
