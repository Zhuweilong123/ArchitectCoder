# Radar Signal Processing Quickstart

这是 ArchitectCoder 的内置快启案例，来自 `project/project_radar`。
案例用于体验 UML 设计、源代码实现、测试验证、Trace 分析和评测中心性能对比。

## 目录结构

```text
design/radar_design.umlproj  UML 设计文件
src/main.py                       示例入口
src/radar_sim/                    雷达信号处理源码
test/                             pytest 测试和 UML-源码一致性检查
reference/                        脱敏的性能参考摘要
```

## 快速运行

在案例根目录执行：

```powershell
python -m pytest test -q
```

在 ArchitectCoder 中打开本目录后，可以继续查看 UML 图，要求 Agent 分析或修改设计/源码，并在评测中心运行相关用例。

## 性能参考

`reference/performance-baseline.json` 来自最近一次 16 个基线用例的性能结果，仅作为快启案例的参考数据，不代表当前环境重新执行后的实时结果。

参考运行使用的模型、版本和汇总指标均已脱敏保留；Trace、工作区绝对路径、Prompt 内容和模型密钥不会随案例发布。
