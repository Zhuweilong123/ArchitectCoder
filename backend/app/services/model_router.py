"""按任务复杂度选择模型的确定性路由器。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelRoute:
    model: str
    tier: str
    reason: str


def choose_model(message: str, settings) -> ModelRoute:
    text = (message or "").lower()
    complex_words = ("uml", "类图", "架构", "设计", "设计图", "关系", "源码", "跨文件", "同步",
                     "重构", "迁移", "一致性",
                     "代码", "实现", "修复", "分析项目", "优化")
    if any(word in text for word in complex_words) or len(text) > 240:
        return ModelRoute(settings.deepseek_model, "pro", "complex task")
    return ModelRoute(settings.deepseek_model_flash, "flash", "simple task")
