# CL 32-bit Seed仕様

## 1. 目的

`CL 32-bit Seed`は、seed上限が異なるGGUF/llama.cpp系ノードとMiniMax H3/Context Loop系ノードへ、同じ再現可能な`INT`を供給する補助ノードである。出力を符号付き32-bit正整数`1～2147483647`へ限定する。

現在対応対象の入力範囲は次の包含関係にある。

- 本プロジェクトのGGUFノード: `1～4294967295`
- MiniMax H3 Context Loop: `0～18446744073709551615`
- 本ノードの出力: `1～2147483647`

したがって本ノードの全出力は双方で有効である。H3が64-bit seedを受理することは、32-bit値を拒否することを意味しない。出力を両系統へ分岐して同じ値を接続できる。

## 2. 公開契約

- ノード型名: `CLSeed32`
- 表示名: `CL 32-bit Seed`
- カテゴリ: `MiniMax H3/Utilities`
- 入力: `seed`, `mode`, 非表示transport `hold_next`
- 出力: `seed` (`INT`)
- 出力ノードではない。
- LLM、モデル、ファイルI/O及び外部カスタムノードに依存しない。

## 3. 入力

| 名前 | 型 | 既定 | 契約 |
| --- | --- | --- | --- |
| `seed` | INT | `-1` | `-1`又は`1～2147483647`。`0`は禁止 |
| `mode` | COMBO | `random` | `fixed`, `random` |
| `hold_next` | BOOLEAN | `false` | `random`ボタン用の一回限り状態。ブラウザ上は非表示 |

`seed`、`mode`及び`hold_next`は通常のウィジェット値としてワークフローJSONへ保存する。乱数生成器の内部状態やPythonオブジェクトを保存しない。

ComfyUI標準の`control_after_generate`（UI表示名「生成後の制御」）は明示的に無効化する。seedの自動更新方針は`mode`だけで決定し、同じ状態を二つのコンボボックスで管理しない。

## 4. 実行規則

1. `seed=-1`ならmodeによらず`1～2147483647`から一様な乱数を生成する。
2. `mode=fixed`かつseedが正なら、その値を変更せず出力する。
3. `mode=random`かつ`hold_next=false`なら、実行時に新しい乱数を生成して出力する。
4. `mode=random`かつ`hold_next=true`なら、現在表示されている正のseedを一回だけそのまま出力する。
5. 正常実行後、ブラウザへ実使用seedを返してseedウィジェットを更新し、`hold_next=false`へ戻す。

乱数はPythonでは`secrets.randbelow(2147483647)+1`、ブラウザのrandomボタンではWeb Cryptoの拒否サンプリングを優先する。いずれも0と範囲外を生成しない。

## 5. UI操作

- `fixed`: 現在表示しているseedを正の値として確定し、modeを`fixed`へ変更する。表示値が`-1`なら先に乱数へ置換する。
- `reuse`: 要求どおり`fixed`と同じく、現在表示しているseedを固定する。
- `random`: ボタン押下時に新しいseedを表示し、modeを`random`、`hold_next=true`とする。直後の一回は画面で確認した値を使用し、その後は実行ごとに更新する。

各ボタン自体はワークフローへシリアライズしない。変更後のseed、mode及び一回限り状態だけを保存する。

## 6. キャッシュ

- `fixed`の正のseedは安定した`IS_CHANGED`値を返し、ComfyUI標準キャッシュを許可する。
- `random`かつ`hold_next=false`、又は`seed=-1`は`NaN`を返して実行ごとに再評価する。
- randomボタン直後の`hold_next=true`は現在値で安定し、一回目の出力を再現可能にする。

## 7. エラー

bool、非整数、0、-2以下、2147483648以上、不明mode及び非boolの`hold_next`は、乱数を消費せず検証エラーとする。

## 8. テスト

- 公開登録、入力順序、出力型及び範囲。
- fixed、random、一回保持及び`-1`の状態遷移。
- 下限1、上限2147483647及び不正値拒否。
- random時だけのキャッシュ無効化。
- 実行結果によるUI seed更新と一回保持解除。
- fixed/reuse/randomボタン、Web Crypto及び非表示transportのフロントエンド契約。
