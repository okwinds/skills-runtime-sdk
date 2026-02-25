"""
Observability（可观测性：指标汇总/离线诊断）。

说明：
- 本包提供“离线可重算”的 run 指标汇总能力（仅依赖 events.jsonl）。
- 不引入第三方监控依赖；平台侧可消费本包输出接入 Prometheus/OTel 等系统。
"""

from __future__ import annotations

__all__ = [
    "run_metrics",
]

