# cl_japanese2json ComfyUIカスタムノード実装仕様書

## 1. 文書の目的

本仕様書は、`cl_japanese2json` を独立したComfyUIカスタムノードとして実装するための要件を定義する。

ノードは、縮小版Markdownで記述された日本語のプレーンテキストを入力として受け取り、MiniMax H3 Contex-Loop Planへ接続可能な厳格なJSONをプレーンテキストとして出力する。

本仕様書は、次のコア変換仕様書と併用する。

```text
cl_japanese2json_spec.md
```

変換規則、タグ保護、日本語読み上げ文字列、MDPARSE、JSONGEN及び出力JSONの意味は、`cl_japanese2json_spec.md` を正規仕様とする。本書は、ComfyUIノード、GGUFモデル管理、`llama-cpp-python`、キャッシュ、VRAM管理及びパッケージ構造に関する追加要件を定義する。

両仕様が矛盾する場合は、次の優先順位を適用する。

1. コア変換内容：`cl_japanese2json_spec.md`
2. ComfyUI統合とモデル実行：本仕様書

## 2. 確定した設計方針

次の方針は確定事項とする。

- `QwenVL-Mod` へ実行時依存しない独立したカスタムノードとして実装する。
- `QwenVL-Mod Prompt Enhancer (GGUF)` のモデル探索、モデル保持、VRAM解放及び `llama-cpp-python` 呼び出し方式を参考にする。
- GGUFモデルはComfyUIの `models/LLM/GGUF` 以下を再帰走査し、ノードの選択欄へ表示する。
- 入力は複数行のComfyUI `STRING` とする。
- 日本語シーン見出しは `# シーン [N秒] [継続]` とし、旧形式の `N秒生成する` と `継続する` は受理しない。
- 出力はJSON文字列を格納したComfyUI `STRING` 1個とする。
- システムプロンプトはカスタムノードディレクトリ内のUTF-8テキストファイルから読み込む。
- `keep_last_prompt=True` で有効な履歴がない場合は通常処理を行い、成功したJSONを履歴として保存する。
- `op_offload` は `float` ではなく `bool` とする。
- LLMは翻訳だけを担当し、最終JSONはPythonコードが生成する。
- LLM出力またはJSON検証に失敗した場合、不正な文字列を出力せずComfyUIの実行エラーとして停止する。

## 3. 実装対象

### 3.1 パッケージ名

推奨カスタムノードディレクトリ名：

```text
ComfyUI-cl-japanese2json
```

ComfyUIへの配置先：

```text
ComfyUI/custom_nodes/ComfyUI-cl-japanese2json/
```

### 3.2 ノードクラス

| 項目 | 値 |
| --- | --- |
| Pythonクラス名 | `CLJapaneseToJSONGGUF` |
| Node ID | `CLJapaneseToJSONGGUF` |
| 表示名 | `CL Japanese to JSON (GGUF)` |
| カテゴリ | `MiniMax H3/Prompt Tools` |
| 実行関数 | `compile_json` |
| 入力型 | `STRING`及び設定ウィジェット |
| 出力型 | `STRING` |
| 出力名 | `json_text` |
| Output Node | `False` |

### 3.3 ComfyUIノードAPI

初期実装は、`QwenVL-Mod Prompt Enhancer (GGUF)` と同じComfyUI V1互換ノードAPIを使用する。

```python
class CLJapaneseToJSONGGUF:
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("json_text",)
    FUNCTION = "compile_json"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False
```

`INPUT_TYPES()` はクラスメソッドとして実装する。

V3 APIへの移行は初期実装の対象外とする。ただし、変換コアとGGUFバックエンドをノードクラスから分離し、将来V3ラッパーを追加できる構造にする。

## 4. 入出力

### 4.1 plain_text入力

入力名は `plain_text` とする。

```python
"plain_text": (
    "STRING",
    {
        "default": "",
        "multiline": True,
        "tooltip": "Japanese reduced Markdown compiled into MiniMax H3 Contex-Loop JSON."
    },
)
```

要件：

- 複数行入力を許可する。
- ノード上のテキストウィジェットから直接入力できる。
- ComfyUIの別ノードから `STRING` として接続できる。
- 入力文字列を暗黙にトリムして意味を変更してはならない。
- UTF-8文字列として処理する。
- 空文字列または空白だけの入力は実行エラーとする。
- 入力のLF及びCRLFはコア変換処理でLFへ正規化する。

### 4.2 json_text出力

出力は1個の `STRING` とし、Pythonの戻り値は1要素タプルとする。

```python
return (json_text,)
```

`json_text` は次を満たす。

- JSONオブジェクトを表すプレーンテキストである。
- Markdownコードフェンスを含まない。
- 説明、前置き、警告文及びログを含まない。
- UTF-8で表現可能である。
- `json.loads(json_text)` が成功する。
- 人間が読みやすい2空白インデントを使用する。
- `ensure_ascii=False` を使用し、日本語読み上げ文字列を可読状態で保持する。
- 末尾に1個のLFを含む。

## 5. ノード入力パラメータ

### 5.1 入力一覧

ノードは、次の入力をこの順序で表示する。

1. `plain_text`
2. `model_name`
3. `max_tokens`
4. `temperature`
5. `top_p`
6. `repetition_penalty`
7. `gpu_layers`
8. `n_batch`
9. `n_ctx`
10. `flash_attn`
11. `kv_cache_type`
12. `op_offload`
13. `keep_model_loaded`
14. `seed`
15. `keep_last_prompt`
16. `steps`
17. `save_debug_output`（任意入力）

### 5.2 パラメータ定義

| 名前 | ComfyUI型 | 既定値 | 範囲・選択肢 | 再ロード |
| --- | --- | ---: | --- | --- |
| `plain_text` | `STRING` | `""` | multiline | 不要 |
| `model_name` | 選択式 `STRING` | 走査結果の先頭 | 検出したGGUF | 必要 |
| `max_tokens` | `INT` | `4096` | 32～16384、step 32 | 不要 |
| `temperature` | `FLOAT` | `0.1` | 0.1～1.0、step 0.05 | 不要 |
| `top_p` | `FLOAT` | `0.9` | 0.0～1.0、step 0.01 | 不要 |
| `repetition_penalty` | `FLOAT` | `1.05` | 0.5～2.0、step 0.05 | 不要 |
| `gpu_layers` | `INT` | `-1` | -1～1000、step 1 | 必要 |
| `n_batch` | `INT` | `256` | 32～4096、step 32 | 必要 |
| `n_ctx` | `INT` | `0` | 0～131072、step 512 | 必要 |
| `flash_attn` | `BOOLEAN` | `True` | True / False | 必要 |
| `kv_cache_type` | 選択式 `STRING` | `q8_0` | `q8_0`, `f16` | 必要 |
| `op_offload` | `BOOLEAN` | `True` | True / False | 必要 |
| `keep_model_loaded` | `BOOLEAN` | `True` | True / False | 不要 |
| `seed` | `INT` | `1` | 1～4294967295 | 不要 |
| `keep_last_prompt` | `BOOLEAN` | `False` | True / False | 不要 |
| `steps` | `INT` | `8` | 1～10000、step 1 | 不要 |
| `save_debug_output` | `BOOLEAN` | `False` | True / False | 不要 |

「再ロード」は、既に保持している `Llama` インスタンスを破棄し、モデルを再ロードする必要がある設定変更を示す。

### 5.3 max_tokens

- 1回の `create_chat_completion()` が生成できる最大トークン数を表す。
- 最終JSONの最大長ではない。
- LLMJ2Eは最初に、ディレクティブ、区間先頭、参照タグ及び発話をプレースホルダ化した文書全体を、単一の生テキスト翻訳ストリームとして1回の推論へ収めることを試みる。
- 固定の区間件数上限を設けず、入力と設定上限 `max_tokens` が実効コンテキスト長へ収まる限り、文書全体を単一推論で処理する。
- コンテキスト長のため複数推論へ分割した場合、`max_tokens` は各推論に適用する。
- 入力トークン、system message、user message、チャットテンプレートの余白及び `max_tokens` が有効コンテキスト長へ収まらない場合は、箇条書き区間単位でバッチを縮小する。
- 1区間だけでも収まらない場合は明示的なコンテキスト長エラーとする。
- `finish_reason == "length"` の結果を採用してはならない。

### 5.4 temperature、top_p、repetition_penalty

`create_chat_completion()` へ次のように渡す。

```python
temperature=temperature
top_p=top_p
repeat_penalty=repetition_penalty
```

翻訳の再現性を優先するため、`temperature` の既定値は `0.1` とする。

### 5.5 gpu_layers

`Llama` コンストラクタの `n_gpu_layers` へ渡す。

| 値 | 意味 |
| ---: | --- |
| `-1` | 利用可能な全レイヤーをGPUへオフロードする |
| `0` | モデルレイヤーをGPUへオフロードしない |
| `1`以上 | 指定数のレイヤーをGPUへオフロードする |

値を変更した場合、保持中のモデルを再ロードする。

### 5.6 n_batch

- `Llama` コンストラクタの `n_batch` へ渡す。
- プロンプト処理時の最大論理バッチサイズを表す。
- 値を変更した場合、保持中のモデルを再ロードする。
- `llama-cpp-python` 側で有効 `n_ctx` 以下へ制限される場合、その挙動を許容する。
- `n_ubatch` は初期実装ではUIへ公開せず、`llama-cpp-python` の既定値を使用する。

### 5.7 n_ctx

- `Llama` コンストラクタの `n_ctx` へ渡す。
- `0` はGGUFモデルに記録された学習時コンテキスト長を使用する。
- 正数はユーザー指定のコンテキスト長として使用する。
- 値を変更した場合、保持中のモデルを再ロードする。
- モデルロード後の実効コンテキスト長は `self.llm.n_ctx()` で取得し、LLMJ2Eのバッチ分割判定に使用する。

### 5.8 flash_attn

- `Llama` コンストラクタの `flash_attn` へBooleanとして渡す。
- 値を変更した場合、保持中のモデルを再ロードする。
- 選択したバックエンドまたはビルドが対応していない場合、暗黙にFalseへ変更せず、モデルロードエラーとして原因を表示する。

### 5.9 kv_cache_type

UI上では `q8_0` または `f16` を選択する。

選択値を `llama-cpp-python` のGGML型定数へ変換し、K及びVの両方へ同じ型を設定する。

```python
KV_CACHE_TYPE_MAP = {
    "q8_0": llama_cpp.GGML_TYPE_Q8_0,
    "f16": llama_cpp.GGML_TYPE_F16,
}

kv_type = KV_CACHE_TYPE_MAP[kv_cache_type]

Llama(
    ...,
    type_k=kv_type,
    type_v=kv_type,
)
```

- 生の整数 `8` や `1` を直接コードへ埋め込まず、公開定数を使用する。
- 定数がインストール済みバージョンに存在しない場合は、互換性エラーとして停止する。
- 選択値を変更した場合、保持中のモデルを再ロードする。
- 非対応モデルやバックエンドで失敗した場合、`f16` へ暗黙フォールバックしてはならない。

### 5.10 op_offload

`op_offload` はBooleanである。

```python
Llama(
    ...,
    op_offload=op_offload,
)
```

- `True` はホスト側テンソル演算のデバイスオフロードを有効にする。
- `False` は無効にする。
- 値を変更した場合、保持中のモデルを再ロードする。
- FLOATウィジェットとして実装してはならない。

### 5.11 keep_model_loaded

- `True` の場合、成功後も `Llama` インスタンスをノードインスタンス内に保持する。
- `False` の場合、成功、失敗を問わず処理終了時にモデルを解放する。
- `True` の場合でも、ロード時設定が変更された場合は旧モデルを破棄して再ロードする。
- 推論中に例外が発生し、モデルコンテキストの健全性を保証できない場合は、`True` でもモデルを破棄する。

### 5.12 seed

- `create_chat_completion()` の `seed` へ渡す。
- 同一入力、同一モデル、同一system prompt及び同一生成パラメータでは、可能な範囲で再現可能な翻訳を生成する。
- バッチごとに異なる決定論的seedを使用する場合は、`seed + batch_index` とする。
- 再試行では元バッチと衝突しない決定論的な値を使用する。
- 32bit範囲を超える加算結果は、1～4294967295の範囲へ正規化する。

### 5.13 keep_last_prompt

名称は既存 `QwenVL-Mod Prompt Enhancer (GGUF)` との操作上の一貫性のため維持するが、保持対象は「最後に検証成功したJSON文字列」である。

内部変数名は `last_json_text` とする。

#### False

通常のコンパイル処理を行う。成功したJSONを `last_json_text` に保存する。

#### Trueかつ履歴あり

- LLMロード、翻訳、パース及びJSON生成を実行しない。
- 現在の `plain_text` 及び生成設定を無視する。
- `last_json_text` をそのまま返す。
- モデルが未ロードでもモデルをロードしない。

#### Trueかつ履歴なし

- 空文字列を返さない。
- エラーにしない。
- `keep_last_prompt=False` と同じ通常処理を行う。
- 成功したJSONを `last_json_text` として保存して返す。

#### 保存期間

- 初期実装ではノードインスタンスのメモリ内だけに保存する。
- ファイル、ComfyUI設定、ワークフローJSON及び共有グローバル変数へ保存しない。
- ComfyUI再起動後は履歴なしの状態へ戻る。
- 複数の `CL Japanese to JSON (GGUF)` ノード間で履歴を共有しない。

### 5.14 steps

- 最終JSONの `defaults.steps` へJSON整数として設定する。
- 既定値は `8`、指定範囲は1～10000とする。
- Boolean及び整数以外の値を許可しない。
- LLMの翻訳生成パラメータではなく、変更時にGGUFモデルを再ロードしない。
- 既存ワークフローのウィジェット位置を変えないため、入力一覧の末尾へ追加する。

### 5.15 save_debug_output

- 既定値は`False`とし、既存ワークフローとの互換性を維持するため`optional`入力として追加する。
- `True`では成功・失敗を問わず、実行ごとの専用ディレクトリを `folder_paths.get_output_directory()/cl_japanese2json_debug/` 以下へ作成する。
- 原文、system prompt、各推論の保護済みストリーム、user request、LLM生応答、終了理由、token usage、検証結果を保存する。成功時は正規形Markdownと最終JSON、失敗時は例外型とメッセージも保存する。
- `ComfyUI/input`へは書き込まない。通常ログへ原文又はLLM生応答を表示しない。
- 診断ファイルにはユーザー入力と生成内容が含まれるため、ユーザーが明示的に有効化した実行だけ保存し、自動削除又は外部送信を行わない。
- デバッグ保存自体の失敗で、正常なJSON生成結果又は本来のコンパイル例外を置き換えてはならない。

## 6. INPUT_TYPES仕様

論理的な `INPUT_TYPES()` は次に相当する。

```python
@classmethod
def INPUT_TYPES(cls):
    model_names = cls.discover_model_names()

    return {
        "required": {
            "plain_text": (
                "STRING",
                {
                    "default": "",
                    "multiline": True,
                    "tooltip": "Japanese reduced Markdown compiled into MiniMax H3 Contex-Loop JSON.",
                },
            ),
            "model_name": (
                model_names,
                {
                    "default": model_names[0],
                    "tooltip": "GGUF text model discovered below ComfyUI/models/LLM/GGUF.",
                },
            ),
            "max_tokens": (
                "INT",
                {"default": 4096, "min": 32, "max": 16384, "step": 32},
            ),
            "temperature": (
                "FLOAT",
                {"default": 0.1, "min": 0.1, "max": 1.0, "step": 0.05},
            ),
            "top_p": (
                "FLOAT",
                {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.01},
            ),
            "repetition_penalty": (
                "FLOAT",
                {"default": 1.05, "min": 0.5, "max": 2.0, "step": 0.05},
            ),
            "gpu_layers": (
                "INT",
                {"default": -1, "min": -1, "max": 1000, "step": 1},
            ),
            "n_batch": (
                "INT",
                {"default": 256, "min": 32, "max": 4096, "step": 32},
            ),
            "n_ctx": (
                "INT",
                {"default": 0, "min": 0, "max": 131072, "step": 512},
            ),
            "flash_attn": ("BOOLEAN", {"default": True}),
            "kv_cache_type": (
                ["q8_0", "f16"],
                {"default": "q8_0"},
            ),
            "op_offload": ("BOOLEAN", {"default": True}),
            "keep_model_loaded": ("BOOLEAN", {"default": True}),
            "seed": (
                "INT",
                {"default": 1, "min": 1, "max": 4294967295},
            ),
            "keep_last_prompt": ("BOOLEAN", {"default": False}),
            "steps": (
                "INT",
                {"default": 8, "min": 1, "max": 10000, "step": 1},
            ),
        },
        "optional": {
            "save_debug_output": ("BOOLEAN", {"default": False}),
        },
    }
```

GGUFが1個も見つからない場合は、`model_names` を空配列にせず次のプレースホルダーを表示する。

```text
(no GGUF models found)
```

この値が選択された状態で実行された場合は、モデル検索先を含む明示的なエラーを発生させる。

## 7. パッケージ構造

初期実装の推奨構造は次のとおりとする。

```text
ComfyUI-cl-japanese2json/
├── __init__.py
├── nodes.py
├── model_discovery.py
├── llama_backend.py
├── debug_output.py
├── compiler/
│   ├── __init__.py
│   ├── llmj2e.py
│   ├── mdparse.py
│   ├── jsongen.py
│   ├── protected_text.py
│   ├── structures.py
│   └── errors.py
├── prompts/
│   └── llmj2e_qwen3_8b_system_prompt.txt
├── tests/
│   ├── test_model_discovery.py
│   ├── test_protected_text.py
│   ├── test_llmj2e.py
│   ├── test_mdparse.py
│   ├── test_jsongen.py
│   ├── test_node_cache.py
│   ├── test_node_integration.py
│   └── test_debug_output.py
├── README.md
├── LICENSE
└── pyproject.toml
```

責務を次のように分離する。

| ファイル | 責務 |
| --- | --- |
| `__init__.py` | ComfyUIノード登録 |
| `nodes.py` | UI定義、入力検証、処理オーケストレーション |
| `model_discovery.py` | GGUFルート探索、一覧生成、パス解決 |
| `llama_backend.py` | `Llama` ロード、再利用、推論、解放 |
| `debug_output.py` | 明示的に有効化された中間診断ファイルの保存 |
| `compiler/llmj2e.py` | 保護済み翻訳、バッチ分割、LLM出力検証 |
| `compiler/mdparse.py` | 正規形Markdownのパース |
| `compiler/jsongen.py` | Python構造から厳格JSON生成 |
| `compiler/protected_text.py` | タグ、台詞、一時トークン処理 |
| `compiler/structures.py` | `Scene`、`Emd` dataclass |
| `compiler/errors.py` | 専用例外 |
| `prompts/*.txt` | Qwen3 8B向けsystem prompt |

巨大な1ファイルへ全処理を実装してはならない。

## 8. ノード登録

`__init__.py` は次に相当するマッピングを公開する。

```python
from .nodes import CLJapaneseToJSONGGUF


NODE_CLASS_MAPPINGS = {
    "CLJapaneseToJSONGGUF": CLJapaneseToJSONGGUF,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLJapaneseToJSONGGUF": "CL Japanese to JSON (GGUF)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
```

同じNode IDを将来変更しない。表示名だけを変更する場合もNode IDは維持し、既存ワークフローとの互換性を保つ。

## 9. GGUFモデル探索

### 9.1 探索先

最低限、次を再帰走査する。

```python
Path(folder_paths.models_dir) / "LLM" / "GGUF"
```

ComfyUIへ追加の `LLM` モデルパスが登録されている場合は、それらも探索する。

論理例：

```python
roots = [Path(folder_paths.models_dir) / "LLM" / "GGUF"]

if "LLM" in folder_paths.folder_names_and_paths:
    for llm_root in folder_paths.get_folder_paths("LLM"):
        roots.append(Path(llm_root) / "GGUF")
        roots.append(Path(llm_root))
```

存在しないディレクトリはエラーにせず読み飛ばす。

### 9.2 対象ファイル

- 拡張子が `.gguf` の通常ファイルを対象とする。
- 拡張子判定は大文字小文字を区別しない。
- `mmproj` をファイル名に含むファイルは除外する。
- シンボリックリンクは通常ファイルとして安全に解決できる場合だけ許可する。
- 同じ実体パスは1回だけ表示する。

### 9.3 表示値

- 同一ルート内ではルートからの相対パスを表示する。
- 同名ファイルが複数ルートに存在する場合は、ルート識別子を付けて一意にする。
- 単なるbasenameだけを内部IDとして使用しない。
- ワークフローへ保存する選択値は、可能な限り絶対パスへ依存しない安定した表示IDとする。
- 実行時に表示IDから絶対パスへ再解決する。

### 9.4 ダウンロード

初期実装はモデルの自動ダウンロードを行わない。

- Hugging Faceへアクセスしない。
- GGUFファイルを作成、移動または削除しない。
- モデルが存在しない場合は、配置先を示すエラーを表示する。

## 10. システムプロンプト

### 10.1 パス

システムプロンプトは次の固定パスから読み込む。

```python
NODE_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = NODE_DIR / "prompts" / "llmj2e_qwen3_8b_system_prompt.txt"
```

現在の作業ディレクトリを基準にしてはならない。

### 10.2 読み込み

- UTF-8で読み込む。
- ファイルが存在しない場合は実行エラーとする。
- 空白だけの場合は実行エラーとする。
- BOMは除去してよい。
- 読み込みエラーのメッセージに期待パスを含める。
- 内容をComfyUIコンソールへ全文表示しない。

### 10.3 キャッシュ

- system prompt本文をメモリへキャッシュしてよい。
- ファイルのmtime、サイズまたはSHA-256が変化した場合は再読込する。
- system prompt変更だけではGGUFモデルを再ロードしない。
- system promptの指紋はComfyUIの `IS_CHANGED()` へ含める。

### 10.4 内容

system promptの内容は `cl_japanese2json_spec.md` の「システムプロンプトファイル」及び「翻訳結果の検証」に従う。

写真プロンプト、画像説明、創作、要約、JSON生成及びMarkdown全体の再構築を要求してはならない。

system promptには、すべてのプレースホルダを翻訳せず、1バイトも改変せず、移動、並べ替え、複製または削除しないガードを明記する。LLMは生テキスト翻訳ストリーム内の各 `SUB`、`COM` または `SCN` プレースホルダから次の構造プレースホルダまでにある通常の日本語文章だけを翻訳する。

## 11. llama-cpp-pythonバックエンド

### 11.1 独立性

- `QwenVL-Mod` のPythonモジュールをimportしてはならない。
- `QwenVL-Mod` が未インストールでも動作しなければならない。
- `folder_paths` 及びComfyUI本体の公開機能は使用してよい。
- モデル探索と `Llama` 管理は本パッケージ内で実装する。

### 11.2 llama-cpp-python未導入時

モジュールimport時にComfyUI全体をクラッシュさせない。

推奨方針：

```python
try:
    import llama_cpp
    from llama_cpp import Llama
    LLAMA_CPP_IMPORT_ERROR = None
except Exception as exc:
    llama_cpp = None
    Llama = None
    LLAMA_CPP_IMPORT_ERROR = exc
```

ノード自体は登録し、実行時にCUDAまたはCPUバックエンド対応の `llama-cpp-python` を別途導入する必要があることを明示する。

### 11.3 自動依存インストール禁止

CUDA対応版を手動導入している環境でCPU版へ置き換わることを防ぐため、初期実装の `pyproject.toml` または `requirements.txt` から `llama-cpp-python` を自動インストール・更新しない。

- `pip install -r requirements.txt` でCPU版を上書きしない。
- `llama-cpp-python` を無条件の依存関係へ記載しない。
- READMEへ手動導入要件を記載する。
- 既に導入済みのCUDA対応wheelまたはユーザーがビルドした環境を使用する。

### 11.4 モデルロード引数

論理的なロード処理は次に相当する。

```python
self.llm = Llama(
    model_path=str(resolved_model_path),
    n_ctx=n_ctx,
    n_gpu_layers=gpu_layers,
    n_batch=n_batch,
    flash_attn=flash_attn,
    type_k=kv_type,
    type_v=kv_type,
    offload_kqv=True,
    op_offload=op_offload,
    chat_format="qwen",
    verbose=False,
)
```

`offload_kqv` は初期実装ではUIへ公開せず `True` に固定する。

### 11.5 chat format

- Qwen3 GGUFを主対象とし、`chat_format="qwen"` を使用する。
- GGUFメタデータから適切なchat templateが利用できる場合も、Qwen3向けの既存実績を優先する。
- Qwen以外のGGUFが一覧へ表示されることは許容するが、正常動作は保証しない。
- 非対応モデルで失敗した場合、モデル名及びchat formatを含むエラーを表示する。

### 11.6 thinking無効化

Qwen3の推論テキストが翻訳結果へ混入しないよう、次の方針を適用する。

1. GGUFアーキテクチャまたはメタデータからQwen3系を検出する。
2. インストール済み `llama-cpp-python` が対応する場合、chat templateの `enable_thinking=False` を使用する。
3. 対応する呼び出し形式で `reasoning=False` を指定できる場合は使用する。
4. user messageの制御部分へ `/no_think` を追加する。
5. `/no_think` を保護済み翻訳ストリーム又は翻訳対象区間へ混入させない。
6. 最終的に応答先頭へ正常に閉じた `<think>...</think>` が1個だけ出現した場合は、その制御ブロックを破棄して後続翻訳を完全検証する。
7. 応答途中のthinking、未閉鎖、複数thinkingブロック、裸の推論文及び後続翻訳が空の応答は検証失敗とする。

インストール済みバージョンが未対応のキーワードを拒否する場合は、`TypeError` を無条件に握り潰して全引数を削除してはならない。対応可否をシグネチャ検査または限定的なフォールバックで判定し、最低限 `/no_think` と出力検証を維持する。

過去のPrompt Enhancerで使用していた「`<think>` があるだけで写真プロンプト用再試行へ移る」処理は使用しない。空の `<think></think>` を理由に別タスク用のsystem promptへ切り替えてはならない。

### 11.7 create_chat_completion

基本呼び出しは次に相当する。

```python
response = self.llm.create_chat_completion(
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_payload},
    ],
    max_tokens=max_tokens,
    temperature=temperature,
    top_p=top_p,
    repeat_penalty=repetition_penalty,
    seed=batch_seed,
    stop=[stream_stop_placeholder],
)
```

要件：

- 推論前に `self.llm.reset()` が利用可能なら呼び出す。
- レスポンス、`choices`、`message` 及び `content` の存在を検証する。
- 空文字列を成功として扱わない。
- `finish_reason == "length"` を失敗として扱う。
- LLMの生出力をそのまま最終ノード出力にしない。
- コア仕様の構造プレースホルダ、区間ごとの保護プレースホルダ及び日本語残留検査を通す。
- 構造プレースホルダが完全に0件の場合の段落順／行順フォールバックは、コア仕様の区間数及び保護プレースホルダ条件をすべて満たす場合だけ許可する。空行で区切られた段落を優先し、段落内の物理改行は空白へ正規化する。
- すべての`SUB/COM/SCN`区間先頭プレースホルダが完全一致して正順に各1回残り、未知又は重複した構造プレースホルダがない場合、`D`ディレクティブプレースホルダだけの欠落を許容し、Python側のブロック情報から復元してよい。
- 先頭SUBプレースホルダが番号付きSubjectタグと`is`の完全一致形式へ置換された場合の限定復旧は、コア仕様の連番、行数及び保護プレースホルダ検証をすべて満たす場合だけ許可する。それ以外のモデル追加Subject表現を受理しない。

### 11.8 JSONモード不使用

翻訳呼び出しでは `llama-cpp-python` の `response_format={"type": "json_object"}` を使用しない。JSON文法制約による生成負荷と、長いJSON文字列のエスケープ及び構造欠落を避ける。

最終JSONはLLM出力ではなく `JSONGEN` が生成する。LLMの入力と応答は文書順を保った単一の生テキスト翻訳ストリームとし、転送用JSON、レコード配列及び重複したプレースホルダ一覧を使用してはならない。コア仕様の一対一検証を必須とし、LLMがContex-Loop Plan全体を直接生成する設計へ変更してはならない。

## 12. モデル保持と再ロード

### 12.1 ノードインスタンス状態

各ノードインスタンスは最低限次を保持する。

```python
self.llm = None
self.current_model_signature = None
self.last_json_text = None
self._lock = threading.RLock()
```

共有グローバルの `LAST_SAVED_PROMPT` を使用してはならない。

### 12.2 モデルシグネチャ

モデルシグネチャには、最低限次を含める。

```text
resolved_model_path
model_file_size
model_file_mtime_ns
n_ctx
gpu_layers
n_batch
flash_attn
kv_cache_type
op_offload
```

推奨表現：

```python
signature = (
    str(resolved_model_path.resolve()),
    stat.st_size,
    stat.st_mtime_ns,
    int(n_ctx),
    int(gpu_layers),
    int(n_batch),
    bool(flash_attn),
    str(kv_cache_type),
    bool(op_offload),
)
```

次はモデルシグネチャへ含めない。

```text
plain_text
max_tokens
temperature
top_p
repetition_penalty
keep_model_loaded
seed
keep_last_prompt
steps
save_debug_output
system prompt本文
```

### 12.3 再利用

- `self.llm` が存在し、シグネチャが完全一致する場合は再利用する。
- シグネチャが異なる場合、旧モデルを完全に解放してから新モデルをロードする。
- 新モデルのロード成功後だけ `current_model_signature` を更新する。
- ロード失敗時は `self.llm=None`、`current_model_signature=None` とする。

### 12.4 解放

モデル解放処理は1個の `clear_model()` に集約する。

論理手順：

1. `self.llm.close()` があれば呼び出す。
2. `self.llm` 参照を `None` にする。
3. `current_model_signature` を `None` にする。
4. `gc.collect()` を呼び出す。
5. PyTorch CUDAが利用可能なら `torch.cuda.empty_cache()` を呼び出してよい。
6. `torch.cuda.ipc_collect()` は利用可能な場合だけ例外を捕捉して呼び出してよい。

`__del__()` を直接呼ぶ方法は、`close()` がない古いバージョンの最終フォールバックに限定する。

### 12.5 排他制御

- モデルロード、推論、`last_json_text` 読み書き及び解放は同じノードインスタンスの `RLock` で保護する。
- ロックを保持したまま不要なファイル走査を行わない。
- 複数ノードインスタンスは独立したモデルと履歴を持つ。

## 13. 実行フロー

`compile_json()` は次の順序で処理する。

```text
1. 入力型と範囲を検証
2. keep_last_promptと履歴を確認
3. 履歴があれば即時返却
4. plain_textの空入力を検証
5. system promptを読込
6. model_nameを絶対パスへ解決
7. モデルシグネチャを生成
8. 必要なら旧モデルを解放
9. GGUFモデルをロードまたは再利用
10. LLMJ2Eで保護、翻訳、復元、検証
11. MDPARSEでEmdを生成
12. JSONGENでJSON文字列を生成
13. json.loads()で再検証
14. Contex-Loopサブセット構造を検証
15. last_json_textへ保存
16. save_debug_output=Trueなら成功中間結果をComfyUI/outputへ保存
17. keep_model_loaded=Falseならモデル解放
18. (json_text,) を返す
```

### 13.1 擬似コード

```python
def compile_json(...):
    with self._lock:
        if keep_last_prompt and self.last_json_text is not None:
            return (self.last_json_text,)

        validate_plain_text(plain_text)
        validate_ui_parameters(...)

        system_prompt = load_system_prompt()
        model_path = resolve_model(model_name)

        try:
            llm = self.ensure_model_loaded(
                model_path=model_path,
                n_ctx=n_ctx,
                gpu_layers=gpu_layers,
                n_batch=n_batch,
                flash_attn=flash_attn,
                kv_cache_type=kv_cache_type,
                op_offload=op_offload,
            )

            canonical_markdown = llmj2e.translate(
                plain_text=plain_text,
                llm=llm,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                seed=seed,
            )

            emd = mdparse.parse(canonical_markdown)
            json_text = jsongen.generate(emd, steps=steps)
            validate_final_json(json_text)

            self.last_json_text = json_text
            return (json_text,)

        except Exception:
            self.clear_model()
            raise

        finally:
            if not keep_model_loaded:
                self.clear_model()
```

例外処理は実装時に二重解放が安全になるよう、`clear_model()` を冪等にする。

## 14. 最終JSON検証

LLMの翻訳検証とは別に、ノードは最終JSONへ次の検証を行う。

```python
parsed = json.loads(json_text)
```

最低限の検証項目：

- ルートが辞書である。
- `prompt_prefix` が文字列である。
- `defaults` が辞書である。
- `defaults.duration_seconds` が整数である。
- `defaults.steps` が整数である。
- `defaults.steps` が1～10000である。
- `shots` が1～128要素の配列である。
- 各shotが辞書である。
- 各shotの `id` が一意の文字列である。
- 各shotの `prompt` が文字列配列である。
- 各shotの `prompt` 最終要素が `non_diegetic_music:\nN/A` である。
- 各shotの `duration_seconds` が正の整数である。
- `continuation_mode` が存在する場合は `guide` である。
- 非継続シーンには `context_length: 0` と `audio_context_length: 0` が存在する。
- 末尾に1個のLFがある。

検証後に再シリアライズしてもよいが、キー順序はJSONGENが生成した読みやすい順序を維持する。

## 15. ComfyUIキャッシュ

### 15.1 基本方針

ComfyUI標準の入力キャッシュを利用する。

- 同じ入力と同じ外部ファイル指紋の場合、ComfyUIが既存出力を再利用してよい。
- 常時再実行させるための `float("NaN")` を返してはならない。
- `IS_CHANGED()` からBooleanの `True` または `False` を固定で返してはならない。

### 15.2 IS_CHANGED

V1ノードの `IS_CHANGED()` は、外部ファイル変更を検出するために使用する。

指紋へ含めるもの：

```text
選択されたGGUFの解決パス
GGUFのサイズ
GGUFのmtime_ns
system promptファイルのSHA-256またはmtime_nsとサイズ
```

入力ウィジェット値はComfyUI標準キャッシュが管理するため、外部指紋へ重複して含めなくてよい。

モデルまたはsystem promptファイルを解決できない場合でも `IS_CHANGED()` 自体でモデルをロードしない。安定したエラー指紋を返し、実際のエラーは `compile_json()` で発生させる。

### 15.3 keep_last_promptとの関係

- `keep_last_prompt=True` はノード内部の明示的バイパスである。
- 入力が変わりComfyUIがノードを実行しても、履歴があれば直前JSONを返す。
- ComfyUIがノードを実行しない場合は、標準キャッシュされた出力が使われる。
- `keep_last_prompt=False` へ戻すと、その入力変更に応じて通常処理される。

## 16. エラー処理

### 16.1 方針

- エラー文字列を正常な `json_text` 出力として返してはならない。
- 例外を発生させ、ComfyUIのノード実行を失敗させる。
- 元例外を `raise ... from exc` で保持する。
- JSONとして不正な途中結果を `last_json_text` へ保存しない。
- 以前の有効な `last_json_text` は、新しい通常処理が失敗しても削除しなくてよい。

### 16.2 専用例外

推奨例外：

```python
class CLJapaneseToJSONError(RuntimeError): ...
class ModelDiscoveryError(CLJapaneseToJSONError): ...
class ModelLoadError(CLJapaneseToJSONError): ...
class SystemPromptError(CLJapaneseToJSONError): ...
class TranslationError(CLJapaneseToJSONError): ...
class ProtectedTextError(CLJapaneseToJSONError): ...
class MarkdownParseError(CLJapaneseToJSONError): ...
class JSONGenerationError(CLJapaneseToJSONError): ...
class JSONValidationError(CLJapaneseToJSONError): ...
```

### 16.3 エラーメッセージ

エラーには、可能な範囲で次を含める。

- 処理段階
- 選択モデルの表示名
- 使用した `n_ctx`、`n_batch`、`gpu_layers`
- 入力レコード番号またはシーン番号
- 期待した構文
- system promptの期待パス

エラーへ次を含めない。

- system prompt全文
- 入力プロンプト全文
- 認証情報
- 不要なメモリアドレス
- 巨大なLLM生出力全文

通常ログへLLM生出力を含めない。`save_debug_output=True`の場合に限り、LLM生出力全文を実行専用の診断ディレクトリへ保存してよい。

## 17. ログ

ログ接頭辞を統一する。

```text
[cl_japanese2json]
```

通常ログ例：

```text
[cl_japanese2json] Discovered 3 GGUF model(s)
[cl_japanese2json] Loading model: qwen3-8b-abliterated-Q4_K_M.gguf
[cl_japanese2json] n_ctx=16384 gpu_layers=-1 n_batch=256 flash_attn=True kv_cache=q8_0 op_offload=True
[cl_japanese2json] Prepared one protected translation stream for 46 text segment(s); using 1 inference request(s)
[cl_japanese2json] Translating batch 1/1 with 46 text segment(s)
[cl_japanese2json] LLM tokens: prompt=4210 completion=2874 total=7084
[cl_japanese2json] Markerless response shape: paragraphs=1 non_empty_lines=45 protected_exact=60/62 protected_occurrences=60
[cl_japanese2json] Salvaged 42/46 markerless text segment(s) using protected-placeholder anchors
[cl_japanese2json] LLM validation failed for batch 1; retrying 4 unresolved text segment(s) once: ...
[cl_japanese2json] Generated 3 scene(s)
[cl_japanese2json] Saved debug output: C:\...\ComfyUI\output\cl_japanese2json_debug\...
[cl_japanese2json] Returning cached last JSON
[cl_japanese2json] Unloading model
```

通常ログへ日本語入力全文、翻訳全文及びJSON全文を表示しない。

## 18. テスト

### 18.1 単体テスト

#### model_discovery

- `models/LLM/GGUF` 以下の `.gguf` を再帰検出する。
- `mmproj` を除外する。
- 同じパスを重複表示しない。
- 同名モデルを一意に識別する。
- モデル0件でプレースホルダーを返す。

#### parameter mapping

- `gpu_layers=-1` が `n_gpu_layers=-1` になる。
- `n_ctx=0` がそのまま渡される。
- `q8_0` が `GGML_TYPE_Q8_0` になる。
- `f16` が `GGML_TYPE_F16` になる。
- KとVに同じ型が設定される。
- `op_offload` がBooleanとして渡される。
- 生成パラメータ変更ではモデル再ロードしない。
- ロード時パラメータ変更ではモデル再ロードする。

#### model lifecycle

- 同一シグネチャでモデルを再利用する。
- 異なるシグネチャで旧モデルを解放する。
- `keep_model_loaded=False` で成功後に解放する。
- 推論例外後にモデルを解放する。
- `clear_model()` を複数回呼んでも例外にならない。

#### keep_last_prompt

- Falseで通常処理する。
- Trueかつ履歴ありでLLMを呼ばない。
- Trueかつ履歴なしで通常処理する。
- 初回成功結果を履歴へ保存する。
- 複数ノードインスタンスで履歴を共有しない。
- 失敗結果を履歴へ保存しない。

#### system prompt

- ノードディレクトリ基準で読み込む。
- カレントディレクトリに依存しない。
- 欠落、空ファイル及び不正UTF-8で明示的に失敗する。
- 内容変更を `IS_CHANGED()` が検出する。
- 内容変更だけではモデル再ロードしない。

#### compiler

`cl_japanese2json_spec.md` 第12章の全受入テストを実装する。

特にJSONGENについて、無発声シーンからAudio参照句が除去されること、発声シーンでは維持されること、否定された発声動詞を無発声として扱うことをテストする。

### 18.2 Llamaモックテスト

実GPU及びGGUFを必要としないモックを用意する。

モックは次を記録できるようにする。

```text
Llamaコンストラクタ引数
create_chat_completion引数
reset呼び出し回数
close呼び出し回数
生成レスポンス
```

テストケース：

- 正常翻訳
- 空レスポンス
- `choices` 欠落
- `finish_reason=length`
- 保護トークン欠落
- 区間先頭プレースホルダの順序変更
- ディレクティブプレースホルダだけの欠落を区間先頭プレースホルダから復元
- `<think>` 混入
- 再試行成功
- 再試行失敗
- `json.loads()` 失敗

### 18.3 ComfyUI統合テスト

- `NODE_CLASS_MAPPINGS` からNode IDを取得できる。
- `INPUT_TYPES()` がすべての指定入力を持つ。
- `RETURN_TYPES == ("STRING",)` である。
- `compile_json()` が1要素タプルを返す。
- 出力をContex-Loop Planの `plan_json_input` へ接続できる。
- 同一入力の再キューでComfyUIキャッシュが利用可能である。
- `seed`、`plain_text` または生成パラメータ変更で必要な再実行が行われる。

### 18.4 実モデル試験

最低限Qwen3 8B GGUFで次を確認する。

- 1シーン
- 複数シーン
- Subject 1～4
- 日本語台詞を含むシーン
- 既存の `<d>[Japanese]...</d>`
- LF及びCRLF
- `n_ctx=0`
- `n_ctx=16384`
- `q8_0`及び`f16`
- `flash_attn=True`
- `gpu_layers=-1`
- `keep_model_loaded=True/False`
- `keep_last_prompt=True/False`
- `save_debug_output=True/False`

## 19. README要件

READMEには最低限次を記載する。

1. ノードの目的
2. 日本語縮小版Markdownの短い入力例
3. JSON出力例
4. `models/LLM/GGUF` へのモデル配置方法
5. CUDA対応 `llama-cpp-python` をユーザー自身で導入する必要があること
6. `llama-cpp-python` を自動インストールしない理由
7. 各UIパラメータの意味
8. `gpu_layers=-1`、`n_ctx=0` の意味
9. `op_offload` がBooleanであること
10. system promptファイルの場所
11. `keep_last_prompt` の初回動作
12. Contex-Loop Planへの接続方法
13. 主なエラーと対処方法
14. `save_debug_output`の保存先、保存内容及び機密性の注意
15. GPL-3.0及び参考元の表示

## 20. ライセンス

本ノードは独立パッケージとするが、`ComfyUI-QwenVL-Mod` の実装を参考または改変してモデル管理処理を作成するため、初期実装のライセンスはGPL-3.0とする。

最低限、次を行う。

- `LICENSE` にGPL-3.0全文を含める。
- READMEへ参考元を記載する。
- コピーまたは改変したファイルへ出典と変更内容を記載する。
- `ComfyUI-QwenVL-Mod` の著作権表示を必要な範囲で保持する。
- `llama-cpp-python` 自体はMITライセンスであることを第三者通知へ記載してよい。

参考元：

```text
https://github.com/huchukato/ComfyUI-QwenVL-Mod
https://github.com/abetlen/llama-cpp-python
https://docs.comfy.org/custom-nodes/backend/server_overview
https://docs.comfy.org/custom-nodes/backend/datatypes
```

## 21. Codexへの実装指示

Codexへ本仕様を渡す際は、同じ作業コンテキストへ `cl_japanese2json_spec.md` も必ず与える。

Codexは次の順序で実装する。

1. 両仕様書を完全に読む。
2. 実装計画とファイル一覧を提示する。
3. パッケージ骨格を作成する。
4. LLMを使わないコア変換処理とテストを先に実装する。
5. GGUFモデル探索を実装する。
6. `llama_backend.py` を実装する。
7. ComfyUIノードラッパーを実装する。
8. system promptテキストファイルを作成する。
9. モックテストを実行する。
10. 可能ならComfyUI importテストを実行する。
11. 実モデルを必要とする試験は自動テストと分離する。
12. READMEとライセンスを作成する。
13. 変更ファイル、テスト結果及び未実施試験を報告する。

実装上の禁止事項：

- LLMへContex-Loop Plan JSON全体を直接生成させない。
- LLM出力を検証せず返さない。
- 写真プロンプト用のretry system promptを使用しない。
- グローバルな `LAST_SAVED_PROMPT` を使用しない。
- `llama-cpp-python` のCPU版を自動インストールしない。
- モデルを自動ダウンロードしない。
- system promptをPythonへ埋め込まない。
- 例外を空文字列へ変換しない。
- `op_offload` をFLOATとして実装しない。
- `kv_cache_type` の選択を無視しない。
- ロード時設定変更後も古いモデルを再利用しない。
- ユーザーの既存ComfyUI、モデル及び他のcustom_nodesを変更しない。

## 22. 完了条件

次をすべて満たした場合、ComfyUIカスタムノードの初期実装を完了とする。

1. `ComfyUI-cl-japanese2json` が `QwenVL-Mod` なしでimportできる。
2. `CL Japanese to JSON (GGUF)` がComfyUIのノード一覧へ表示される。
3. `models/LLM/GGUF` のGGUFを選択できる。
4. 指定された16個の必須入力と1個の任意入力がすべて存在する。
5. `op_offload` がBooleanである。
6. `kv_cache_type` がK及びVのキャッシュ型へ反映される。
7. ロード時設定変更でモデルが正しく再ロードされる。
8. `keep_model_loaded` が正しくモデルを保持または解放する。
9. `keep_last_prompt` の履歴あり、履歴なしの両動作が仕様どおりである。
10. system promptをノードディレクトリ内のテキストファイルから読み込む。
11. 日本語縮小版Markdownから厳格なContex-Loop JSON文字列を生成できる。
12. 出力を `json.loads()` で再読込できる。
13. 出力をContex-Loop Planの文字列入力へ接続できる。
14. コア仕様及び本仕様の自動テストが成功する。
15. README、GPL-3.0ライセンス及び参考元表示が存在する。
