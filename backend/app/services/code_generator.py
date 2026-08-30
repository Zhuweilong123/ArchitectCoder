"""Code generation service – supported languages.

代码生成能力已于 2026-08-27 下线（主画布「生成代码」按钮 + 相关端点移除）。
单图优化已于 2026-08-30 下线（/api/llm/optimize-uml 端点 + 前端「单图设计」按钮），
全局优化由 app.services.uml_optimizer_v2 承担。
本模块保留：
- ``SUPPORTED_LANGUAGES`` — 语言选择能力（供 /api/llm/languages 与后续复用）
"""

SUPPORTED_LANGUAGES = [
    "python", "java", "typescript", "javascript", "csharp", "cpp",
    "go", "rust", "ruby", "swift", "kotlin", "php",
]
