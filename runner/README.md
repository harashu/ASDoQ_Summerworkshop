# 実行スクリプト

[docs/design.md](../docs/design.md) のワークフローを、フレームワークを使わずに Claude API を直接呼んで動かします。

| ファイル | 内容 |
|---|---|
| `review.py` | レビューを実行する（パネル選定 → ペルソナ並列レビュー → 検証 → 判定役（任意）→ 統合） |
| `evaluate.py` | 結果を正解（`tests/cases/*/answer_key.json`）と照合して指標を出す。APIは呼ばない |
| `asdoq.py` | ASDoQ 6品質特性・17副特性・測定項目の定義（検証に使う） |
| `claude_code/` | APIキーなしで試すときの PowerShell 版（`validate.ps1`：検証と基礎スコア、`evaluate.ps1`：照合）。手順は [docs/run_with_claude_code.md](../docs/run_with_claude_code.md) |

## 準備

1. **Python 3.10 以上をインストールする。** [python.org](https://www.python.org/downloads/) からインストールします（インストール時に「Add python.exe to PATH」にチェック）。社内のソフトウェア導入ルールがあれば従ってください。
2. **パッケージをインストールする。**
   ```bash
   pip install -r runner/requirements.txt
   ```
3. **APIキーを設定する。** Anthropic の APIキーを環境変数に設定します（PowerShell の例）。キーはファイルに書かないでください。
   ```powershell
   $env:ANTHROPIC_API_KEY = "（あなたのAPIキー）"
   ```

> **データの取り扱いに注意してください。** レビュー対象の文書は Anthropic の API に送信されます。社外秘の文書を使う前に、社内の情報セキュリティのルールを確認してください。`tests/` のテスト文書は架空の内容です。

## 使い方

以下のコマンドは、プロジェクトのフォルダ（`260925_aihara`）で実行します。

**1. まず APIを呼ばずに確認する（無料）**

パネルの選定結果と、各ペルソナに送るプロンプトを確認できます。

```bash
python runner/review.py --case tests/cases/case01_srs --dry-run
```

**2. 最小構成で実行する**

標準パネル（＋ meta.json のフラグによる追加）だけで実行します。

```bash
python runner/review.py --case tests/cases/case01_srs
```

**3. 結果を正解と照合する**

`review.py` の最後に表示されるコマンドをそのまま実行します。

```bash
python runner/evaluate.py --run runs/case01_srs_YYYYMMDD_HHMMSS --case tests/cases/case01_srs
```

**4. 構成を変えて比べる**（設計書の「評価（テスト）計画」）

| やりたいこと | オプション |
|---|---|
| 困った人型（C群）を1人加える | `--with-c` |
| 層2 特性別の判定役を使う | `--judges` |
| パネルを手動で指定する | `--panel A11,A05,A01` |
| 思考の深さを変える | `--effort medium`（既定は `high`） |
| モデルを変える | `--model claude-sonnet-5`（既定は `claude-opus-5`） |
| 同時に実行する数を変える | `--concurrency 2`（レート制限にかかる場合） |

LLMの出力は毎回変わるので、同じ構成で3回以上実行して平均を見てください。

## 出力（`runs/<ケース名>_<日時>/`）

| ファイル | 内容 |
|---|---|
| `final_review.md` | **書き手に渡す最終レビュー** |
| `plan.md` | オーケストレーターの統合方針 |
| `trace.json` | 判断の記録（統合元の指摘、言い換え、スコアの内訳、知識の引用） |
| `verification.json` | 判断の記録のスコアをコードで再計算した結果（食い違いの一覧） |
| `panel.json` | 選ばれたパネルと選定理由 |
| `findings.json` | 検証を通ったペルソナの指摘（基礎スコア付き） |
| `excluded.json` | 検証で除外された指摘と理由（引用が文書にない、副特性が公式名称でない など） |
| `good_points.json` | ペルソナが挙げた良い点 |
| `judgments.json` | 判定役の結果（`--judges` のときだけ） |
| `usage.json` | 呼び出しごとのトークン数と概算費用 |
| `raw/` | 各LLM呼び出しの生の出力 |
| `evaluation.md` / `evaluation.json` | `evaluate.py` の照合結果 |

## 費用の目安

概算です。実際の値は `usage.json` で確認してください。

| 構成 | API呼び出し | 1回あたりの目安（Claude Opus 5） |
|---|---|---|
| 標準パネル6人 ＋ 統合 | 7回 | 1.5〜2ドル程度 |
| ＋ 判定役（`--judges`） | 13回 | 2.5〜3.5ドル程度 |

- 共通プロンプトはプロンプトキャッシュの対象にしています。
- 拒否時のサーバー側フォールバック（`fallbacks: "default"`）を既定で有効にしています。不要なら `--no-fallback` を付けてください。

## 仕組み（3原則との対応）

- **シンプルさ**：パネル選定・検証・スコア計算はコードで行い、LLMは判断が必要な処理だけに使います。判定役は既定では使いません。
- **透明性**：統合の方針（`plan.md`）と判断の記録（`trace.json`）を必ず残し、スコアの計算内訳をコードで再計算して `verification.json` に食い違いを出します。
- **ACIの作り込み**：ペルソナの出力は、副特性の公式名称、副特性と品質特性の親子関係、引用が文書中に実在するかをコードで検査します。形式エラーのときは1回だけ再実行します。
