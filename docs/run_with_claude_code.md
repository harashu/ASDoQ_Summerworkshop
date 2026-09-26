# APIキーなしで試す手順（Claude Code のサブエージェントを使う方法）

Anthropic の APIキーがない環境で、`runner/review.py` と同じワークフローを Claude Code の中で再現する手順です。2026-09-26 の3回の試行は、この方法で行いました。

- 追加の APIキーや支払いは不要です（会社が契約している Claude Code の利用枠の範囲で動きます）。
- Python がなくても、Windows 標準の PowerShell で検証と照合ができます。
- `review.py` とまったく同じ条件にはならないので、厳密な数値比較は、APIキーを用意してから `review.py` で行ってください。

## 全体の流れ

| ステップ | やること | 担当 |
|---|---|---|
| 1. パネル選定 | `docs/design.md` の標準パネル表とメタ情報の追加ルールで選ぶ | Claude Code（ルールどおりに選ぶだけ） |
| 2. ペルソナレビュー | ペルソナ1人につきサブエージェントを1つ起動し、並列にレビューさせる | サブエージェント |
| 3. 検証・基礎スコア | `runner/claude_code/validate.ps1` | PowerShell |
| 4. 統合 | オーケストレーター役のサブエージェントに統合させる | サブエージェント |
| 5. 照合 | `runner/claude_code/evaluate.ps1`（テスト文書のときだけ） | PowerShell |

## ステップ2：ペルソナのサブエージェントへの指示

各サブエージェントには、次の内容を渡します。

1. **読んでよいファイルを2つに限定する**：`prompts/common/reviewer_base.md`（共通プロンプト）と、レビュー対象の文書。
2. **ほかのファイルを読ませない**：特に `tests/` の正解表（`answer_key.*`）、`knowledge/`、`prompts/personas/`、`runs/`。
3. **ペルソナの本文だけを指示文に直接書く**：ペルソナファイルのフロントマター（重みや補正方針）はペルソナに見せない設計なので、ファイルを読ませずに本文だけを渡す。
4. **文書のメタ情報**（種類・目的・想定読者）を渡す。
5. **出力を `runs/<実行名>/raw/<ペルソナID>.txt` に保存させ、最終回答は「saved」だけにさせる**：出力を会話に返させると、メインの会話が長くなりすぎるため。

## ステップ3：検証と基礎スコア

```powershell
.\runner\claude_code\validate.ps1 -RunDir runs\<実行名> -DocPath tests\cases\case01_srs\document.md -PersonaDir prompts\personas
```

- `findings.json`（検証を通った指摘）、`excluded.json`（除外した指摘）、`good_points.json` を作ります。
- 検査内容は `review.py` と同じです（副特性の公式名称、品質特性との親子関係、引用が文書に実在するか、基礎スコアの計算式）。

## ステップ4：オーケストレーターのサブエージェントへの指示

- 指示書として `prompts/orchestrator/02_integration.md` を最初に読ませる。
- 読んでよいファイル：共通プロンプトの ASDoQ 部分、文書、`meta.json`、`knowledge/` の3ファイル、`findings.json`・`good_points.json`・`excluded.json`、パネルのペルソナファイルのフロントマター。
- **正解表は読ませない。**
- 出力として `plan.md`、`final_review.md`、`trace.json`、`raw/integration.txt` を書かせる。
- `trace.json` の `source_findings` に、すべての指摘がちょうど1回ずつ入るように指示する。

## ステップ5：照合

```powershell
.\runner\claude_code\evaluate.ps1 -RunDir runs\<実行名> -CaseDir tests\cases\case01_srs -KnowledgeDir knowledge
```

- `evaluation.md` と `verification.json`（判断の記録のスコア再計算）を作ります。
- 照合ルールは `tests/README.md` を参照してください。

## PowerShell 5.1 で気をつけること（試行で実際に起きた問題）

| 問題 | 原因 | 対策 |
|---|---|---|
| スクリプトの日本語が文字化けして構文エラーになる | PowerShell 5.1 は BOM のない UTF-8 ファイルを ANSI（Shift_JIS）として読む | `.ps1` は **BOM 付き UTF-8** で保存する |
| 補正後の重要度 4.5 が 4 に丸められる | `[math]::Max(1, 4.5)` が整数どうしの比較として扱われる | `[double]1.0` のように、明示的に小数にする |
| JSON の配列を読むと、配列全体が1個の要素になる | 5.1 の `ConvertFrom-Json` の仕様 | `(… \| ConvertFrom-Json) \| ForEach-Object { $_ }` で展開する |
| `ConvertTo-Json` の結果をファイルに書くと文字コードが変わる | 既定の文字コードが UTF-8 ではない | `[IO.File]::WriteAllText(パス, 内容, UTF8Encoding)` で書く |
| 公式名称でない副特性（例：「非曖昧性（識別性）／可読性」）が検査をすり抜ける | `if (@($null) \| Where-Object {...})` の結果が `$null` になり、条件が偽と判定される | 不正な候補を別の配列に集め、`.Count -gt 0` で判定する |
