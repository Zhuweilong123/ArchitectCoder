"""pytest 公共配置。

测试全部使用 Mock LLM，不真正调用外部 API；但导入 ``app.agent_base`` 会经
``config.get_settings()`` 触发 ``deepseek_api_key`` 必填校验，
故在无 ``.env`` 的环境（如 CI）注入一个 dummy key，保证导入可成功。
"""
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DEEPSEEK_API_KEY", "test-dummy-key")


def pytest_configure(config: pytest.Config) -> None:
    """Keep pytest's generated workspaces outside the repository.

    ``tmp_path`` is used extensively by execution and evaluation tests.  A
    dedicated system-temp base prevents test artifacts from appearing under
    ``backend/`` and avoids stale workspace directories being picked up by
    source-control tooling.
    """

    if config.getoption("basetemp", default=None) is None:
        base = Path(tempfile.gettempdir()) / "uml-designer-pytest"
        base.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(base)
