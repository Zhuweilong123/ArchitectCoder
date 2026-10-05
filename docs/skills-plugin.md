# Skill 插件

Skill 通过独立的 `skills` 插件槽位提供给 Agent。核心负责协议、任务内目录和工具适配；文件发现、元数据解析、资源读取由 `extensions/skills` 实现。替换存储实现不需要修改 Agent 主流程。

## 配置

默认启用仓库内置技能：

```dotenv
AGENT_SKILLS_ENABLED=true
AGENT_SKILLS_PROVIDER=extensions.skills:create
```

设置 `AGENT_SKILLS_ENABLED=false` 可关闭能力。插件导入、工厂创建或目录读取失败时，Agent 使用空目录，不注入 Skills Prompt，也不注册 `skill` 工具。读取单个资源失败时，工具返回明确错误。

## 模块边界

| 模块 | 职责 |
|---|---|
| `backend/app/agent_base/core/skills.py` | `SkillProvider` 协议、数据结构、NoOp、目录捕获和版本检查 |
| `extensions/skills/provider.py` | 内置文件技能发现、frontmatter 解析、资源快照、路径检查 |
| `backend/app/agent_base/tools/my_tools/skill_loader.py` | L1 Prompt 目录、L2 正文和 L3 参考资源的工具适配 |
| `backend/app/agent_base/assembly.py` | 创建目录并共享给主 Agent Prompt、工具和直接创建的子 Agent |
| `skills/<目录名>/SKILL.md` | 内置技能内容及同目录参考资源 |

`SkillMeta` 包含稳定 ID、显示名称、描述、版本、作用域和来源，不暴露文件路径。文件实现使用目录名作为 ID，frontmatter 中的 `name` 作为显示名称；缺少描述、不可读取或名称重复的技能会被跳过。

## 任务内一致性

创建 Agent 时捕获一次目录。Prompt 中的名称、工具 schema 中的名称和实际读取内容使用同一份 `SkillCatalog`。主 Agent 直接创建的子 Agent 共享该目录；独立创建的子 Agent 捕获自己的目录。

两个 provider 接口已纳入统一调度：`list_skills` 默认绑定公共阶段 `initialize`，`read_skill` 默认绑定 `tool_before`。在活动操作中调用时继承该操作当前阶段，例如技能工具中的读取挂在该工具操作之下。调用条件与目录快照保持不变，实际执行通过计划中的 `skills.interface.*` 贡献，并可在有活动 Trace 的任务中回放。详见[插件阶段调度](plugin-lifecycle.md)。

默认文件 provider 在创建时读取正文和参考文件，版本由内容哈希生成。任务期间修改、删除或新增技能不会改变已有快照，新创建的 Agent 才会看到更新。资源快照驻留内存，适合当前规模的文本技能包；后续大型资源存储可通过其他 provider 实现。

自定义 provider 必须在一个实例生命周期内保持版本一致。核心会拒绝与目录 ID 或版本不一致的读取结果，并提示启动新任务刷新。

## 扩展 provider

通过 `module:factory` 注册工厂，工厂接受 `settings` 等关键字参数，返回实现以下协议的对象：

```python
from app.agent_base.core.skills import SkillContent, SkillContext, SkillMeta

class MySkillProvider:
    def list_skills(self, context: SkillContext | None = None) -> tuple[SkillMeta, ...]:
        return (SkillMeta("review", "review-guide", "Review checklist", "v1"),)

    def read_skill(self, skill_id: str, resource: str | None = None) -> SkillContent:
        return SkillContent(skill_id, "v1", "Review instructions")

def create(*, settings=None, **kwargs):
    return MySkillProvider()
```

配置 `AGENT_SKILLS_PROVIDER=my_package.skills:create` 即可替换。`SkillContext.workspace_root` 为以后增加项目级技能预留上下文；默认实现目前只读取仓库内置技能。

## 当前范围

本阶段提供只读技能插件，保留现有 `skill(name, file)` 调用和渐进式内容加载。文件 provider 拒绝路径越界，排除指向技能目录外的参考文件；读取资源不会执行脚本，代码执行仍通过现有运行时工具进行。

项目级技能写入、从任务经验生成候选技能、验证与晋升、使用效果反馈及自动迭代属于后续阶段。本阶段为这些能力建立解耦接口，不会自动固化任务内容。
