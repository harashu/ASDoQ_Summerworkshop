"""レビュー結果を正解（answer_key.json）と照合して、指標を出す。

照合ルールは tests/README.md を参照。APIは呼ばない。

使い方:
  python runner/evaluate.py --run runs/case01_srs_20260926_120000 --case tests/cases/case01_srs
"""

import argparse
import json
import re
import sys
from pathlib import Path

from asdoq import CHARACTERISTICS

REPORTED = ("採用", "将来の改善候補")


def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def norm(s):
    return re.sub(r"\s+", "", s or "").strip("「」『』\"“”")


def text_match(quote, target):
    q, t = norm(quote), norm(target)
    if not q or not t:
        return False
    return t in q or (q in t and len(q) >= 0.5 * len(t))


def matches_defect(f, d):
    """指摘 f が欠陥 d を検出しているか（照合ルールは tests/README.md）。

    1. 引用が一致し、かつ中身も合っている（副特性が許容範囲内、または evidence_any の語を含む）
    2. 引用が違っても、指摘文に mention_any の具体的な語を含む
    """
    text = f"{f.get('issue') or ''} {f.get('raw_comment') or ''}"
    if any(text_match(f.get("quote"), t) for t in d.get("match_texts", [])):
        if classified_ok(f.get("sub_characteristic"), d) or any(k in text for k in d.get("evidence_any", [])):
            return True
    return any(k in text for k in d.get("mention_any", []))


def candidates(sub):
    return [s.strip() for s in re.split(r"[／/]", sub or "") if s.strip()]


def classified_ok(sub, defect):
    return any(c in defect["accepted_sub_characteristics"] for c in candidates(sub))


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return round(cov / (va * vb), 3) if va and vb else None


def pct(n, d):
    return f"{n}/{d}（{n / d:.0%}）" if d else "―"


def main():
    ap = argparse.ArgumentParser(description="レビュー結果を正解と照合する")
    ap.add_argument("--run", required=True, help="review.py の出力フォルダ")
    ap.add_argument("--case", required=True, help="answer_key.json があるフォルダ")
    args = ap.parse_args()

    run_dir, case_dir = Path(args.run), Path(args.case)
    key = load(case_dir / "answer_key.json")
    defects, decoys = key["defects"], key["decoys"]
    findings = load(run_dir / "findings.json", [])
    trace = load(run_dir / "trace.json", {})
    by_id = {f["finding_id"]: f for f in findings}

    # --- ペルソナ段階（統合前） -------------------------------------------
    finding_hits = {f["finding_id"]: [d["id"] for d in defects if matches_defect(f, d)] for f in findings}
    detected, classified, detected_by = set(), set(), {}
    for f in findings:
        for did in finding_hits[f["finding_id"]]:
            detected.add(did)
            detected_by.setdefault(did, set()).add(f["persona_id"])
            if classified_ok(f["sub_characteristic"], next(d for d in defects if d["id"] == did)):
                classified.add(did)
    decoy_hits = [
        {"finding_id": f["finding_id"], "decoy": dc["id"]}
        for f in findings if not finding_hits[f["finding_id"]]
        for dc in decoys if text_match(f.get("quote"), dc["match_text"])
    ]
    unmatched_findings = [f["finding_id"] for f in findings if not finding_hits[f["finding_id"]]]
    personas = sorted({f["persona_id"] for f in findings})
    per_persona = {
        pid: {
            "findings": sum(1 for f in findings if f["persona_id"] == pid),
            "detected": sorted({d for f in findings if f["persona_id"] == pid for d in finding_hits[f["finding_id"]]}),
            "unique": sorted(did for did, ps in detected_by.items() if ps == {pid}),
        }
        for pid in personas
    }

    # --- 最終レビュー段階（統合後） ---------------------------------------
    items = [it for it in trace.get("items", []) if it.get("status") in REPORTED]
    item_hits = {}
    for it in items:
        hits = {d for s in it.get("source_findings", []) if s in by_id for d in finding_hits[s]}
        item_hits[it.get("review_id")] = sorted(hits)
    final_detected, final_classified, final_score, knowledge_ok = set(), set(), {}, set()
    for it in items:
        for did in item_hits[it.get("review_id")]:
            d = next(x for x in defects if x["id"] == did)
            final_detected.add(did)
            if classified_ok(it.get("sub_characteristic"), d):
                final_classified.add(did)
            score = it.get("score") or {}
            try:
                final_score[did] = max(final_score.get(did, 0), float(score.get("final") or 0))
            except (TypeError, ValueError):
                pass
            if set(d.get("knowledge_refs") or []) & set(score.get("knowledge_refs") or []):
                knowledge_ok.add(did)
    final_decoys = [
        {"review_id": it.get("review_id"), "decoy": dc["id"]}
        for it in items if not item_hits[it.get("review_id")]
        for s in it.get("source_findings", []) if s in by_id
        for dc in decoys if text_match(by_id[s].get("quote"), dc["match_text"])
    ]
    final_unmatched = [it.get("review_id") for it in items if not item_hits[it.get("review_id")]]
    sev_pairs = [(final_score[did], next(d for d in defects if d["id"] == did)["expected_severity"]) for did in final_score]
    rho = spearman([p[0] for p in sev_pairs], [p[1] for p in sev_pairs])
    with_knowledge = [d["id"] for d in defects if d.get("knowledge_refs") and d["id"] in final_detected]

    # --- 副特性ごと ---------------------------------------------------------
    by_sub = []
    for ch, spec in CHARACTERISTICS.items():
        for sub in spec["subs"]:
            ids = [d["id"] for d in defects if d["sub_characteristic"] == sub]
            by_sub.append({"characteristic": ch, "sub_characteristic": sub, "defects": len(ids),
                           "persona_detected": sum(1 for i in ids if i in detected),
                           "final_detected": sum(1 for i in ids if i in final_detected)})

    n = len(defects)
    result = {
        "persona_stage": {
            "findings": len(findings), "detected": len(detected), "detection_rate": round(len(detected) / n, 3),
            "classification_accuracy": round(len(classified) / len(detected), 3) if detected else None,
            "decoy_hits": decoy_hits, "unmatched_findings": unmatched_findings, "per_persona": per_persona,
        },
        "final_stage": {
            "reported_items": len(items), "detected": len(final_detected), "detection_rate": round(len(final_detected) / n, 3),
            "classification_accuracy": round(len(final_classified) / len(final_detected), 3) if final_detected else None,
            "severity_spearman": rho,
            "knowledge_reflection": f"{len(knowledge_ok)}/{len(with_knowledge)}",
            "decoy_hits": final_decoys, "unmatched_items": final_unmatched,
        },
        "missed_defects": [d["id"] for d in defects if d["id"] not in final_detected],
        "by_sub_characteristic": by_sub,
    }
    (run_dir / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Markdown レポート --------------------------------------------------
    lines = [
        f"# 評価結果：{run_dir.name}",
        "",
        "## 全体",
        "",
        "| 指標 | ペルソナ段階（統合前） | 最終レビュー（統合後） |",
        "|---|---|---|",
        f"| 指摘数 | {len(findings)} | {len(items)} |",
        f"| 検出率 | {pct(len(detected), n)} | {pct(len(final_detected), n)} |",
        f"| 分類正解率 | {pct(len(classified), len(detected))} | {pct(len(final_classified), len(final_detected))} |",
        f"| 誤検出（正しい記述への指摘） | {len(decoy_hits)} | {len(final_decoys)} |",
        f"| 対応なし（要目視確認） | {len(unmatched_findings)} | {len(final_unmatched)} |",
        f"| 重要度の順位相関 | ― | {rho if rho is not None else '―'} |",
        f"| 知識の反映率 | ― | {pct(len(knowledge_ok), len(with_knowledge))} |",
        "",
        "## 副特性ごとの検出",
        "",
        "| 品質特性 | 副特性 | 欠陥数 | ペルソナ段階 | 最終 |",
        "|---|---|:-:|:-:|:-:|",
    ]
    lines += [f"| {r['characteristic']} | {r['sub_characteristic']} | {r['defects']} | {r['persona_detected']} | {r['final_detected']} |" for r in by_sub]
    lines += ["", "## ペルソナごと", "", "| ペルソナ | 指摘数 | 検出した欠陥 | 独自に検出した欠陥 |", "|---|:-:|---|---|"]
    lines += [f"| {pid} | {v['findings']} | {len(v['detected'])} | {', '.join(v['unique']) or '―'} |" for pid, v in per_persona.items()]
    missed = [d for d in defects if d["id"] not in final_detected]
    lines += ["", f"## 最終レビューで見逃した欠陥（{len(missed)}件）", ""]
    lines += [f"- {d['id']}（{d['sub_characteristic']}）{d['description']}" + ("　※ペルソナ段階では検出" if d["id"] in detected else "") for d in missed]
    lines += ["", "## 目視確認が必要な項目", "", "どの欠陥にも対応しなかった最終レビューの項目です。仕込んでいない本当の問題か、誤検出かを確認してください。", ""]
    lines += [f"- {rid}" for rid in final_unmatched] or ["- なし"]
    (run_dir / "evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"検出率  ペルソナ段階 {pct(len(detected), n)} ／ 最終 {pct(len(final_detected), n)}")
    print(f"分類正解率（最終） {pct(len(final_classified), len(final_detected))}")
    print(f"誤検出（最終） {len(final_decoys)} 件、要目視確認 {len(final_unmatched)} 件")
    print(f"詳細: {run_dir / 'evaluation.md'}")


if __name__ == "__main__":
    # Windows でリダイレクトしたときに表示できない文字があっても止まらないようにする
    sys.stdout.reconfigure(errors="replace")
    main()
