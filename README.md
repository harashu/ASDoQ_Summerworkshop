# ASDoQ 文書品質モデルに基づくマルチペルソナ文書レビューエージェント

ASDoQ サマーワークショップ（2026年9月）向けの試作です。人格の異なる複数のレビュアー（ペルソナ）が、ASDoQ システム開発文書品質モデルの6品質特性・17副特性に従って文書をレビューし、指揮者（オーケストレーター）が会社テンプレ・業界ノウハウ・顧客特性の知識で重み付けして、最終レビューにまとめます。

## 前提にしている知識

| 知識 | 文書 |
|---|---|
| ASDoQ システム開発文書品質モデル Ver. 2.0a（6品質特性・17副特性・測定項目・用語集） | `docs/reference/ASDoQ_SystemDocumentationQualityModel_v2.0a.md`（※） |
| 再構成版の解説（列の意味、紛らわしい副特性の見分け方） | `docs/reference/ASDoQ文書品質モデル_再構成版用解説.md`（※） |
| Anthropic「Building effective agents」の設計3原則 | [docs/reference/building_effective_agents_要点.md](docs/reference/building_effective_agents_要点.md) |

※ ASDoQ の資料は著作権の関係で、公開リポジトリには含めていません。システム開発文書品質研究会（ASDoQ）から入手し、上記のファイル名で `docs/reference/` に置いてください。

### 出典について

- `prompts/common/reviewer_base.md` と `runner/asdoq.py` に載せている6品質特性・17品質副特性・測定項目の名称と説明は、システム開発文書品質研究会（ASDoQ）「システム開発文書品質モデル Ver. 2.0a」に基づいています。
- ワークショップ教材（ブロック崩しゲームの要求仕様書と改訂案。テストケース `case02_blockbreaker`）とその実行結果も、公開リポジトリには含めていません。

## フォルダ構成

| フォルダ・ファイル | 内容 |
|---|---|
| [docs/design.md](docs/design.md) | 設計書（層と役割、ワークフロー、標準パネル表、スコア計算式、評価計画） |
| [docs/lessons_learned.md](docs/lessons_learned.md) | 試行の記録と、そこから得た知見 |
| [docs/run_with_claude_code.md](docs/run_with_claude_code.md) | APIキーなしで試す手順（Claude Code のサブエージェントを使う） |
| [prompts/](prompts/README.md) | 共通プロンプト、29人のペルソナ、オーケストレーター、判定役のプロンプト |
| [knowledge/](knowledge/README.md) | オーケストレーターだけが使う知識ベース（会社テンプレ・業界ノウハウ・顧客特性。中身は例） |
| [runner/](runner/README.md) | 実行スクリプト（`review.py`：APIで実行、`evaluate.py`：正解と照合、`claude_code/`：APIキーなしの試行用 PowerShell） |
| [tests/](tests/README.md) | 欠陥を仕込んだテスト文書と正解表 |
| `runs/` | 実行結果 |
| `persona_mindmap.html` | ペルソナ分類のマインドマップ（PNG・SVGで保存できる） |

## 構成の概要（2026-09-26 時点）

```
オーケストレーター（議長）：パネル選定 ─ 文書の種類とメタ情報から標準パネル表で選ぶ（コード）
        │
層1 ペルソナ（4〜7人・並列）─ 詳しい指摘10件＋軽微な指摘5件
        │
事務局（コード）─ 引用の実在・副特性の名称と親子関係を検査し、基礎スコアを計算
        │
層2 特性別の判定役（任意）─ 分類の確認・同じ問題のグループ化・漏れの確認
        │
オーケストレーター（議長）：統合 ─ 知識ベースで重み付けし、最終レビューと判断の記録を出す
```

## ライセンス

[MIT License](LICENSE) です。ただし、次のものは MIT License の対象外で、権利はそれぞれの権利者にあります。

- ASDoQ「システム開発文書品質モデル Ver. 2.0a」に由来する品質特性・品質副特性・測定項目の名称と説明（`prompts/common/reviewer_base.md`、`runner/asdoq.py` などに含まれる部分）：システム開発文書品質研究会（ASDoQ）
- [docs/reference/building_effective_agents_要点.md](docs/reference/building_effective_agents_要点.md) が要約している記事の内容：Anthropic

## はじめに読む順番

1. この README
2. [docs/design.md](docs/design.md)（全体の設計）
3. [docs/lessons_learned.md](docs/lessons_learned.md)（試してわかったこと）
4. 実際に動かすなら [runner/README.md](runner/README.md)（APIキーあり）または [docs/run_with_claude_code.md](docs/run_with_claude_code.md)（APIキーなし）
