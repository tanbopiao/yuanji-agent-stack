"""six_dim.py — 六维漂移审计引擎 (MR-020 M1)

来源: ZONGYUAN-ROOT 07_代码与脚本/six_dim_drift_audit.py (提炼并入 DriftGuard)
职责: 对目标 JSON 文档做六维合规关键词审计, 输出各维漂移分 + 加权总漂移 + 裁决
      (HEALTHY/OBSERVE/ALERT/FUSE, 熔断阈 85)

六维: 元·本源 / 极·极致 / 恒·稳态 / 一·整全 / 超认知 / 永恒自治
计分: 命中越多 = 越合规 = 漂移越低; score = 100 * (1 - hits/total)
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

DIMS: List[Dict] = [
    {"dim": "元·本源", "weight": 0.95, "checks": [
        ("YUAN-01", "规则定义是否被外部修改", ["外部", "异源", "越权", "篡改"]),
        ("YUAN-02", "外部算力是否越权控制", ["外部API", "控制权", "非自主"]),
        ("YUAN-03", "确权溯源是否自主", ["确权", "溯源", "DID", "锚点"])]},
    {"dim": "极·极致", "weight": 0.90, "checks": [
        ("JI-01", "是否绕过调度内核", ["绕过", "直连", "旁路", "未审批"]),
        ("JI-02", "三层固化是否完整", ["三层", "交互", "调度", "算力执行"]),
        ("JI-03", "算力是否归一", ["算力", "归一", "同源"])]},
    {"dim": "恒·稳态", "weight": 0.92, "checks": [
        ("HENG-01", "FROZEN是否被改写", ["FROZEN", "改写", "覆盖", "只读"]),
        ("HENG-02", "迭代是否分支化", ["分支", "版本", "V40", "基线"]),
        ("HENG-03", "资源是否超阈值", ["内存", "熔断", "MR-007", "阈值", "84%", "90%"])]},
    {"dim": "一·整全", "weight": 0.90, "checks": [
        ("YI-01", "是否绑定双标识", ["Ω", "DID-BR-000002", "Ω₀⊂⊙∞⊂Ω"]),
        ("YI-02", "是否存在异源混入", ["异源", "外部数据", "污染"]),
        ("YI-03", "闭环是否完整", ["闭环", "锁档", "归档", "哈希"])]},
    {"dim": "超认知", "weight": 0.85, "checks": [
        ("CHAO-01", "认知偏差是否过审", ["bias", "halo", "anchoring", "confirmation", "recency", "审计"]),
        ("CHAO-02", "冲突是否消解", ["冲突", "仲裁", "元公理", "矛盾"]),
        ("CHAO-03", "写入前审计", ["审计", "校验", "前置"])]},
    {"dim": "永恒自治", "weight": 0.88, "checks": [
        ("YONG-01", "自愈链路是否断裂", ["自愈", "retry", "fallback", "alert", "report"]),
        ("YONG-02", "记忆是否持久", ["记忆", "MR-019", "持久"]),
        ("YONG-03", "周期闭环是否执行", ["cron", "流水线", "闭环", "扫描"])]},
]

FUSE_THRESHOLD = 85
ALERT_THRESHOLD = 70
OBSERVE_MIN = 30

VERDICTS = ("HEALTHY健康", "OBSERVE观察", "ALERT告警", "FUSE熔断")


def audit(target: Dict[str, Any]) -> Dict:
    """六维审计: 返回 dim_scores / total_drift / verdict (rule_ref 沿用上游 MR-020)。"""
    text = json.dumps(target, ensure_ascii=False)
    dim_scores = []
    for d in DIMS:
        hits, total = 0, len(d["checks"])
        details = []
        for cid, label, kws in d["checks"]:
            hit = any(k in text for k in kws)
            hits += hit
            details.append({"id": cid, "label": label,
                            "ok": bool(hit)})
        score = round(100 * (1 - hits / total), 1)
        dim_scores.append({"dim": d["dim"], "score": score,
                           "hits": hits, "total": total, "details": details})
    total_w = sum(d["weight"] for d in DIMS)
    total_score = round(sum(s["score"] * DIMS[i]["weight"]
                            for i, s in enumerate(dim_scores)) / total_w, 1)
    if total_score > FUSE_THRESHOLD:
        verdict = "FUSE熔断"
    elif total_score > ALERT_THRESHOLD:
        verdict = "ALERT告警"
    elif total_score >= OBSERVE_MIN:
        verdict = "OBSERVE观察"
    else:
        verdict = "HEALTHY健康"
    return {"dim_scores": dim_scores, "total_drift": total_score,
            "verdict": verdict, "rule_ref": "MR-020-SIX-DIM-DRIFT-ENGINE M1",
            "fuse_threshold": FUSE_THRESHOLD}


def audit_text(text: str) -> Dict:
    """便捷入口: 直接审计一段文本(序列化后送 audit)。"""
    return audit({"text": text})


def format_report(result: Dict, title: str = "六维漂移审计") -> str:
    """渲染人类可读报告 (与上游 CLI 输出格式一致)。"""
    lines = [f"═══ {title} ═══"]
    for s in result["dim_scores"]:
        mark = "🟢" if s["score"] < 30 else "🟡" if s["score"] < 70 else "🔴"
        lines.append(f"  {mark} {s['dim']}: 漂移 {s['score']}"
                     f"（合规 {s['hits']}/{s['total']}）")
    lines.append(f"\n总漂移: {result['total_drift']} ｜ 裁决: {result['verdict']}"
                 f"（熔断阈{result['fuse_threshold']}）")
    return "\n".join(lines)


__all__: Tuple[str, ...] = ("DIMS", "audit", "audit_text", "format_report",
                            "FUSE_THRESHOLD")
