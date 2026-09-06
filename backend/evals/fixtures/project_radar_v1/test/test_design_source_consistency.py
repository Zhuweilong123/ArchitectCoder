"""Regression checks that keep the UML contract aligned with the source tree."""

from __future__ import annotations

import ast
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DESIGN_PATH = PROJECT_ROOT / "design" / "radar_design_0730.umlproj"
SOURCE_ROOT = PROJECT_ROOT / "src" / "radar_sim"

CLASS_MODULES = {
    "ModeController": "mode_control.py",
    "ModeParamTable": "mode_control.py",
    "TransmitCoordinator": "transmit.py",
    "WaveformGenerator": "transmit.py",
    "PulseSequencer": "transmit.py",
    "EchoSimulator": "echo.py",
    "TargetSceneManager": "echo.py",
    "DelayProcessor": "echo.py",
    "NoiseAdder": "echo.py",
    "PulseCompressor": "pulse_compression.py",
    "MatchedFilterBuilder": "pulse_compression.py",
    "PeakDetector": "pulse_compression.py",
}


def _design() -> dict:
    return json.loads(DESIGN_PATH.read_text(encoding="utf-8"))


def _source_class(class_name: str) -> ast.ClassDef:
    module_path = SOURCE_ROOT / CLASS_MODULES[class_name]
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)


def _source_method(class_node: ast.ClassDef, method_name: str) -> ast.FunctionDef:
    return next(
        node for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )


def _instance_fields(class_node: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for node in ast.walk(class_node):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                fields.add(target.attr)
    return fields


def _param_names(params: str) -> list[str]:
    if not params.strip():
        return []
    return [item.split(":", 1)[0].split("=", 1)[0].strip() for item in params.split(",")]


def test_uml_classes_and_methods_match_source():
    document = _design()
    class_diagrams = [diagram for diagram in document["diagrams"] if diagram["diagram_type"] == "class"]

    for diagram in class_diagrams:
        for design_class in diagram["classes"]:
            class_name = design_class["name"]
            source_class = _source_class(class_name)
            assert {item["name"] for item in design_class["attributes"]} == _instance_fields(source_class)

            source_methods = {
                node.name: node
                for node in source_class.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
            }
            for design_method in design_class["methods"]:
                method_name = design_method["name"]
                assert method_name in source_methods, f"{class_name}.{method_name} missing from source"
                source_method = _source_method(source_class, method_name)
                source_params = [arg.arg for arg in source_method.args.args if arg.arg != "self"]
                assert source_params == _param_names(design_method["params"]), (
                    f"{class_name}.{method_name} parameter contract drifted"
                )


def test_sequence_diagram_matches_main_flow_order():
    document = _design()
    sequence = next(diagram for diagram in document["diagrams"] if diagram["diagram_type"] == "sequence")
    labels = [message["label"] for message in sorted(sequence["messages"], key=lambda item: item["order"])]
    expected_labels = [
        "setMode",
        "getCurrentParams",
        "generateTransmitSignal",
        "buildFilter",
        "setTargets",
        "generateEcho",
        "compress",
        "detect",
    ]
    positions = [next(index for index, label in enumerate(labels) if expected in label) for expected in expected_labels]
    assert positions == sorted(positions)
    assert sequence["fragments"] == []

    main_source = (PROJECT_ROOT / "src" / "main.py").read_text(encoding="utf-8")
    source_calls = [
        "mode_controller.setMode(",
        "mode_controller.getCurrentParams(",
        "tx_coordinator.generateTransmitSignal(",
        "pulse_compressor.buildFilter(",
        "echo_simulator.setTargets(",
        "echo_simulator.generateEcho(",
        "pulse_compressor.compress(",
        "pulse_compressor.detector.detect(",
    ]
    source_positions = [main_source.index(expected) for expected in source_calls]
    assert source_positions == sorted(source_positions)
