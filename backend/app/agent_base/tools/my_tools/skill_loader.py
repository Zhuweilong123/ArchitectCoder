"""SkillTool — 按需加载 skills/ 下的领域知识包。

渐进式披露三级：
- L1 ``build_skills_section()``：name + description 目录，进静态 system prompt，
  字节恒定随前缀命中缓存。
- L2 ``skill(name)``：SKILL.md 正文 + 同目录引用文件清单。
- L3 ``skill(name, file=...)``：单个引用文件正文。

L3 必须由本工具投递而非让 agent 走 read_file —— ``file_system_tools.safe_path()``
把路径限制在 source_dir / test_dir / design_dir 三个 workspace root 内，而
skills/ 在仓库根目录、不在用户被分析项目里，read_file 必然抛
``Path escapes workspace``。

注意：本工具输出远超 ``TruncateHook`` 默认的 2000 字符上限，已在
``core/hooks.py`` 中为 "skill" 单独放宽，否则正文会被静默腰斩。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List

from app.agent_base.tools.base import Tool, ToolParameter

SKILL_ENTRY = "SKILL.md"

# skills/ 位于仓库根：my_tools → tools → agent_base → app → backend → <repo root>。
# 不复用 os.path.join(settings.uml_dir, "..", "..")（services/tools.py:354 等处的
# 仓库根推导）：uml_dir 默认值是相对路径 "../temp/uml_files"，依赖进程 CWD=backend/，
# 换目录启动就全歪。skill 目录用 __file__ 锚定，与启动方式无关。
_REPO_ROOT = Path(__file__).resolve().parents[5]


def _skills_root() -> Path:
    return _REPO_ROOT / "skills"


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    path: Path


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 ``---`` 包裹的平铺 key: value frontmatter，返回 (元数据, 正文)。

    frontmatter 只有 name / description 两个平铺字符串，手写解析即可 ——
    requirements.txt 里没有 pyyaml，不为两个字符串引依赖。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    meta: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:]).lstrip("\n")
        key, sep, value = line.partition(":")  # description 值本身含冒号，按首个切
        if sep:
            meta[key.strip()] = value.strip()

    return {}, text  # 无闭合 ---，视作没有 frontmatter


@lru_cache(maxsize=1)
def _default_skills() -> tuple[SkillMeta, ...]:
    return _discover_skills(_skills_root())


def discover_skills(root: Path | None = None) -> tuple[SkillMeta, ...]:
    """扫描 ``root`` 下带 SKILL.md 的子目录；``root=None`` 时用仓库内置 skills/。

    skill 是静态文件、进程内不变，默认路径结果缓存。无 SKILL.md 的目录直接
    跳过：静默收录一个没有 description 的 skill，在 L1 目录里就是一行没有
    触发语义的噪声，反而让模型更难判断该不该加载。
    """
    if root is None:
        return _default_skills()
    return _discover_skills(root)


def _discover_skills(root: Path) -> tuple[SkillMeta, ...]:
    if not root.is_dir():
        return ()

    found: list[SkillMeta] = []
    for entry in sorted(root.iterdir()):
        skill_file = entry / SKILL_ENTRY
        if not (entry.is_dir() and skill_file.is_file()):
            continue
        try:
            meta, _ = _parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        except OSError:
            continue
        description = meta.get("description", "").strip()
        if not description:
            continue
        found.append(SkillMeta(
            name=meta.get("name", "").strip() or entry.name,
            description=description,
            path=entry,
        ))
    return tuple(found)


def build_skills_section(root: Path | None = None) -> str:
    """生成注入 system prompt 的 L1 目录段；无 skill 时返回空串。"""
    skills = discover_skills(root)
    if not skills:
        return ""

    lines = [
        "## Skills",
        "On-demand knowledge packs. When a task matches one, call "
        "skill(name) FIRST and follow what it says — do not work from memory:",
    ]
    lines.extend(f"- {s.name}: {s.description}" for s in skills)
    return "\n".join(lines)


def _reference_files(skill_dir: Path) -> list[Path]:
    """SKILL.md 之外的同目录引用文件（递归，稳定排序）。"""
    return sorted(
        p for p in skill_dir.rglob("*")
        if p.is_file() and p.name != SKILL_ENTRY
    )


class SkillTool(Tool):
    """加载一个 skill 的正文或其引用文件。"""

    def __init__(self, root: Path | None = None):
        super().__init__(
            name="skill",
            description=(
                "Load a knowledge pack listed under '## Skills' in the system "
                "prompt. Call with name only to get the skill's main guide plus "
                "its reference file list; pass file to load one reference file."
            ),
        )
        self._root = root

    def get_parameters(self) -> List[ToolParameter]:
        return []  # 用 to_openai_schema 提供带 enum 的精确 schema

    def to_openai_schema(self) -> dict:
        names = [s.name for s in discover_skills(self._root)]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        # enum 让模型无法编造不存在的 skill 名
                        "name": {"type": "string", "enum": names},
                        "file": {
                            "type": "string",
                            "description": (
                                "Optional reference file, relative to the skill "
                                "directory, as listed by a prior skill(name) call."
                            ),
                        },
                    },
                    "required": ["name"],
                },
            },
        }

    def run(self, parameters: dict) -> str:
        skills = discover_skills(self._root)
        name = (parameters.get("name") or "").strip()

        match = next((s for s in skills if s.name == name), None)
        if match is None:
            available = ", ".join(s.name for s in skills) or "(none)"
            return f"Error: unknown skill '{name}'. Available: {available}"

        rel = (parameters.get("file") or "").strip()
        if not rel:
            return self._render_entry(match)

        try:
            target = (match.path / rel).resolve()
            target.relative_to(match.path.resolve())
        except ValueError:
            # skill 是唯一能读 workspace 之外磁盘内容的工具，
            # file="../../backend/.env" 这类穿越必须挡住。
            return f"Error: '{rel}' escapes skill directory '{match.name}'"
        except OSError as e:
            return f"Error: cannot resolve '{rel}': {e}"

        if not target.is_file():
            listing = self._render_references(match) or "  (none)"
            return (
                f"Error: '{rel}' not found in skill '{match.name}'.\n"
                f"Reference files:\n{listing}"
            )

        try:
            return target.read_text(encoding="utf-8")
        except OSError as e:
            return f"Error: cannot read '{rel}': {e}"

    def _render_entry(self, skill: SkillMeta) -> str:
        try:
            _, body = _parse_frontmatter(
                (skill.path / SKILL_ENTRY).read_text(encoding="utf-8")
            )
        except OSError as e:
            return f"Error: cannot read skill '{skill.name}': {e}"

        listing = self._render_references(skill)
        if not listing:
            return body
        return (
            f"{body}\n\n---\n"
            f"Reference files (load with skill(name=\"{skill.name}\", file=...)):\n"
            f"{listing}"
        )

    @staticmethod
    def _render_references(skill: SkillMeta) -> str:
        # 附上字节数，让模型对加载成本有感、优先挑最相关的一个而非全拉
        return "\n".join(
            f"- {p.relative_to(skill.path).as_posix()} ({p.stat().st_size} bytes)"
            for p in _reference_files(skill.path)
        )
