# YUANJI Agent Stack

> **Give your AI agent unbreakable rules, a memory that never leaks, and an anchor that never drifts.**
>
> 为自治 AI 智能体提供：不可篡改的行为规则 + 永不出域的记忆共享 + 意图漂移防控。
>
> Ω₀⊂⊙∞⊂Ω | 从 ZONGYUAN-ROOT 治理内核提炼的双引擎开源栈

[English](#english) | [中文](#中文)

---

## English

Long-running autonomous agents fail in three predictable ways: they **rewrite their own constraints**, **leak raw data** when sharing knowledge, and **drift from their goals** over long multi-step runs. YUANJI Agent Stack addresses all three with two orthogonal engines:

| Engine | What it does | Analogy |
|---|---|---|
| **AxiomKit** ([`axiomkit/`](axiomkit)) | Formally verified invariants (Z3 SMT) + federated semantic memory + guarded recursive self-improvement | CPU microcode + ECC shared cache |
| **DriftGuard** ([`drift_guard/`](drift_guard)) | Four-layer threshold defense + three-level circuit breaker against intent drift, loops, resource overflow, context entropy + six-dimension governance drift audit (MR-020) | Watchdog timer + breaker panel |

### Highlights

- **Append-only, hash-locked rule set** — the agent can propose rules but never approve its own invariants (synthesis → Z3 verification → arbitration)
- **Federated memory (SFMP)** — cross-tenant knowledge circulates only as purified A–D graded semantic fragments; raw data never leaves its domain
- **Guarded RSI** — self-improvement runs as shadow trials with grayscale promotion/rollback; the axiom core stays frozen, every step hash-chained
- **Zero-intrusion middleware** — DriftGuard hooks `before_step() / after_tool() / report_context()` without touching your agent code; embedding backend degrades gracefully (BGE-m3 → n-gram) so the defense chain never breaks
- **Measured on our own harness**: max interception effectiveness 99.17%, FP 0.83%, FN 0.83% (32/32 cases); 5 Z3-verified invariants; 896 semantic units in the federation pool

### 60-second tour

```bash
pip install -e ".[z3]"          # core + formal verification
python examples/quickstart.py
```

```python
from axiomkit.invariants import InvariantPipeline

pipe = InvariantPipeline()
cand = pipe.propose(layer="L3", category="circuit-breaker",
                    statement="energy_draw(t2) <= energy_draw(t1) + quota")
pipe.verify(cand)                       # "verified" | "refuted" | "manual-review"
pipe.arbitrate(cand.id, decision="approve", arbiter="human-admin")
```

```python
# DriftGuard: wrap any agent chain
from drift_guard.middleware import DriftGuardMiddleware

guard = DriftGuardMiddleware.from_config("drift_guard/configs/thresholds.yaml")
guard.before_step(agent_id, goal_text)   # anchoring + semantic drift check
guard.after_tool(agent_id, tool, args)   # behavior risk + loop detection
```

### Repository map

```
├── axiomkit/        invariants · fedmem · gateway · recall · evolution
├── drift_guard/     anchor · semantic · behavior · coordination · circuit_breaker · observability
├── examples/        quickstart, evolution demo
└── tests/           unittest suite (CI matrix: Python 3.10–3.12)
```

---

## 中文

长期自治智能体以三种可预测的方式失败：**在压力下改写自身约束**、**共享知识时泄露原文**、**多步长程运行中意图漂移**。元极智能体栈（YUANJI Agent Stack）以两个正交引擎同时应对：

| 引擎 | 机制 | 类比 |
|---|---|---|
| **AxiomKit** | Z3 形式化验证不变量 + 联邦语义记忆 + 受护递归自改进 | CPU 微码 + ECC 共享缓存 |
| **DriftGuard** | 四层阈值防线 + 三级熔断：意图漂移/死循环/资源溢出/上下文熵增 + 六维治理漂移审计(MR-020) | 看门狗 + 断路器面板 |

### 核心特性

- **只追加、哈希锁档的规则集** — 智能体可提议规则，但永远无法独自批准（合成 → Z3 验证 → 仲裁流水线）
- **联邦记忆池(SFMP)** — 跨租户只流通提纯后的 A–D 分级语义碎片，原文不出域
- **受护自改进(RSI)** — 影子试跑 + 灰度晋升/自动回滚，公理核冻结，全程哈希链留痕
- **零侵入中间件** — DriftGuard 通过 `before_step() / after_tool() / report_context()` 钩子挂载，不改业务链路；嵌入后端自动降级（BGE-m3 → n-gram），防控链路不中断
- **自建仿真基准**：最大拦截效能 99.17%，假阳性/假阴性各 0.83%（32/32 用例）；5 条 Z3 验证不变量；联邦池 896 个语义单元

### 快速上手

```bash
pip install -e ".[z3]"
python examples/quickstart.py
```

DriftGuard 安装见 [`drift_guard/docs/README.md`](drift_guard/docs/README.md)（含固定阈值表与三级熔断动作矩阵）。

### 来源与治理

- 本仓库是从 [ZONGYUAN-ROOT](https://atomgit.com/zongyuangen/ZONGYUAN-ROOT) 治理内核中**提炼的衍生产出**，遵循「元极恒一」治理规范：基准资产只读、衍生独立建库
- 许可：Apache-2.0（见 [LICENSE](LICENSE)）
- 安全问题请勿公开提 Issue，见 [SECURITY.md](SECURITY.md)
