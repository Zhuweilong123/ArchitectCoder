import json
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path

from app.agent_base.core.contracts import ArtifactFacts, ContractEntity, ContractSnapshot
from app.agent_base.core.contract_harness import ContractHarness
from app.agent_base.core.language_adapters import (
    ClangAstAdapter,
    LanguageAdapterRegistry,
    PythonAstAdapter,
    broker_command_runner,
    default_language_adapters,
)


def test_python_adapter_returns_normalized_source_facts(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text(
        "import os\n\nclass Service:\n    def run(self):\n        return 1\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )

    facts = PythonAstAdapter().extract(source, project_id="p1")

    assert facts.status == "success"
    assert {item.entity_type for item in facts.entities} >= {
        "source_module", "source_import", "source_class", "source_method", "source_function",
    }
    service = next(item for item in facts.entities if item.name == "Service")
    method = next(item for item in facts.entities if item.name == "run")
    assert method.parent_id == service.entity_id


def test_python_adapter_reports_syntax_failure(tmp_path):
    source = tmp_path / "broken.py"
    source.write_text("class Broken(:\n", encoding="utf-8")

    facts = PythonAstAdapter().extract(source)

    assert facts.status == "failed"
    assert facts.diagnostics[0]["code"] == "parse_error"


def test_clang_adapter_uses_compile_commands_and_parses_json_ast(tmp_path):
    source = tmp_path / "main.cpp"
    source.write_text("struct Service {}; int run() { return 0; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {
            "directory": str(tmp_path),
            "file": "main.cpp",
            "arguments": ["clang++", "-std=c++20", "-c", "main.cpp", "-o", "main.o"],
        },
    ]), encoding="utf-8")
    calls = {}

    def runner(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"kind": "TranslationUnitDecl", "inner": [
                {"kind": "CXXRecordDecl", "name": "Service", "loc": {"line": 1}},
                {"kind": "FunctionDecl", "name": "run", "loc": {"line": 1}},
            ]}).encode(),
            stderr=b"",
        )

    facts = ClangAstAdapter(runner=runner).extract(
        source, project_id="cpp", project_root=tmp_path,
    )

    assert facts.status == "success"
    assert {item.name for item in facts.entities} == {"Service", "run"}
    assert "-Xclang" in calls["argv"]
    assert "-ast-dump=json" in calls["argv"]
    assert "-c" not in calls["argv"]
    assert calls["kwargs"]["cwd"] == str(tmp_path)


def test_clang_adapter_reuses_nearest_translation_unit_for_headers(tmp_path):
    source = tmp_path / "service.hpp"
    source.write_text("struct Service {};\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {
            "directory": str(tmp_path),
            "file": "service.cpp",
            "arguments": ["clang++", "-I.", "-c", "service.cpp", "-o", "service.o"],
        },
    ]), encoding="utf-8")
    calls = {}

    def runner(argv, **kwargs):
        calls["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=b'{"kind":"TranslationUnitDecl"}', stderr=b"")

    facts = ClangAstAdapter(runner=runner).extract(source, project_root=tmp_path)

    assert facts.status == "success"
    assert calls["argv"][-1] == str(source.resolve())
    assert "service.cpp" not in calls["argv"]


def test_clang_adapter_requires_compile_database(tmp_path):
    source = tmp_path / "main.cpp"
    source.write_text("int main() {}\n", encoding="utf-8")

    facts = ClangAstAdapter().extract(source, project_root=tmp_path)

    assert facts.status == "failed"
    assert facts.diagnostics[0]["code"] == "compile_database_missing"


def test_clang_adapter_does_not_launch_host_process_without_runner(tmp_path):
    source = tmp_path / "main.cpp"
    source.write_text("int main() {}\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"directory": str(tmp_path), "file": "main.cpp", "arguments": ["clang++", "main.cpp"]},
    ]), encoding="utf-8")

    facts = ClangAstAdapter().extract(source, project_root=tmp_path)

    assert facts.status == "failed"
    assert facts.diagnostics[0]["code"] == "clang_runner_unconfigured"


def test_clang_adapter_rejects_non_clang_compile_command(tmp_path):
    source = tmp_path / "main.cpp"
    source.write_text("int main() {}\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"directory": str(tmp_path), "file": "main.cpp", "arguments": ["bash", "-c", "echo unsafe"]},
    ]), encoding="utf-8")

    facts = ClangAstAdapter(runner=lambda *args, **kwargs: None).extract(
        source, project_root=tmp_path,
    )

    assert facts.status == "failed"
    assert facts.diagnostics[0]["code"] == "compile_command_invalid"


def test_broker_command_runner_converts_structured_evidence():
    calls = {}

    class Broker:
        sandbox_name = "container"

        async def execute(self, task, cwd, *, policy):
            calls.update(task=task, cwd=cwd, policy=policy)
            return SimpleNamespace(status="success", exit_code=0, output='{"kind":"TranslationUnitDecl"}')

    result = broker_command_runner(Broker())(
        ["clang++", "-fsyntax-only", "main.cpp"], cwd="/workspace",
        capture_output=True, timeout=3, check=False,
    )

    assert result.returncode == 0
    assert result.stdout == b'{"kind":"TranslationUnitDecl"}'
    assert calls["task"].kind.value == "custom"
    assert calls["policy"].sandbox == "container"


def test_registry_is_extensible_and_does_not_whitelist_languages(tmp_path):
    source = tmp_path / "main.cpp"
    source.write_text("int main() {}\n", encoding="utf-8")
    registry = LanguageAdapterRegistry()
    registry.register(PythonAstAdapter())
    registry.register(ClangAstAdapter())

    assert registry.adapter_for(source).language == "cpp"
    assert default_language_adapters().adapter_for(tmp_path / "x.py").language == "python"


def test_design_contract_provider_consumes_registered_cpp_facts(tmp_path):
    from extensions.design_contract.provider import DesignContractProvider

    source_root = tmp_path / "src"
    test_root = tmp_path / "tests"
    source_root.mkdir()
    test_root.mkdir()
    source = source_root / "service.cpp"
    test = test_root / "service_test.cpp"
    source.write_text("", encoding="utf-8")
    test.write_text("", encoding="utf-8")

    @dataclass
    class FakeAdapter:
        language: str = "cpp"

        def supports(self, path):
            return Path(path).suffix == ".cpp"

        def extract(self, path, **kwargs):
            name = Path(path).stem
            entity_type = "source_function" if "test" in name else "source_class"
            entity_name = "test_service" if "test" in name else "Service"
            return ArtifactFacts(
                project_id="",
                scope=kwargs.get("scope", "source"),
                status="success",
                entities=(ContractEntity(
                    f"cpp:{entity_name}", entity_type, entity_name, str(path), 1,
                ),),
            )

    registry = LanguageAdapterRegistry((FakeAdapter(),))
    provider = DesignContractProvider(language_adapters=registry)
    facts = provider.collect_facts({
        "workspace_root": str(tmp_path),
        "source_root": str(source_root),
        "test_root": str(test_root),
    }, project_id="cpp-fixture")

    assert facts.status == "collected"
    assert any(item.entity_type == "source_class" for item in facts.entities)
    assert any(item.entity_type == "test_case" for item in facts.entities)


def test_contract_harness_considers_cpp_changes_relevant(tmp_path):
    source_root = tmp_path / "src"
    source_root.mkdir()
    changed = source_root / "service.cpp"
    changed.write_text("", encoding="utf-8")

    class Provider:
        def collect(self, manifest, project_id="", scope="project"):
            return ContractSnapshot(project_id, scope, "collected")

    result = ContractHarness().check(
        {
            "workspace_root": str(tmp_path),
            "source_root": str(source_root),
        },
        project_id="cpp",
        changed_paths=[str(changed)],
        contract_provider=Provider(),
    )

    assert result.status != "not_applicable"


def test_cpp_fixture_can_pass_existing_uml_source_test_contract_rules(tmp_path):
    from extensions.design_contract.provider import DesignContractProvider

    source_root = tmp_path / "src"
    test_root = tmp_path / "tests"
    source_root.mkdir()
    test_root.mkdir()
    (source_root / "service.cpp").write_text("", encoding="utf-8")
    (test_root / "service_test.cpp").write_text("", encoding="utf-8")
    design = tmp_path / "design.umlproj"
    design.write_text(json.dumps({
        "diagrams": [{
            "id": "class-diagram",
            "classes": [{"id": "Service", "name": "Service", "methods": [{"name": "run"}]}],
        }],
    }), encoding="utf-8")

    class FakeAdapter:
        language = "cpp"

        def supports(self, path):
            return Path(path).suffix == ".cpp"

        def extract(self, path, **kwargs):
            if "test" in Path(path).stem:
                entities = (
                    ContractEntity("test-function", "source_function", "test_service", str(path), 1),
                )
            else:
                class_entity = ContractEntity("service-class", "source_class", "Service", str(path), 1)
                entities = (
                    class_entity,
                    ContractEntity("service-run", "source_method", "run", str(path), 2, class_entity.entity_id),
                )
            return ArtifactFacts("", kwargs.get("scope", "source"), "success", entities=entities)

    provider = DesignContractProvider(
        language_adapters=LanguageAdapterRegistry((FakeAdapter(),)),
    )
    result = ContractHarness().check(
        {
            "workspace_root": str(tmp_path),
            "project_file": str(design),
            "project_files": [str(design)],
            "source_root": str(source_root),
            "test_root": str(test_root),
        },
        project_id="cpp-fixture",
        changed_paths=[str(source_root / "service.cpp")],
        contract_provider=provider,
    )

    assert result.status == "pass"
