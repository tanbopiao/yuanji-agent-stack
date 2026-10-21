"""config.py — 阈值与自愈策略配置加载

单一真值入口: 所有算子从 CONFIG 读取阈值,禁止各模块私设硬编码。
支持环境变量 DRIFTGUARD_CONFIG 指向自定义 YAML;基于历史数据的阈值迭代
只需更新 YAML 并 reload(),无需改代码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict

import yaml

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "configs", "thresholds.yaml")
DEFAULT_HEAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "configs", "self_heal.yaml")


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Config:
    thresholds: Dict[str, Any] = field(default_factory=dict)
    self_heal: Dict[str, Any] = field(default_factory=dict)

    # ---- 固定阈值(方案既定值,作为缺省兜底;YAML 可按场景覆盖) ----
    @property
    def alignment_threshold(self) -> float:
        return float(self.thresholds.get("anchor", {}).get("alignment_threshold", 0.75))

    @property
    def drift_threshold(self) -> float:
        return float(self.thresholds.get("semantic", {}).get("drift_threshold", 0.3))

    @property
    def max_reasoning_steps(self) -> int:
        return int(self.thresholds.get("semantic", {}).get("max_reasoning_steps", 20))

    @property
    def embedding_model(self) -> str:
        return str(self.thresholds.get("semantic", {}).get("embedding_model", "BAAI/bge-m3"))

    @property
    def embedding_device(self) -> str:
        return str(self.thresholds.get("semantic", {}).get("device", "cpu"))

    @property
    def loop_repeat_limit(self) -> int:
        return int(self.thresholds.get("behavior", {}).get("loop", {}).get("repeat_limit", 3))

    @property
    def res_max_tokens(self) -> int:
        return int(self.thresholds.get("behavior", {}).get("resources", {}).get("max_tokens", 200000))

    @property
    def res_max_api_calls(self) -> int:
        return int(self.thresholds.get("behavior", {}).get("resources", {}).get("max_api_calls", 500))

    @property
    def res_max_runtime(self) -> float:
        return float(self.thresholds.get("behavior", {}).get("resources", {}).get("max_runtime_seconds", 3600))

    @property
    def res_warn_ratio(self) -> float:
        return float(self.thresholds.get("behavior", {}).get("resources", {}).get("warn_ratio", 0.8))

    @property
    def context_saturation(self) -> float:
        return float(self.thresholds.get("behavior", {}).get("context", {}).get("saturation_threshold", 0.90))

    @property
    def child_alignment_threshold(self) -> float:
        return float(self.thresholds.get("coordination", {}).get("child_alignment_threshold", 0.6))

    @property
    def level1_to_level2(self) -> int:
        return int(self.thresholds.get("circuit_breaker", {}).get("level1_to_level2", 2))

    @property
    def level2_to_level3(self) -> int:
        return int(self.thresholds.get("circuit_breaker", {}).get("level2_to_level3", 3))

    @property
    def metrics_api_host(self) -> str:
        return str(self.thresholds.get("metrics_api", {}).get("host", "127.0.0.1"))

    @property
    def metrics_api_port(self) -> int:
        return int(self.thresholds.get("metrics_api", {}).get("port", 9820))

    # ---- 自愈策略 ----
    def heal_strategy(self, scene: str) -> str:
        return str(self.self_heal.get("self_heal", {}).get(scene, {}).get("strategy", "ticket"))

    def heal_attempts(self, scene: str) -> int:
        return int(self.self_heal.get("self_heal", {}).get(scene, {}).get("max_attempts", 1))

    @property
    def ticket_dir(self) -> str:
        return str(self.self_heal.get("ticket", {}).get("dir", "storage/tickets"))


def load_config(config_path: str | None = None,
                heal_path: str | None = None) -> Config:
    path = config_path or os.environ.get("DRIFTGUARD_CONFIG", DEFAULT_CONFIG_PATH)
    hpath = heal_path or os.environ.get("DRIFTGUARD_SELF_HEAL", DEFAULT_HEAL_PATH)
    cfg = Config()
    if os.path.exists(path):
        cfg.thresholds = _load_yaml(path)
    if os.path.exists(hpath):
        cfg.self_heal = _load_yaml(hpath)
    return cfg


# 进程级单例;reload_config() 供阈值热迭代
CONFIG = load_config()


def reload_config(config_path: str | None = None,
                  heal_path: str | None = None) -> Config:
    """动态阈值适配入口: 更新 YAML 后调用即可热生效。"""
    global CONFIG
    CONFIG = load_config(config_path, heal_path)
    return CONFIG
