"""BaseAgents 异常体系"""


class BaseAgentsException(Exception):
    """BaseAgents 框架基础异常"""
    pass


class ConfigError(BaseAgentsException):
    """配置相关错误"""
    pass


class LLMError(BaseAgentsException):
    """LLM 调用相关错误"""
    pass


class AgentError(BaseAgentsException):
    """Agent 执行相关错误"""
    pass


class AgentInterrupted(AgentError):
    """Agent 循环被中断（hook 抛出的停止信号，传播到编排层）。"""
    pass


class ToolError(BaseAgentsException):
    """工具执行相关错误"""
    pass
