"""DriftGuard — AI Agent 意图漂移防控系统

四层阈值防线 + 三级熔断打断,低侵入插件式挂载。
算子模块:
    anchor        目标结构化锚定算子(只读锁档、目标对齐校验)
    semantic      语义漂移监测算子(BGE-m3 语义距离、漂移判定)
    behavior      行为模式风控算子(死循环检测、资源监控、上下文饱和度)
    coordination  跨Agent协同校验算子(父子目标一致性)
    circuit_breaker 三级熔断自愈调度算子(预警-限制-终止 + 自愈)
    middleware    低侵入挂载器(一键挂载/卸载/独立启停)
    observability 全链路日志 + 指标面板数据接口
"""

__version__ = "1.0.0"

from .middleware import DriftGuardMiddleware, mount, unmount  # noqa: F401
