# DriftGuard — AI Agent 意图漂移防控系统 部署说明书

版本: 1.0.0 | 日期: 2026-10-21 | 分层: 五大算子模块 + 中间件挂载 + 可观测性

## 1. 系统概述

解决大模型 Agent 多步推理意图漂移、目标跑偏、死循环、无效推理、资源溢出、
上下文熵增核心问题,提供「四层阈值防线 + 三级熔断打断」工业级可控自治能力。

### 分层架构

```
┌──────────────────────────────────────────────────────────┐
│  业务 Agent 链路(不改动)                                   │
│   before_step() → 推理 → after_tool() → report_context()  │
└──────────────┬───────────────────────────────────────────┘
               │ 中间件钩子(零侵入)
┌──────────────▼───────────────────────────────────────────┐
│  DriftGuardMiddleware(挂载器/调度装配)                     │
│  ├─ anchor       目标结构化锚定算子(只读锁档,阈值0.75)      │
│  ├─ semantic     语义漂移监测算子(BGE-m3,阈值0.3,步数20)   │
│  ├─ behavior     行为模式风控算子(死循环3,资源,上下文90%)   │
│  ├─ coordination 跨Agent协同校验算子(父子阈值0.6,溯源链)    │
│  ├─ circuit_breaker 三级熔断自愈调度(预警→限制→终止)        │
│  └─ observability 全链路日志 + /metrics 面板数据接口        │
└──────────────────────────────────────────────────────────┘
```

### 固定阈值(可在 configs/thresholds.yaml 按场景调整)

| 防线 | 阈值 | 动作 |
|---|---|---|
| 目标对齐相似度 | < 0.75 | 一级预警(重锚定) |
| 语义漂移距离 | > 0.3 | 一级预警(重锚定) |
| 推理步数 | > 20 | 一级预警 |
| 资源用量 | ≥ 80% 上限 | 一级预警;≥100% 三级终止 |
| 死循环 | 连续 3 次重复 | 二级熔断(暂停+锁工具) |
| 一级熔断连发 | 连续 2 次 | 升级二级 |
| 上下文饱和度 | ≥ 90% | 强制重锚定+清理 |
| 父子目标相似度 | < 0.6 | 拦截子输出+告警 |
| 二级熔断连发 | 连续 3 次 | 升级三级(终止止损) |

## 2. 环境依赖

Python ≥ 3.10,详见 `requirements.txt`。核心依赖仅 numpy + PyYAML;
BGE-m3 通过 FlagEmbedding 或 sentence-transformers 加载,两者都不可用时
自动降级内置 n-gram 嵌入器(防控链路不中断,降级事件写日志)。

## 3. 安装

```bash
# 方式一: 脚本安装
bash scripts/install.sh

# 方式二: 手动
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

可选(生产推荐,启用 BGE-m3 语义精度):
```bash
pip install FlagEmbedding        # 或 sentence-transformers
```

## 4. 启动与挂载

```bash
# 独立启停: 启动指标面板 API(端口 9820)
bash scripts/start.sh     # 后台启动 /metrics /healthz /trajectory/<task_id>
bash scripts/stop.sh
```

业务侧一行挂载(零侵入):
```python
from drift_guard import mount

guard = mount(task="整理Q3销售报表并发送给运营组")  # 自动解析+只读锁档目标

v = guard.before_step("正在检索Q3报表数据")   # 每轮推理前
if not v["allowed"]:
    handle(v["action"])                       # re_anchor|pause|terminate

guard.after_tool("search", {"query": "Q3销售额"}, tokens=820)  # 每次工具调用后
guard.report_context(context_tokens=115_000)   # 每轮上报上下文占用

# 跨Agent:
payload = guard.dispatch_child("sub-agent-1", "统计各区销售额")
v = guard.verify_child("sub-agent-1", child_output_text)

guard.snapshot()   # 面板快照
guard.unmount()    # 一键卸载
```

## 5. 目录结构

```
drift_guard/
├── __init__.py            # 包入口(暴露 mount/unmount)
├── config.py              # 配置单一真值入口 + 热加载 reload_config()
├── anchor.py              # 算子1: 目标结构化锚定
├── semantic.py            # 算子2: 语义漂移监测
├── behavior.py            # 算子3: 行为模式风控
├── coordination.py        # 算子4: 跨Agent协同校验
├── circuit_breaker.py     # 算子5: 三级熔断自愈调度
├── middleware.py          # 低侵入挂载器
├── observability.py       # 日志 + 指标面板 API
├── configs/
│   ├── thresholds.yaml    # 阈值配置(动态适配入口)
│   └── self_heal.yaml     # 自愈策略配置
├── scripts/
│   ├── install.sh / start.sh / stop.sh / run_selftest.sh
├── tests/
│   └── test_selftest.py   # 自测套件(准确率/误报率验证)
├── docs/
│   ├── selftest_report.md # 自测报告
│   └── logging_and_alerts.md # 故障告警与日志说明
└── storage/               # 运行期落盘(anchors/logs/faults/tickets)
```

## 6. 动态阈值适配

改 `configs/thresholds.yaml` 后热生效:
```python
from drift_guard.config import reload_config
reload_config()   # 重新读取 YAML,所有算子下一次校验即用新阈值
```
阈值迭代建议: 导出 `storage/logs/threshold.jsonl` 中的历史超限数据,
统计真实漂移分布的分位数,回填 YAML(如把 drift_threshold 调到 P95),
实现基于历史数据的持续优化。

## 7. 故障自愈

策略见 `configs/self_heal.yaml`:
- 语义漂移 → re_anchor 自动重锚定(最多 3 次),失败转二级
- 上下文溢出 → compact_context 清理无效上下文、保留核心目标
- 死循环 → rewind_step 回退(实际由二级熔断强制管控)
- 资源溢出 → 无法自愈,自动生成工单(storage/tickets/*.json)

## 8. 卸载

- 运行期: `guard.unmount()`(解除引用,业务零残留)
- 环境: `pip uninstall` 不适用(纯源码包),直接移除 drift_guard/ 目录即可,
  业务代码中 mount 调用点已做 try 兼容可另行删除
