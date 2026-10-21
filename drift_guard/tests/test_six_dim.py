import importlib.util
import os
import unittest

# six_dim 是零依赖模块; 直接按路径加载, 避免触发 drift_guard/__init__
# 的 yaml 依赖链 (与上游 middleware 无关, 独立测试)
_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "six_dim", os.path.join(_HERE, "..", "six_dim.py"))
six_dim = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(six_dim)

audit = six_dim.audit
audit_text = six_dim.audit_text
format_report = six_dim.format_report
DIMS = six_dim.DIMS


class TestSixDim(unittest.TestCase):
    def test_compliant_doc_low_drift(self):
        doc = {
            "identity": "Ω₀⊂⊙∞⊂Ω DID-BR-000002",
            "governance": "锚点确权溯源, 三层固化, 算力归一同源",
            "frozen": "FROZEN 只读, 基线分支, 熔断阈值",
            "audit": "冲突仲裁元公理, 写入前置审计",
            "ops": "自愈 retry fallback, 记忆持久, cron 闭环扫描",
        }
        r = audit(doc)
        self.assertLess(r["total_drift"], 30)
        self.assertEqual(r["verdict"], "HEALTHY健康")

    def test_empty_doc_fuse(self):
        r = audit({})
        self.assertEqual(r["verdict"], "FUSE熔断")
        self.assertEqual(r["fuse_threshold"], 85)

    def test_dim_structure(self):
        r = audit_text("锚点 闭环")
        self.assertEqual(len(r["dim_scores"]), len(DIMS))
        for s in r["dim_scores"]:
            self.assertEqual(s["total"], 3)
            self.assertTrue(0 <= s["score"] <= 100)

    def test_rule_ref_carried(self):
        r = audit({})
        self.assertEqual(r["rule_ref"], "MR-020-SIX-DIM-DRIFT-ENGINE M1")

    def test_format_report(self):
        out = format_report(audit({}))
        self.assertIn("熔断", out)
        self.assertIn("总漂移", out)


if __name__ == "__main__":
    unittest.main()
