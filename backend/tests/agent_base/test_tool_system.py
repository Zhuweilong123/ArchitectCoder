"""Tool 系统单元测试（无需 LLM）。"""
from app.agent_base.tools.base import Tool, ToolParameter
from app.agent_base.tools.registry import ToolRegistry


class EchoTool(Tool):
    def __init__(self):
        super().__init__(name="echo", description="回显输入文本")

    def get_parameters(self):
        return [ToolParameter(name="text", type="string", description="要回显的文本")]

    def run(self, parameters):
        return f"回显: {parameters.get('text', '')}"


def test_tool_parameter_defaults():
    p = ToolParameter(name="q", type="string", description="查询")
    assert p.name == "q"
    assert p.type == "string"
    assert p.required is True
    assert p.default is None


def test_tool_openai_schema():
    tool = EchoTool()
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    params = schema["function"]["parameters"]
    assert "text" in params["properties"]
    assert params["required"] == ["text"]


def test_registry_register_and_execute_tool():
    reg = ToolRegistry()
    reg.register_tool(EchoTool())
    assert "echo" in reg
    assert reg.execute_tool_with_params("echo", {"text": "hello"}) == "回显: hello"


def test_registry_register_function_and_execute():
    reg = ToolRegistry()
    reg.register_function("double", "翻倍", lambda s: str(int(s) * 2))
    assert "double" in reg
    # execute_tool: 函数直接收 input 字符串
    assert reg.execute_tool("double", "21") == "42"
    # execute_tool_with_params: 函数取 input 参数
    assert reg.execute_tool_with_params("double", {"input": "4"}) == "8"


def test_registry_openai_specs_includes_all():
    reg = ToolRegistry()
    reg.register_tool(EchoTool())
    reg.register_function("double", "翻倍", lambda s: s)
    specs = reg.get_openai_specs()
    names = {s["function"]["name"] for s in specs}
    assert names == {"echo", "double"}


def test_registry_compact_specs_preserve_call_shape_without_parameter_prose():
    reg = ToolRegistry()
    reg.register_tool(EchoTool())

    full = reg.get_openai_specs()[0]
    compact = reg.get_openai_specs(compact=True)[0]

    assert compact["function"]["name"] == full["function"]["name"]
    assert compact["function"]["parameters"]["properties"]["text"]["type"] == "string"
    assert "description" in full["function"]["parameters"]["properties"]["text"]
    assert "description" not in compact["function"]["parameters"]["properties"]["text"]


def test_registry_unregister_and_missing_tool():
    reg = ToolRegistry()
    reg.register_function("f", "desc", lambda s: s)
    assert reg.unregister("f") is True
    assert "f" not in reg
    assert "未找到工具" in reg.execute_tool("f", "x")
