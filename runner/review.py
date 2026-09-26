"""ASDoQ マルチペルソナ文書レビュー：実行スクリプト。

docs/design.md のワークフローを、フレームワークを使わずに Claude API を直接呼んで実行する。

  [1] パネル選定（コードの標準パネル表。表にない文書種別だけLLM）
  [2] 層1 ペルソナの並列レビュー（LLM）
  [3] 事務局：検証・基礎スコア計算・特性ごとの仕分け（コード）
  [4] 層2 特性別の判定役（任意・LLM。--judges を付けたときだけ）
  [5] オーケストレーターによる統合（LLM）＋ 判断の記録の再計算チェック（コード）

使い方:
  python runner/review.py --case tests/cases/case01_srs --dry-run
  python runner/review.py --case tests/cases/case01_srs
  python runner/review.py --case tests/cases/case01_srs --judges --with-c
"""

import argparse
import asyncio
import datetime
import json
import re
import sys
from pathlib import Path

import yaml

from asdoq import CHARACTERISTICS, SUB_TO_PARENT, characteristic_spec, normalize_sub, split_candidates

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
KNOWLEDGE = ROOT / "knowledge"

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# 文書種別ごとの標準パネル（docs/design.md 3章）。(基本メンバー, C群の候補)
_PANEL_SRS = (["A11", "A05", "A01", "A07", "B08"], "C04")
_PANEL_DESIGN = (["A11", "A05", "A07", "B03", "B08"], "C06")
_PANEL_MANUAL = (["A11", "A06", "A08", "B04"], "C07")
_PANEL_MINUTES = (["A11", "A07", "B02"], "C09")
_PANEL_PROPOSAL = (["A11", "B02", "A02", "B01"], "C04")
_PANEL_REPORT = (["A11", "A02", "B05", "B03"], "C01")
STANDARD_PANELS = {
    "要求仕様書": _PANEL_SRS,
    "設計書": _PANEL_DESIGN,
    "手順書": _PANEL_MANUAL,
    "取扱説明書": _PANEL_MANUAL,
    "議事録": _PANEL_MINUTES,
    "メール": _PANEL_MINUTES,
    "企画書": _PANEL_PROPOSAL,
    "提案書": _PANEL_PROPOSAL,
    "報告書": _PANEL_REPORT,
}
# meta.json のフラグ → 追加するペルソナ
META_ADDITIONS = [
    ("external", "A03", "社外に出る文書のため"),
    ("overseas", "A09", "海外拠点・海外顧客に渡る文書のため"),
    ("audit_target", "A04", "認証・監査の対象のため"),
    ("knowledge_base", "A10", "ナレッジベースに登録する文書のため"),
]
# 判定役が追加した指摘の基礎スコア計算に使う値（docs/design.md 5章）
JUDGE_META = {"id": "J", "name": "特性別の判定役", "base_reliability": 0.9, "severity_bias": 0, "focus": None}

# Claude Opus 5 の料金（USD / 100万トークン）。概算表示用
PRICE = {"claude-opus-5": {"input": 5.0, "output": 25.0}}


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------

def read(path):
    return Path(path).read_text(encoding="utf-8")


def load_personas():
    personas = {}
    for path in sorted((PROMPTS / "personas").glob("*.md")):
        m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n(.*)$", read(path), re.S)
        if not m:
            raise ValueError(f"フロントマターが読めません: {path}")
        meta = yaml.safe_load(m.group(1))
        personas[meta["id"]] = {"meta": meta, "body": m.group(2).strip()}
    return personas


def section(text, start, end):
    s = text.index(start)
    return text[s:text.index(end, s + len(start))].strip()


def load_knowledge():
    texts, ids = [], []
    for name in ("company_template.md", "domain_knowhow.md", "customer_profile.md"):
        path = KNOWLEDGE / name
        if path.exists():
            text = read(path)
            texts.append(text)
            ids += re.findall(r"^### (K-[A-Z]+-\d+)", text, re.M)
    return "\n\n".join(texts), set(ids)


def extract_tag(text, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else None


def parse_json(text):
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(s)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# LLM 呼び出し
# ---------------------------------------------------------------------------

class LLM:
    def __init__(self, model, effort, use_fallback, concurrency):
        import anthropic  # --dry-run では読み込まない

        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.effort = effort
        self.use_fallback = use_fallback
        self.sem = asyncio.Semaphore(concurrency)
        self.usage = []

    async def call(self, label, system, user, max_tokens=32000):
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        async with self.sem:
            print(f"  → {label} を実行中…", flush=True)
            if self.use_fallback:
                try:
                    stream_manager = self.client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
                except TypeError as e:
                    raise RuntimeError("このSDKは fallbacks に未対応です。pip install -U anthropic で更新するか、--no-fallback を付けてください") from e
            else:
                stream_manager = self.client.messages.stream(**kwargs)
            async with stream_manager as stream:
                msg = await stream.get_final_message()

        u = msg.usage
        self.usage.append({
            "label": label,
            "model": msg.model,
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "stop_reason": msg.stop_reason,
        })
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"{label}: 応答が拒否されました（{getattr(msg, 'stop_details', None)}）")
        if msg.stop_reason == "max_tokens":
            print(f"  ! {label}: 出力が max_tokens で打ち切られました", flush=True)
        print(f"  ○ {label} 完了", flush=True)
        return "".join(b.text for b in msg.content if b.type == "text")


# ---------------------------------------------------------------------------
# [1] パネル選定
# ---------------------------------------------------------------------------

def select_panel_by_table(meta, with_c):
    doc_type = meta.get("document_type", "")
    if doc_type not in STANDARD_PANELS:
        return None
    base, c_member = STANDARD_PANELS[doc_type]
    panel = [{"persona_id": pid, "reason": "必須（較正の基準線）" if pid == "A11" else f"{doc_type}の標準パネル"} for pid in base]
    for flag, pid, reason in META_ADDITIONS:
        if meta.get(flag) and pid not in base:
            panel.append({"persona_id": pid, "reason": reason})
    if with_c:
        panel.append({"persona_id": c_member, "reason": "C群（困った人型）の候補"})
    return panel


async def select_panel_by_llm(llm, meta, document, personas):
    catalog = [
        {"persona_id": pid, "name": p["meta"]["name"], "group": p["meta"]["group"],
         "sub_focus": p["meta"].get("sub_focus"), "hidden_value": p["meta"].get("hidden_value")}
        for pid, p in personas.items()
    ]
    outline = "\n".join(line for line in document.splitlines() if line.startswith("#"))
    standard = {k: {"panel": v[0], "c_candidate": v[1]} for k, v in STANDARD_PANELS.items()}
    user = (
        f"<document_meta>\n{dumps(meta)}\n</document_meta>\n"
        f"<document_outline>\n{outline}\n</document_outline>\n"
        f"<persona_catalog>\n{dumps(catalog)}\n</persona_catalog>\n"
        f"<standard_panels>\n{dumps(standard)}\n</standard_panels>"
    )
    text = await llm.call("パネル選定", read(PROMPTS / "orchestrator" / "01_panel_selection.md"), user, max_tokens=8000)
    result = parse_json(extract_tag(text, "result") or "")
    panel = [p for p in result["panel"] if p["persona_id"] in personas]
    if not any(p["persona_id"] == "A11" for p in panel):
        panel.insert(0, {"persona_id": "A11", "reason": "必須（較正の基準線）"})
    return panel, text


# ---------------------------------------------------------------------------
# [2] ペルソナレビュー と [3] 検証・スコア計算
# ---------------------------------------------------------------------------

def normalize_text(s):
    return re.sub(r"\s+", "", s or "")


def quote_in_document(quote, doc_norm):
    q = (quote or "").strip().strip("「」『』\"“”")
    parts = [normalize_text(p) for p in re.split(r"…+|\.\.\.+", q)]
    parts = [p for p in parts if p]
    return bool(parts) and all(p in doc_norm for p in parts)


def check_finding(f, doc_norm, allow_empty_quote=True):
    """指摘1件を検証して正規化する。(正規化した指摘, エラー種別, 理由) を返す。"""
    subs = split_candidates(f.get("sub_characteristic"))
    if subs is None:
        return None, "enum", f"副特性が公式名称ではありません: {f.get('sub_characteristic')}"
    ch = f.get("characteristic")
    auto_fixed = False
    if ch not in {SUB_TO_PARENT[s] for s in subs}:
        ch, auto_fixed = SUB_TO_PARENT[subs[0]], True

    quote = (f.get("quote") or "").strip()
    if quote:
        if not quote_in_document(quote, doc_norm):
            return None, "quote", f"引用が文書中に見つかりません: {quote}"
    elif not allow_empty_quote:
        return None, "quote", "引用が空です"

    try:
        severity = min(5, max(1, int(round(float(f.get("severity", 3))))))
        confidence = min(1.0, max(0.0, float(f.get("confidence", 0.5))))
    except (TypeError, ValueError):
        severity, confidence = 3, 0.5

    return {
        "characteristic": ch,
        "sub_characteristic": "／".join(subs),
        "candidates": subs,
        "measurement_item": f.get("measurement_item"),
        "location": f.get("location"),
        "quote": quote,
        "raw_comment": f.get("raw_comment"),
        "issue": f.get("issue"),
        "suggestion": f.get("suggestion"),
        "severity": severity,
        "confidence": confidence,
        "auto_fixed": auto_fixed,
    }, None, None


def base_score(f, pmeta):
    adjusted = min(5, max(1, f["severity"] - float(pmeta.get("severity_bias") or 0)))
    focus = pmeta.get("focus")
    factor = 1.0 if focus is None or focus.get(f["characteristic"], 0) >= 0.2 else 0.7
    return round(adjusted * float(pmeta.get("base_reliability", 0.8)) * factor * f["confidence"], 2)


def validate_persona_output(pid, pmeta, text, doc_norm):
    """ペルソナの出力を検証する。(findings, good_points, excluded, retry_needed) を返す。"""
    try:
        data = parse_json(extract_tag(text, "result") or "")
    except (json.JSONDecodeError, TypeError):
        return [], [], [{"persona_id": pid, "reason": "JSONとして読み込めません"}], True

    findings, excluded, retry_needed = [], [], False
    for i, raw in enumerate(data.get("findings", []), start=1):
        f, err, reason = check_finding(raw, doc_norm)
        if err:
            excluded.append({"finding_id": f"{pid}-{i}", "persona_id": pid, "error": err, "reason": reason, "original": raw})
            retry_needed = retry_needed or err == "enum"
            continue
        f.update({"finding_id": f"{pid}-{i}", "persona_id": pid, "persona_name": pmeta["name"], "is_minor": False})
        f["base_score"] = base_score(f, pmeta)
        findings.append(f)
    # 件数上限を超えた分の軽微な指摘。形式エラーでも再実行はせず、除外だけする
    for i, raw in enumerate(data.get("minor_findings", []), start=1):
        f, err, reason = check_finding(raw, doc_norm)
        if err:
            excluded.append({"finding_id": f"{pid}-m{i}", "persona_id": pid, "error": err, "reason": reason, "original": raw})
            continue
        f.update({"finding_id": f"{pid}-m{i}", "persona_id": pid, "persona_name": pmeta["name"], "is_minor": True})
        f["base_score"] = base_score(f, pmeta)
        findings.append(f)
    good_points = [dict(g, persona_id=pid) for g in data.get("good_points", [])]
    return findings, good_points, excluded, retry_needed


def persona_user_message(meta, document):
    doc_meta = {k: meta[k] for k in ("document_type", "purpose", "intended_readers") if k in meta}
    return (
        f"<document_meta>\n{dumps(doc_meta)}\n</document_meta>\n"
        f"<document>\n{document}\n</document>\n\n"
        "上記の文書を、あなたのペルソナとしてレビューしてください。"
    )


async def run_persona(llm, pid, persona, base_prompt, user, doc_norm, raw_dir):
    system = [
        {"type": "text", "text": base_prompt, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": persona["body"]},
    ]
    pmeta = persona["meta"]
    for attempt in (1, 2):
        text = await llm.call(f"{pid} {pmeta['name']}", system, user)
        (raw_dir / f"{pid}_try{attempt}.txt").write_text(text, encoding="utf-8")
        findings, good_points, excluded, retry_needed = validate_persona_output(pid, pmeta, text, doc_norm)
        if not retry_needed or attempt == 2:
            return findings, good_points, excluded
        print(f"  ! {pid}: 形式エラーのため1回だけ再実行します", flush=True)


# ---------------------------------------------------------------------------
# [4] 層2 特性別の判定役（任意）
# ---------------------------------------------------------------------------

def parse_judged_sub(value):
    """「理解容易性／関係性」のように特性名が付いていても副特性だけを取り出す。"""
    if not value:
        return None
    tokens = [t for t in re.split(r"[／/]", value) if t.strip() and t.strip() not in CHARACTERISTICS]
    return split_candidates("／".join(tokens)) if tokens else None


async def run_judges(llm, findings, document, doc_norm, boundary_rules, raw_dir):
    template = read(PROMPTS / "judges" / "characteristic_judge.md").split("\n---\n", 1)[1].strip()
    groups = {ch: [] for ch in CHARACTERISTICS}
    for f in findings:
        for ch in {SUB_TO_PARENT[s] for s in f["candidates"]}:
            groups[ch].append(f)

    async def judge(ch):
        items = [{k: f[k] for k in ("finding_id", "characteristic", "sub_characteristic", "measurement_item", "location", "quote", "issue")} for f in groups[ch]]
        user = (
            f"<characteristic_spec>\n{characteristic_spec(ch)}\n</characteristic_spec>\n"
            f"<boundary_rules>\n{boundary_rules}\n</boundary_rules>\n"
            f"<document>\n{document}\n</document>\n"
            f"<findings>\n{dumps(items)}\n</findings>"
        )
        text = await llm.call(f"判定役 {ch}", template.replace("{{特性名}}", ch), user, max_tokens=16000)
        (raw_dir / f"judge_{ch}.txt").write_text(text, encoding="utf-8")
        try:
            return ch, parse_json(extract_tag(text, "result") or "")
        except (json.JSONDecodeError, TypeError):
            return ch, {"error": "JSONとして読み込めません"}

    results = dict(await asyncio.gather(*(judge(ch) for ch in CHARACTERISTICS)))

    by_id = {f["finding_id"]: f for f in findings}
    additions, excluded = [], []
    for ch, result in results.items():
        for check in result.get("checks", []):
            f = by_id.get(check.get("finding_id"))
            if not f:
                continue
            f.setdefault("judgments", []).append(dict(check, judge=ch))
            if check.get("verdict") in ("再分類", "移管"):
                subs = parse_judged_sub(check.get("sub_characteristic"))
                if subs:
                    f.update({"sub_characteristic": "／".join(subs), "candidates": subs, "characteristic": SUB_TO_PARENT[subs[0]]})
        for i, raw in enumerate(result.get("additions", []), start=1):
            raw = dict(raw, characteristic=ch)
            f, err, reason = check_finding(raw, doc_norm, allow_empty_quote=False)
            fid = f"J-{ch}-{i}"
            if err:
                excluded.append({"finding_id": fid, "persona_id": "J", "error": err, "reason": reason, "original": raw})
                continue
            f.update({"finding_id": fid, "persona_id": "J", "persona_name": JUDGE_META["name"]})
            f["base_score"] = base_score(f, JUDGE_META)
            additions.append(f)
    return results, additions, excluded


# ---------------------------------------------------------------------------
# [5] 統合 と 判断の記録の再計算チェック
# ---------------------------------------------------------------------------

def verify_trace(trace, findings_by_id, knowledge_ids):
    problems, checked = [], 0
    for item in trace.get("items", []):
        rid = item.get("review_id", "?")
        sources = item.get("source_findings") or []
        unknown = [s for s in sources if s not in findings_by_id]
        if unknown:
            problems.append(f"{rid}: 存在しない指摘IDを参照しています {unknown}")
        known = [findings_by_id[s] for s in sources if s in findings_by_id]
        score = item.get("score")
        if not known or not score:
            continue
        checked += 1
        exp_base = max(f["base_score"] for f in known)
        exp_bonus = min(1.5, 0.5 * (len({f["persona_id"] for f in known}) - 1))
        try:
            adj = float(score.get("knowledge_adjust") or 0)
            got_base = float(score.get("base_max") or 0)
            got_bonus = float(score.get("agreement_bonus") or 0)
            got_final = float(score.get("final") or 0)
        except (TypeError, ValueError):
            problems.append(f"{rid}: スコアが数値ではありません")
            continue
        refs = score.get("knowledge_refs") or []
        if abs(got_base - exp_base) > 0.05:
            problems.append(f"{rid}: base_max が {got_base}（再計算値 {exp_base}）")
        if abs(got_bonus - exp_bonus) > 0.01:
            problems.append(f"{rid}: agreement_bonus が {got_bonus}（再計算値 {exp_bonus}）")
        if not -2.0 <= adj <= 2.0:
            problems.append(f"{rid}: knowledge_adjust {adj} が範囲外")
        if adj != 0 and not refs:
            problems.append(f"{rid}: 知識補正 {adj} に知識IDの引用がありません")
        bad_refs = [r for r in refs if r not in knowledge_ids]
        if bad_refs:
            problems.append(f"{rid}: 存在しない知識IDを引用しています {bad_refs}")
        expected_final = round(exp_base + exp_bonus + adj, 2)
        if abs(got_final - expected_final) > 0.05:
            problems.append(f"{rid}: final が {got_final}（再計算値 {expected_final}）")
    return {"items_checked": checked, "problems": problems}


def integration_user_message(asdoq_model, meta, document, knowledge, panel_info, findings, good_points, excluded, judgments):
    finding_fields = ("finding_id", "persona_id", "persona_name", "characteristic", "sub_characteristic", "measurement_item",
                      "location", "quote", "raw_comment", "issue", "suggestion", "severity", "confidence", "base_score", "is_minor", "judgments")
    slim = [{k: f[k] for k in finding_fields if k in f} for f in findings]
    parts = [
        f"<asdoq_model>\n{asdoq_model}\n</asdoq_model>",
        f"<document_meta>\n{dumps(meta)}\n</document_meta>",
        f"<document>\n{document}\n</document>",
        f"<knowledge>\n{knowledge}\n</knowledge>",
        f"<panel>\n{dumps(panel_info)}\n</panel>",
        f"<findings>\n{dumps(slim)}\n</findings>",
        f"<good_points>\n{dumps(good_points)}\n</good_points>",
        f"<excluded>\n{dumps([{k: e[k] for k in ('finding_id', 'reason')} for e in excluded if 'finding_id' in e])}\n</excluded>",
    ]
    if judgments:
        parts.append(f"<judgments>\n{dumps(judgments)}\n</judgments>")
    parts.append("作業手順に従って、<plan>、<final_review>、<trace> の順に出力してください。")
    return "\n".join(parts)


def summarize_usage(usage, model):
    total = {k: sum(u[k] for u in usage) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    price = PRICE.get(model)
    if price:
        cost = (total["input_tokens"] * price["input"] + total["cache_creation_input_tokens"] * price["input"] * 1.25
                + total["cache_read_input_tokens"] * price["input"] * 0.1 + total["output_tokens"] * price["output"]) / 1_000_000
        total["estimated_cost_usd"] = round(cost, 3)
    return total


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

async def main():
    ap = argparse.ArgumentParser(description="ASDoQ マルチペルソナ文書レビュー")
    ap.add_argument("--case", required=True, help="document.md と meta.json があるフォルダ")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--with-c", action="store_true", help="C群（困った人型）を1人パネルに加える")
    ap.add_argument("--judges", action="store_true", help="層2 特性別の判定役を使う")
    ap.add_argument("--panel", help="パネルを手動で指定する（例: A11,A05,A01）")
    ap.add_argument("--no-fallback", action="store_true", help="拒否時のサーバー側フォールバックを使わない")
    ap.add_argument("--concurrency", type=int, default=4, help="同時に実行するAPI呼び出し数")
    ap.add_argument("--dry-run", action="store_true", help="APIを呼ばずに、パネルとプロンプトだけを出力する")
    args = ap.parse_args()

    case_dir = Path(args.case)
    meta = json.loads(read(case_dir / "meta.json"))
    document = read(case_dir / meta.get("document_file", "document.md"))
    doc_norm = normalize_text(document)
    personas = load_personas()
    base_prompt = read(PROMPTS / "common" / "reviewer_base.md")
    asdoq_model = section(base_prompt, "## 評価の基準", "## レビューのルール")
    boundary_rules = section(base_prompt, "### 紛らわしい副特性の見分け方", "## レビューのルール")
    knowledge, knowledge_ids = load_knowledge()

    run_dir = ROOT / "runs" / f"{case_dir.name}_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    print(f"出力先: {run_dir}")

    # [1] パネル選定
    llm = None if args.dry_run else LLM(args.model, args.effort, not args.no_fallback, args.concurrency)
    if args.panel:
        panel = [{"persona_id": pid.strip(), "reason": "手動指定"} for pid in args.panel.split(",")]
    else:
        panel = select_panel_by_table(meta, args.with_c)
        if panel is None:
            if args.dry_run:
                sys.exit(f"文書種別「{meta.get('document_type')}」は標準パネル表にありません。--panel で指定するか、--dry-run を外してLLMに選ばせてください。")
            panel, text = await select_panel_by_llm(llm, meta, document, personas)
            (raw_dir / "panel_selection.txt").write_text(text, encoding="utf-8")
    unknown = [p["persona_id"] for p in panel if p["persona_id"] not in personas]
    if unknown:
        sys.exit(f"存在しないペルソナIDです: {unknown}")
    (run_dir / "panel.json").write_text(dumps(panel), encoding="utf-8")
    print("パネル: " + "、".join(f"{p['persona_id']} {personas[p['persona_id']]['meta']['name']}" for p in panel))

    user = persona_user_message(meta, document)
    if args.dry_run:
        for p in panel:
            pid = p["persona_id"]
            (raw_dir / f"{pid}_prompt.txt").write_text(f"[system]\n{base_prompt}\n\n{personas[pid]['body']}\n\n[user]\n{user}", encoding="utf-8")
        print(f"--dry-run: APIは呼んでいません。各ペルソナのプロンプトを {raw_dir} に保存しました。")
        return

    # [2] ペルソナの並列レビュー
    print("[2] ペルソナレビュー")
    results = await asyncio.gather(
        *(run_persona(llm, p["persona_id"], personas[p["persona_id"]], base_prompt, user, doc_norm, raw_dir) for p in panel),
        return_exceptions=True,
    )
    findings, good_points, excluded = [], [], []
    for p, result in zip(panel, results):
        if isinstance(result, Exception):
            print(f"  × {p['persona_id']} が失敗しました: {result}")
            excluded.append({"persona_id": p["persona_id"], "reason": f"実行失敗: {result}"})
            continue
        f, g, e = result
        findings += f
        good_points += g
        excluded += e
    print(f"[3] 検証: 指摘 {len(findings)} 件を採用、{len(excluded)} 件を除外")

    # [4] 判定役（任意）
    judgments = None
    if args.judges:
        print("[4] 特性別の判定役")
        judgments, additions, judge_excluded = await run_judges(llm, findings, document, doc_norm, boundary_rules, raw_dir)
        findings += additions
        excluded += judge_excluded
        (run_dir / "judgments.json").write_text(dumps(judgments), encoding="utf-8")
        print(f"  判定役による追加 {len(additions)} 件")

    (run_dir / "findings.json").write_text(dumps(findings), encoding="utf-8")
    (run_dir / "good_points.json").write_text(dumps(good_points), encoding="utf-8")
    (run_dir / "excluded.json").write_text(dumps(excluded), encoding="utf-8")

    # [5] 統合
    print("[5] オーケストレーターによる統合")
    panel_info = []
    for p in panel:
        m = personas[p["persona_id"]]["meta"]
        panel_info.append({
            "persona_id": m["id"], "name": m["name"], "group": m["group"], "focus": m.get("focus"),
            "base_reliability": m.get("base_reliability"), "severity_bias": m.get("severity_bias"),
            "hidden_value": m.get("hidden_value"), "orchestrator_hint": m.get("orchestrator_hint"),
            "selection_reason": p["reason"],
        })
    if args.judges:
        panel_info.append({"persona_id": "J", "name": JUDGE_META["name"], "base_reliability": JUDGE_META["base_reliability"],
                           "severity_bias": 0, "hidden_value": "ASDoQの定義だけに基づく中立な判定", "orchestrator_hint": "追加した指摘は引用の実在を確認済み"})
    integration_user = integration_user_message(asdoq_model, meta, document, knowledge, panel_info, findings, good_points, excluded, judgments)
    text = await llm.call("統合", read(PROMPTS / "orchestrator" / "02_integration.md"), integration_user, max_tokens=64000)
    (raw_dir / "integration.txt").write_text(text, encoding="utf-8")

    plan = extract_tag(text, "plan") or ""
    final_review = extract_tag(text, "final_review") or ""
    (run_dir / "plan.md").write_text(plan, encoding="utf-8")
    (run_dir / "final_review.md").write_text(final_review, encoding="utf-8")
    try:
        trace = parse_json(extract_tag(text, "trace") or "")
        verification = verify_trace(trace, {f["finding_id"]: f for f in findings}, knowledge_ids)
    except (json.JSONDecodeError, TypeError):
        trace, verification = {}, {"items_checked": 0, "problems": ["trace をJSONとして読み込めません（raw/integration.txt を確認）"]}
    (run_dir / "trace.json").write_text(dumps(trace), encoding="utf-8")
    (run_dir / "verification.json").write_text(dumps(verification), encoding="utf-8")

    usage = {"calls": llm.usage, "total": summarize_usage(llm.usage, args.model)}
    (run_dir / "usage.json").write_text(dumps(usage), encoding="utf-8")

    print("\n完了")
    print(f"  最終レビュー: {run_dir / 'final_review.md'}")
    print(f"  判断の記録の再計算チェック: {verification['items_checked']} 件を確認、問題 {len(verification['problems'])} 件")
    for problem in verification["problems"][:10]:
        print(f"    - {problem}")
    total = usage["total"]
    print(f"  トークン: 入力 {total['input_tokens']:,} / 出力 {total['output_tokens']:,} / キャッシュ読込 {total['cache_read_input_tokens']:,}"
          + (f"（概算 ${total['estimated_cost_usd']}）" if "estimated_cost_usd" in total else ""))
    print(f"  正解との照合: python runner/evaluate.py --run {run_dir} --case {case_dir}")


if __name__ == "__main__":
    # Windows でリダイレクトしたときに表示できない文字があっても止まらないようにする
    sys.stdout.reconfigure(errors="replace")
    asyncio.run(main())
