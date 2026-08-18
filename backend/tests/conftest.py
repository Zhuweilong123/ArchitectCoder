"""pytest 公共配置。

测试全部使用 Mock LLM，不真正调用外部 API；但导入 ``app.agent_base`` 会经
``app.core.config.get_settings()`` 触发 ``deepseek_api_key`` 必填校验，
故在无 ``.env`` 的环境（如 CI）注入一个 dummy key，保证导入可成功。
"""
import os

os.environ.setdefault("DEEPSEEK_API_KEY", "test-dummy-key")
