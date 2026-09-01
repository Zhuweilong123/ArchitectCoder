---
name: bugfix-architectcoder
description: 记录ArchitectCoder项目开发中遇到的问题及解决方案
metadata: 
  node_type: memory
  type: project
  originSessionId: 0194a1c3-ffe7-43ae-ae97-6e9071116351
---

# ArchitectCoder 项目问题与解决方案汇总

## 问题速查表

| # | 问题 | 根因 | 分类 |
|---|------|------|------|
| 1 | 页面白屏 | `react-shape` 不存在于 X6 核心包 | X6/渲染 |
| 2 | 节点只有黑框无内容 | `attrs.content.html` 设置方式错误 | X6/渲染 |
| 3 | 多个类堆叠在同一位置 | "添加类" 按钮使用固定坐标 (200,200) | 交互 |
| 4 | npm install 版本不兼容 | @antv/x6 插件版本与核心版本不匹配 | 环境 |
| 5 | TypeScript 编译错误 | X6 v2 API 差异（snapline/keyboard/bindKey等） | 环境 |
| 6 | 端口圆点不可见 | ports markup 缺少 circle 定义 | X6/渲染 |
| 7 | pip install pandas 超时 | Python 3.14 无旧版 pandas wheel | 环境 |
| 8 | LLM 优化返回 500 | LLM 输出枚举值/字段名不匹配 Pydantic 模型 | LLM/后端 |
| 9 | 500 错误难调试 | FastAPI 默认不打印完整堆栈 | 后端/调试 |
| 10 | 后端代码修改后未生效 | uvicorn reload 不可靠 | 环境 |
| 11 | 关系类型修改后画布不更新 | 边同步只处理新增，不更新已有边 | X6/同步 |
| 12 | 网格开关无效/不显示 | X6 grid API 调用格式和初始化参数不当 | X6/渲染 |
| 13 | 流水线启动 404 + 422 | pipeline 创建时序错误 + FastAPI 多参数解析失败 | 后端/API |
| 14 | WebSocket 代理 404 | Vite proxy `/api` 缺少 `ws: true` | 环境 |
| 15 | 节点位置抖动 | (补充) 避免不必要的 setPosition/setSize | X6/性能 |
| 16 | `StageName.uml_optimize` 不存在 | Python Enum 成员名需大写 | 后端 |
| 17 | Excel 中文乱码/代理对 | Python 非 UTF-8 模式运行导致 openpyxl 读写中文异常 | 环境/编码 |
| 18 | GitHub push 认证失败 | 密码认证已废弃，需用 SSH Key 或 Personal Access Token | 环境/Git |
| 19 | GitHub 网站无法访问 | 国内网络 DNS 污染/GFW 阻断，需代理或改用 Gitee | 环境/Git |
| 20 | 流水线 Stage 4 不暂停 | `resume_pipeline` 旧代码直接标记SUCCESS跳过 | 后端/流水线 |
| 21 | 进度条完成停在86% | 缺少 `pipeline_complete` 事件，WebSocket直接关闭 | 后端/流水线 |
| 22 | 测试产物不显示 | `resume_pipeline` Stage 5 保存磁盘但未 append artifacts | 后端/流水线 |
| 23 | 源代码产物翻倍 | `resume_pipeline` 被多次调用重复跑 Stage 3 | 后端/流水线 |
| 24 | 优化需求弹窗消失 | 重复的空 `else if` 吞掉了 `request_instructions` 事件 | 前端 |
| 25 | "检视完成继续"无响应 | 多条路径到达Stage 4但未注册等待逻辑 | 后端/流水线 |
| 26 | ReAct 工具调用 coroutine 未 await | Tool.execute lambda 返回协程但引擎未 await | 后端/ReAct |
| 27 | ReAct API 400 错误 | DeepSeek 不支持 OpenAI function calling 的 tool_calls 格式 | 后端/ReAct |
| 28 | 优化仅1轮就停止 | LLM 自称完成但实际未修复，缺少验证机制 | 后端/流水线 |
| 29 | 日志只记录最后一轮 | 优化结果被覆盖，历史未保存 | 后端/流水线 |
| 30 | 文件无法打开（全部 API 失败） | Vite 代理端口 8001，后端监听 8000 — 端口不匹配 | 环境/前端 |
| 31 | 生成的测试用例不包含用例ID | ① Sheet "用例概览" 污染 ② 列索引硬编码 ③ LLM 提示词源码优先于用例 | LLM/后端 |
| 32 | 后端代码修改后不生效 | 多个僵尸 Python 进程占 8000 端口，StatReload 重载不到新代码 | 环境 |
| 33 | Python 3.14 PYTHONUTF8 崩溃 | `set PYTHONUTF8=1` 触发 fatal error，改用 `-X utf8` | 环境/Python |
| 34 | Stage 6/7 LLM 模拟测试不可靠 | `_execute_tests()` LLM猜PASS/FAIL，每次不同 → 改真实pytest subprocess | 后端/流水线 |
| 35 | Stage 6 ReAct 盲修测试 | Action解析失败、工具bug → 去掉ReAct，简化为LLM一次性编译修复 | 后端/流水线 |
| 36 | Path.parent 路径算错 | Python 3.9+ `Path(".").parent` 返回自身 → 全改 `.resolve().parent.parent` | 后端 |
| 37 | 改代码不生效 | `.pyc` 缓存优先源码 → `sys.dont_write_bytecode = True` | 后端/环境 |
| 38 | 测试数量不一致 | `new_test_results[:2000]` 截断 → 去截断、展示上限提到5000 | 后端/流水线 |
| 39 | 失败原因无法提取 | regex 不兼容 `\r\n` + pytest格式 → 三策略递进匹配 | 后端/流水线 |
| 40 | 测试文件保存不清理旧文件 | JSON解析失败fallback存非法.py，旧文件残留被pytest收集 | 后端/流水线 |
| 41 | 最终测试结果和第三轮不一致 | for-else多余的`_execute_tests`覆盖了最后一轮的正确数据 | 后端/流水线 |
| 42 | 前端转圈不停+进度条不到100% | running/completed独立state手动维护，事件时序问题 | 前端 |
| 43 | 后端日志缺少时间戳 | uvicorn/logging默认无`asctime` | 后端/日志 |
| 44 | API Key 硬编码在 config.py 默认值中 | 真实 Key 随代码提交 Git，永久暴露在历史记录 | 安全 |
| 45 | 全端点无鉴权，任何人可调 LLM API | 无认证机制，公网部署后 API 配额可被盗刷 | 安全 |
| 46 | 文件端点目录遍历漏洞 | `/api/files/open` 和 `/browse` 接受任意路径参数，可读取项目外任意文件 | 安全 |
| 47 | FastAPI HTTPBearer 子依赖注入失败 | `Depends` 嵌套闭包时 `request` 参数无法自动注入 | 后端/鉴权 |
| 48 | WebSocket 路由级 Depends 报错 | WebSocket 不支持 HTTP 的 `Depends` 依赖注入机制 | 后端/鉴权 |
| 49 | Windows asyncio subprocess 抛 NotImplementedError | ProactorEventLoop 不支持 `create_subprocess_exec`，pytest 无法真实执行 | 后端/测试 |
| 50 | `__pycache__` 残留导致 pytest 0 tests collected | 旧测试目录的 `.pyc` 缓存引用旧路径，与新位置冲突 | 后端/测试 |
| 51 | Stage 5 JSON 解析失败 | `generate_tests` 等未开 `json_mode`，`clean_llm_json_response` 只处理开头 ``` 的情况 | LLM/后端 |
| 52 | Stage 7 三轮优化无改善 | 优化 prompt 只看错误行不看测试代码，LLM 猜不出缺失函数的签名 | 后端/流水线 |
| 53 | Stage 6 误拦截对象级 AttributeError | `_extract_fatal_errors` 把 `'Foo' object has no attribute 'bar'` 当做编译错误 | 后端/流水线 |
| 54 | 未选目录时保存到旧嵌套路径 | `_save_generated_files` 默认路径含 `{project}/{language}` 子目录，与扁平结构不一致 | 后端/流水线 |
| 55 | 目录选择每次都要重选 | 前端 `pipelineSourceDir/TestDir` 存内存，刷新丢失 | 前端 |
| 56 | 时序图消息位置保存后丢失 | 消息 Y 用 `order*40` 计算，order 有间隔时 Y 跳变 | 前端/时序图 |
| 57 | 时序图消息连线切换视角后位置丢失 | 边 Y 坐标只在创建时计算，切换组件重建时重算 | 前端/时序图 |
| 58 | `setDiagram` 替换了整个 project.diagrams | legacy 函数把 diagrams 替换为 `[diagram]` 单元素数组 | 前端 |
| 59 | 优化设计"接受"后其他图消失 | DiffViewer 接受时调用 `setDiagram` → 覆盖所有图 | 前端 |
| 60 | 优化设计弹窗提示始终是类图内容 | Toolbar/DiffViewer 硬编码 "类图包含 X 个类，Y 条关系" | 前端 |
| 61 | 打开项目保存生成新文件 | 保存 `saveProject` 未传现有 filepath，后端生成新文件名 | 前后端 |
| 62 | 时序图生命线 x 坐标变成负值 | `graph.clientToLocal()` 在 StrictMode 双 mount 下返回异常坐标 | 前端/时序图 |
| 63 | 浏览 API 不显示 .umlproj 文件 | `browse_directory` 只过滤 `.uml` 文件 | 后端 |
| 64 | 组件图不支持嵌套组件 | CompNode 无 parent_id，X6 无 embedding | 前端/组件图 |
| 65 | Ctrl+C/V 复制不保留尺寸 | 粘贴调用 addComponent/addClass 使用默认尺寸 | 前端 |
| 66 | 网格只在类图生效 | SeqEditor/CompEditor 硬编码 grid 参数，未读 diagram 数据 | 前端 |
| 67 | 右侧栏关闭后无法恢复 | 关闭按钮 ❌ 后无重新打开入口 | 前端 |
| 68 | 组件图优化弹窗提示是类图内容 | placeholder 只判断 sequence vs else | 前端 |
| 69 | 类图标签显示 Untitled | Toolbar 标签逻辑不一致，类图用 d.name | 前端 |
| 70 | 全局优化空项目无输出 | `optimize_project` 空图不切生成模式，无日志 | 后端 |
| 71 | 全局优化 prompt 日志截断 | `full_system[:3000]` 和 `response[:5000]` 限制 | 后端 |
| 72 | 流式全局优化前端无显示 | `entityType` 解析把 `class:id` 整体当类型；idMap 未映射 | 前端 |
| 73 | 流式边找不到节点 | LLM生成ID与store自动ID不一致，relation 无法连接 | 前端 |
| 74 | 流式元素加到错误图 | 未先切换 active diagram，class 加到 sequence 图 | 前端 |
| 75 | 流式 attr/method 行丢失 | 前端只处理 class/relation/lifeline/component | 前端 |
| 76 | 工具栏过长右侧隐藏 | 单行 flex 无 wrap，按钮溢出 | 前端 |
| 77 | 流水线停止按钮不生效 | WebSocket 消息读取与流水线执行串行，没人读停止消息 | 后端/前端 |
| 78 | 停止后状态机未重置 | `stop_pipeline()` 不更新阶段状态，`_stopped` 残留 | 后端 |
| 79 | Stage 5b ReAct import 全失败 | 只传测试文件给 ReAct，缺少源文件模块依赖 | 后端 |
| 80 | 流水线 7→6 阶段 | 删除 TEST_EXEC，Stage 5 后加 ReAct 测试校验 | 后端/前端 |
| 81 | dev_review.txt 评审分散 | UML+用例审核分两处，跳过 UML 无记录 | 后端/前端 |
| 82 | uploads/ 反复生成 | 三处 `os.makedirs` + 上传后不清除 | 后端 |
| 83 | 流水线 UML 优化只优化类图 | Stage 1 调用 `optimize_uml(diagram)` 只传类图 | 后端/流水线 |
| 84 | 优化结果缺少 diagram_type 画布切换异常 | LLM 返回纯内容不含元数据，`setDiagram` 后 editor 分发到错误类型 | 前端 |
| 85 | 全局优化弹窗提示全是类图内容 | 流水线指令弹窗硬编码"类图包含 X 个类" | 前端 |
| 86 | `getState().diagram` 取到非类图导致画布串图 | 流水线事件处理拿当前激活图当 classOrig | 前端 |

---

## 问题1: 页面白屏 - AntV X6 自定义节点注册失败

**现象**: 前端 http://localhost:3000 打开后空白，没有任何内容显示。

**根因**: UMLEditor.tsx 中使用 `Node.registry.register('uml-class', { inherit: 'react-shape' })` 注册自定义节点，但 `react-shape` 是独立包 `@antv/x6-react-shape` 提供的，不在 `@antv/x6` 核心包中，导致注册失败、React 组件树崩溃。

**解决**: 
- 改用 `Graph.registerNode()` 在模块顶层注册（在创建 Graph 实例之前）
- 使用 `inherit: 'rect'` 作为基础形状
- 使用 `foreignObject` + `div` 标记渲染 HTML 内容
- 模式参考 X6 自带的 `text-block` 形状实现

**代码模式**:
```typescript
Graph.registerNode('uml-class', {
  inherit: 'rect',
  markup: [
    { tagName: 'rect', selector: 'body' },
    {
      tagName: 'foreignObject', selector: 'fo',
      children: [
        { tagName: 'div', ns: 'http://www.w3.org/1999/xhtml', selector: 'content' }
      ]
    }
  ],
  attrs: {
    body: { stroke: '#333', strokeWidth: 2, fill: '#fff', rx: 6, ry: 6, magnet: true },
    fo: { refWidth: '100%', refHeight: '100%' },
    content: { html: '' },
  },
});
```

---

## 问题2: 节点 HTML 内容不渲染（只能看到黑框）

**现象**: 类节点只有黑色矩形框，没有文字内容（类名、属性、方法等）。

**根因**: X6 v2.19.2 的 `graph.addNode()` 不支持直接传 `html` 属性（该属性在 TypeScript 类型中不存在，运行时也不生效）。需要使用 X6 的标准模式：在注册节点时定义 `foreignObject` + `div` 的 markup，然后通过 `attrs.content.html` 设置 HTML 内容。

**解决**:
- 在 `Graph.registerNode` 时正确配置 `foreignObject` 和 `div` 的 selector
- 创建节点时使用: `graph.addNode({ attrs: { content: { html: '...' } } })`
- 更新节点时使用: `node.setAttrByPath('content/html', htmlContent)`

---

## 问题3: 多个类堆叠在同一位置

**现象**: 第一个类创建正常，后续创建的类"看不到"（实际堆叠在相同坐标）。

**根因**: "添加类" 按钮的 onClick 使用固定坐标 `{ x: 200, y: 200 }`，所有新类在同一位置。

**解决**:
```typescript
onClick={() => {
  const x = 150 + Math.random() * 400;
  const y = 100 + Math.random() * 300;
  useDiagramStore.getState().addClass({ x, y });
}}
```

---

## 问题4: npm 包版本不兼容

**现象**: `npm install` 失败，报 `ETARGET` 或 `ERESOLVE` 错误。

**根因**:
- `@antv/x6-plugin-clipboard@^2.3.2` 版本不存在
- `@antv/x6-plugin-clipboard@3.0.0` 要求 `@antv/x6@^3.x`，与项目使用的 `@antv/x6@^2.18.1` 不兼容

**解决**:
- 使用 `@antv/x6-plugin-clipboard@^2.1.6`（最新 2.x 版本）
- 各插件版本锁定为实际可用的版本：
  - `@antv/x6`: ^2.18.1
  - `@antv/x6-plugin-history`: ^2.2.4
  - `@antv/x6-plugin-transform`: ^2.1.8
  - `@antv/x6-plugin-selection`: ^2.2.2
  - `@antv/x6-plugin-snapline`: ^2.1.7
  - `@antv/x6-plugin-clipboard`: ^2.1.6
  - `@antv/x6-plugin-export`: ^2.1.6
  - `@antv/x6-plugin-dnd`: ^2.1.1

---

## 问题5: TypeScript 类型错误

**现象**: 多个 TS 编译错误。

**具体错误及修复**:

| 错误 | 修复 |
|------|------|
| `snapline` does not exist in Graph options | 使用 `graph.use(new Snapline())` 插件代替，不在 Graph 构造中设置 |
| `keyboard` does not exist in Graph options | 改用 DOM `addEventListener('keydown', ...)` |
| `bindKey` does not exist on Graph | 同上，用 DOM 事件代替 |
| `GridOutlined` not exported from @ant-design/icons | 改用 `AppstoreOutlined` |
| `Export({ enabled: true })` Expected 0 arguments | 改为 `new Export()` 无参构造 |
| `drawGrid` 参数类型不匹配 | 使用 `(graph as any).drawGrid(...)` 类型断言 |

---

## 问题6: 端口圆点不可见

**现象**: 鼠标悬停在节点上时看不到连接端口。

**根因**: 
1. 未在 `Graph.registerNode` 中正确定义 ports（markup、items）
2. CSS 选择器无法匹配 X6 生成的端口 DOM 结构

**解决**:
- 在节点注册时明确定义 4 组端口（top/right/bottom/left），每组包含 `markup: [{ tagName: 'circle', selector: 'circle' }]`
- CSS 使用 `.x6-node .x6-port-body circle` 选择器控制端口显隐

**注册配置**:
```typescript
ports: {
  groups: {
    top: {
      position: { name: 'top' },
      markup: [{ tagName: 'circle', selector: 'circle' }],
      attrs: { circle: { r: 6, magnet: true, stroke: '#1890ff', strokeWidth: 2, fill: '#fff' } },
    },
    // ... right, bottom, left 类似
  },
  items: [
    { id: 'pt', group: 'top' },
    { id: 'pr', group: 'right' },
    { id: 'pb', group: 'bottom' },
    { id: 'pl', group: 'left' },
  ],
}
```

**CSS**:
```css
.x6-node .x6-port-body circle { opacity: 0; transition: opacity 0.15s; }
.x6-node:hover .x6-port-body circle { opacity: 1; }
```

---

## 问题7: 后端 Pandas 安装超时

**现象**: `pip install pandas==2.2.0` 从源码编译，过程缓慢超时。

**根因**: Python 3.14 操作系统较新，pandas 2.2.0 没有预编译的 wheel。

**解决**: 使用更宽泛的版本范围或安装有预编译 wheel 的版本。

---

---

## 问题8: LLM 优化 UML 返回 500 错误 — Pydantic 枚举校验失败

**现象**: 点击"优化设计"按钮后报错 `AxiosError: Request failed with status code 500`，后端返回 Pydantic ValidationError。

**根因**: DeepSeek LLM 返回的 JSON 数据不符合后端 Pydantic 模型的枚举约束：
1. `visibility` 字段使用 `"public"`/`"private"` 而不是 `"+"`/`"-"`
2. `stereotype` 字段为空字符串 `""`（LLM 新增类时未设置）
3. 关系 `type` 使用 `"generalization"` 而不是 `"inheritance"`
4. 关系字段名使用 `from`/`to` 而非 `source`/`target`
5. 关系缺少必填的 `id` 字段
6. 关系有多余字段如 `source_mult`、`label` 等

**解决**: 在 `code_generator.py` 中创建 `_normalize_llm_output()` 函数，在 LLM 返回结果传给 Pydantic 之前进行数据规范化：

```python
def _normalize_llm_output(data: dict) -> dict:
    """Normalize LLM output to ensure all enum values and field names match Pydantic model."""

    VIS_MAP = {"public": "+", "private": "-", "protected": "#", ...}
    VALID_STEREOTYPES = {"class", "interface", "abstract", "enum"}
    RELATION_TYPE_MAP = {"generalization": "inheritance", "extends": "inheritance", ...}

    FIELD_ALIASES = {
        "from": "source", "to": "target",
        "source_id": "source", "target_id": "target",
        "source_mult": "multiplicity_source", "target_mult": "multiplicity_target",
        "label": "role_name",
    }

    # Auto-generate missing IDs
    # Remap field aliases
    # Normalize enum values
```

**额外措施**:
- 所有 Pydantic 模型添加 `model_config = {"extra": "ignore"}` 忽略多余字段
- Prompt 中增加 CRITICAL RULES 明确要求 LLM 使用正确格式
- System prompt 强调 `Always use +, -, # for visibility values`

**Why:** LLM 输出格式不可控，需要防御性编程在数据进入 Pydantic 校验之前做容错处理。

**How to apply:** 任何调用 LLM 并解析结构化输出时，都需要类似的归一化层。

---

## 问题9: 后端端点500错误调式方法

**现象**: 前端显示通用 AxiosError 500，无法确定具体错误原因。

**根因**: FastAPI 端点异常被默认处理，只返回 `{"detail": "..."}` 简短信息。需要显式捕获并输出完整堆栈。

**解决**: 在端点上添加 `try/except` + `traceback.print_exc()`：

```python
@router.post("/optimize-uml", response_model=UmlOptimizeResponse)
async def optimize_uml_endpoint(req: UmlOptimizeRequest):
    import traceback
    try:
        ...
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")
```

调试流程：
1. 用 `curl` 发送最小 payload 直接测试后端端点
2. 用 Python 脚本模拟端点内的完整调用链路
3. 逐步增加 payload 复杂度定位问题

**Why:** 前端 axios catch 只能看到 500，后端日志才是调试关键。

---

## 问题10: 后端代码修改后未生效

**现象**: 修改了后端代码文件，但 API 行为未改变。

**根因**: `uvicorn --reload` 的热更新有时不可靠，特别是修改了导入的深层模块时可能不会触发热重载。

**解决**:
- 手动 `taskkill //F //IM python.exe` 杀死所有 Python 进程
- 重新启动 `python -m app.main`

**Why:** 热加载检测基于文件修改时间，但 Windows 上文件系统事件可能延迟。

---

---

## 问题11: 关系类型修改后画布不更新

**现象**: 在右侧属性面板修改连接的关系类型（如 association→composition），画布上的边标签和线型不变。

**根因**: UMLEditor 的 sync useEffect 中，边的同步代码 `if (existingEdgeIds.has(rel.id)) return;` 直接跳过了已存在的边，只添加新边不更新旧边。

**解决**: 将边的同步逻辑改为同时处理新增和更新：
```typescript
if (existingEdgeIds.has(rel.id)) {
  // Update existing edge: labels, dash style, arrow style
  const edge = graph.getCellById(rel.id) as Edge;
  edge.setLabels(labelText ? [labelText] : []);
  edge.setAttrByPath('line/strokeDasharray', isDashed ? '5,5' : '');
  edge.setAttrByPath('line/targetMarker/name', arrowStyle);
} else {
  // Add new edge
  graph.addEdge({...});
}
```

**Why:** 数据双向同步必须同时处理增删改，只处理增和删会导致编辑操作被静默忽略。

---

## 问题12: 网格开关无效、网格不显示

**现象**: 点击网格开关按钮后画布网格不变，有时从头到尾都看不到网格。

**根因**: 
1. Graph 初始化时 `grid` 选项缺少 `visible: true` 显式声明
2. 网格同步 useEffect 使用 `(graph as any).drawGrid()` 但参数格式在 X6 v2 中有严格要求
3. `drawGrid()` 的 `args` 参数在指定 `type: 'dot'` 时需要数组格式，不指定 type 时直接传对象

**解决**:
- 初始化: `grid: { size: 20, visible: true, args: { color: '...', thickness: 1 } }`（不指定type，使用默认dot preset）
- 同步: 使用 X6 原生方法 `graph.showGrid()` / `graph.hideGrid()` / `graph.setGridSize()`
- `drawGrid` 仅用于颜色/粗细变更，不传 type 参数简化路径

**X6 Grid API 关键方法**（运行时存在但不在 TS 类型中）:
- `graph.showGrid()` / `graph.hideGrid()` — 显隐切换
- `graph.setGridSize(n)` — 改变网格大小
- `graph.drawGrid(options)` — 重绘网格
- `graph.clearGrid()` — 清除网格

**Why:** X6 v2.19.2 的 TypeScript 类型定义不完整，grid 大部分方法需要在 `(graph as any)` 下调用。

---

## 问题13: 流水线 WebSocket 404 + REST 422

**现象**: 启动流水线时报：
- `GET /api/pipeline/pipe_xxx → 404`（pipeline 未找到）
- `POST /api/pipeline/create → 422`（请求体格式错误）

**根因**:
1. **404**: 前端 useEffect 在 WebSocket 连接之前就通过 REST 查询 pipeline 状态，但 pipeline 只在 WebSocket handler 内创建，此时尚不存在
2. **422**: FastAPI 端点 `create_pipeline_endpoint(req: PipelineCreateRequest, diagram: UmlDiagram)` 接收两个独立 Pydantic 模型参数，但前端发送 `{ diagram_id, ...diagram flat fields }` 扁平结构，FastAPI 无法正确解析

**解决**:
- **404**: 启动前先通过 REST 创建 pipeline，再开 WebSocket；useEffect 改为忽略 404 错误
- **422**: 创建组合请求模型 `CreatePipelineBody` 包含嵌套 `diagram` 字段
```python
class CreatePipelineBody(BaseModel):
    diagram_id: str
    auto_confirm: bool = False
    diagram: UmlDiagram
```
前端发送 `{ diagram_id, diagram: {...} }` 嵌套格式

**Why:** FastAPI 单个 JSON body 无法自动拆分为多个 Pydantic 参数，需要嵌套模型。

---

## 问题14: Vite WebSocket 代理未配置

**现象**: WebSocket 连接到 `ws://localhost:3000/api/pipeline/ws/{id}` 返回 404。

**根因**: Vite 代理配置中 `/api` 规则缺少 `ws: true`，导致 WebSocket 升级请求被当作普通 HTTP 处理。

**解决**: 在 vite.config.ts 的 `/api` 代理规则中添加 `ws: true`：
```typescript
proxy: {
  '/api': {
    target: 'http://localhost:8000',
    changeOrigin: true,
    ws: true,  // 允许 WebSocket 升级
  },
}
```
注意: Vite 配置修改后需要重启 dev server（`taskkill //F //IM node.exe`）。

**Why:** WebSocket 通过 HTTP Upgrade 机制建立连接，代理必须显式支持。

---

## 问题15: 画布上类节点位置堆叠

**(补充)** 节点同步也需要防止位置抖动。节点位置和尺寸更新应只在值实际变化时才调用 `setPosition`/`setSize`，避免每帧都触发 setter 导致性能问题。

---

## 问题16: Python Enum 属性大小写错误

**现象**: `type object 'StageName' has no attribute 'uml_optimize'` 运行时错误。

**根因**: Python `str, Enum` 类属性区分大小写。`StageName.UML_OPTIMIZE`（大写）是正确的，`StageName.uml_optimize`（小写）不存在。

**解决**: 统一使用 `StageName.UML_OPTIMIZE`、`StageStatus.SKIPPED` 等大写形式访问枚举成员。

**Why:** Python 的 Enum 成员名遵从 Python 变量命名规范（大写），即使值是小写字符串。

---

---

## 问题17: Excel 中文乱码 — Python UTF-8 模式

**现象**: 
- 终端运行 `python testHub/gen_testcases.py` 生成 Excel，中文 sheet 名称显示为乱码
- 后端 API 返回的 JSON 中中文变成代理对（`\udca7\udc88`）
- 前端加载测试用例文件后显示乱码

**根因**: Windows 中文版 Python 默认使用 GBK 编码（而非 UTF-8）。`gen_testcases.py` 文件中包含中文字符串（如"用例概览"），Python 在非 UTF-8 模式下解释为 GBK 乱码，写入 Excel 后数据永久损坏。

**解决**:
1. **Python 脚本**：首行添加 `# -*- coding: utf-8 -*-` 声明源文件编码
2. **启动 Python**：设置环境变量 `PYTHONUTF8=1` 强制 UTF-8 模式
3. **后端启动**：`start.bat` 中 `set PYTHONUTF8=1 & python -m app.main`
4. **验证方法**：检查原始 JSON 字节是否为合法 UTF-8 序列

```bash
# 正确启动方式
export PYTHONUTF8=1 && python testHub/gen_testcases.py
```

**注意**: `PYTHONUTF8` 必须在 Python 进程启动前设置，`os.environ.setdefault()` 在运行时设置无效。

**Why:** Python 3.7+ 引入了 UTF-8 模式（PEP 540），Windows 需要显式开启。否则 locale.getpreferredencoding() 返回 GBK。

---

## 问题18: GitHub push 认证失败

**现象**: 
```bash
$ git push -u origin master
remote: Invalid username or token.
Password authentication is not supported for Git operations.
fatal: Authentication failed
```

**根因**: GitHub 自 2021 年 8 月起废弃了密码认证，所有 Git 操作必须使用 SSH Key 或 Personal Access Token (PAT)。

**解决**:
- **SSH Key**（推荐）：`ssh-keygen` 生成密钥 → GitHub Settings → SSH Keys 添加公钥 → 远程地址改为 `git@github.com:user/repo.git`
- **PAT Token**：GitHub Settings → Developer settings → Personal Access Token → 生成 token → 远程地址 `https://token@github.com/user/repo.git`

```bash
# SSH 方式
git remote set-url origin git@github.com:Zhuweilong123/UML_design.git

# Token 方式
git remote set-url origin https://<token>@github.com/Zhuweilong123/UML_design.git
```

**Why:** 密码认证安全性不足，GitHub 强制使用更安全的认证方式。

---

## 问题19: 国内访问 GitHub 网络问题

**现象**: GitHub 网站打不开、加载极慢，但 `curl https://github.com` 返回 200。

**根因**: 国内网络对 GitHub 的 DNS 污染和间歇性阻断。有时能 ping 通但 web UI 加载超时，Git 命令行操作也可能失败。

**解决**:
1. **配置 Git 代理**（如有梯子）：
   ```bash
   git config --global http.proxy http://127.0.0.1:7890
   git config --global https.proxy http://127.0.0.1:7890
   ```
2. **使用 Gitee 码云**：国内镜像仓库，访问稳定
3. **修改 hosts**：添加 GitHub 的最新 IP 映射
4. **使用 gh CLI**：`winget install GitHub.cli` 后用 `gh auth login` 认证

**诊断命令**:
```bash
curl -s -o /dev/null -w "%{http_code}" https://github.com --connect-timeout 5
```

**Why:** GFW 策略对 GitHub 的阻断时强时弱，需要多种应对方案。

---

## 问题20: 流水线 Stage 4（用例检视）不暂停

**现象**: 自动化流水线到达 Stage 4 后不停留，直接跳过到 Stage 5。

**根因**: `resume_pipeline` 中的 Stage 4 代码为旧版，直接 `yield SUCCESS` 跳过了，没有像 `run_pipeline` 那样 yield `request_case_review` 并 return 暂停。

**解决**: 
- `resume_pipeline` 的 Stage 4 改为和 `run_pipeline` 一致：先 yield RUNNING，再 yield `request_case_review` 事件，最后 return 等待
- WebSocket handler 中所有可能到达 Stage 4 的路径（`skip_instructions`、`listen_for_commands`、main loop）都注册了 `wait_for_case_review()` 等待逻辑
- 确认后通过 `confirm_case_review` action 恢复，传 `skip_case_review=True` 跳过重复暂停

**Why:** 多条代码路径（提交优化/跳过/接受拒绝）都能到达 Stage 4，每条路径都需要注册对应的暂停逻辑。

---

## 问题21: 流水线完成后进度条停在 86%

**现象**: 所有阶段执行完毕，但进度条显示 86%（6/7），未到 100%。

**根因**: 
1. `resume_pipeline` 最后阶段（code_optimize）成功完成后，WebSocket 主循环自然退出，但未发送"完成"信号
2. WebSocket 关闭后前端 `onclose` 追加"(连接关闭)"文字

**解决**:
- 后端主循环结束后发送 `pipeline_complete` 事件
- 前端收到后设置 `completed=true`，进度条强制 100% 并变绿
- `ws.onclose` 对已完成/已停止的流水线不再追加"(连接关闭)"

---

## 问题22: 代码产物中测试代码不显示

**现象**: 流水线侧边栏"测试代码"始终显示"待生成"，但磁盘 `generated/test/` 目录下已有文件。

**根因**: `resume_pipeline` 的 Stage 5 代码调用了 `generate_tests()` 和 `_save_generated_files()`，但**没有将测试文件添加到 `pipeline.code_artifacts`**。只有 `run_pipeline` 的 Stage 5 有 append 逻辑。

**解决**: 在 `resume_pipeline` Stage 5 添加：
```python
for fname, content in test_files.items():
    pipeline.code_artifacts.append(CodeArtifact(
        language=language, filename=fname, content=content, version=2,
    ))
```
同时使用 `version=2` 标记测试产物，`version=1` 标记源代码产物，前端通过文件名前缀 `test_` 分类显示。

---

## 问题23: 源代码产物重复显示

**现象**: 代码产物中相同源文件出现两次。

**根因**: `resume_pipeline` 在两条路径中被调用：
1. 从 dev_confirm 恢复 → 跑 Stage 3（生成源代码）
2. 从 case_review 确认 → 又跑 Stage 3（再次生成源代码）

两次调用都会 `pipeline.code_artifacts.append()`，导致重复。

**解决**: 添加 `skip_code_gen` 参数：
- 从 dev_confirm 恢复时：`skip_code_gen=False`（首次生成源代码）
- 从 case_review 确认时：`skip_code_gen=True`（复用已有源代码，直接跳到 Stage 5）

**Why:** 流水线暂停-恢复机制中，恢复函数可能被多次调用，需要标志位跳过已完成的阶段。

---

## 问题24: 优化需求弹窗消失

**现象**: 流水线启动后不弹出优化需求输入框，直接跳过。

**根因**: 在 `ws.onmessage` 中添加 `request_case_review` 处理时，误复制了一行空的 `else if (data.event === 'request_instructions') {}`，导致真正的 `request_instructions` 处理逻辑被跳过。

**解决**: 删除重复的空行。

**Why:** 重复的 if-else 分支中 JS 只会进入第一个匹配的分支。

---

## 问题25: "检视完成，继续"按钮无响应

**现象**: Stage 4 暂停后点击"检视完成，继续"按钮，流水线不继续运行。

**根因**: `confirm_case_review` 消息通过 WebSocket 发送后，主循环已退出（因为 `break`），消息无人接收。多条到达 Stage 4 的路径中只有部分注册了接收逻辑。

**解决**: 
- 在 `wait_for_instructions`（skip_instructions handler）和 `listen_for_commands`（confirm handler）中都注册了 `confirm_case_review` 处理
- 统一调用 `resume_pipeline(skip_case_review=True, skip_code_gen=True)` 恢复流水线

---

## 问题26: ReAct 引擎工具执行 coroutine 未 await

**现象**: 工具调用返回值是 `<coroutine object>` 而不是实际结果字符串，引擎打印 `RuntimeWarning: coroutine was never awaited`。

**根因**: `Tool.execute` 是 lambda 包装的异步函数（如 `_validate_syntax`），lambda 返回的是 coroutine 对象但引擎中直接使用返回值，没有 await。

**解决**:
- `Tool` 新增 `async run(**kwargs)` 方法，内部处理 coroutine vs 普通值：
```python
async def run(self, **kwargs) -> str:
    result = self.execute(**kwargs)
    if asyncio.coroutines.iscoroutine(result):
        result = await result
    return str(result)
```
- 引擎统一调用 `await tool.run(**func_args)` 替代原来的直接调用

**Why:** Python 的 coroutine 必须显式 await，lambda 包装的 async 函数不会自动执行。

---

## 问题27: ReAct 引擎 DeepSeek API 400 错误

**现象**: 运行流水线后所有 ReAct 阶段报错：
```
Error code: 400 - "An assistant message with tool_calls must be followed
by tool messages responding to each tool_call_id"
```

**根因**: `deepseek-chat` 模型不兼容 OpenAI 原生 function calling 格式（`tools`/`tool_choice`/`tool_calls`消息）。消息格式不符合 DeepSeek 预期。

**解决**: 改为纯文本方式实现工具调用：
- LLM 输出使用 `THOUGHT:` + `ACTION:` + JSON 代码块格式
- 引擎用正则解析 `ACTION: xxx` 和 JSON 参数
- 工具结果作为普通 user 消息追加，不依赖特殊消息格式

**优势**: 兼容所有 LLM（DeepSeek/GPT/Claude），上下文文件可读性更好，调试更容易。

---

## 问题28: 优化仅1轮就停止（LLM 虚假完成）

**现象**: 跑完3轮优化但实际只有1轮有效，后两轮结果被覆盖。且 LLM 在测试仍有失败时自称"完成"。

**根因**: 
1. `finish_optimization` 的"成功"仅基于 LLM 声称，未重新验证
2. 每轮结果互相覆盖，最终流水线日志只显示最后一轮

**解决**:
- 每轮 ReAct 优化后强制执行 `_execute_tests` 重新分析测试结果
- 检查实际结果中是否有 `FAIL`，有则继续下一轮
- 用 `rounds_history` 列表累积每轮结果，日志中逐轮展示

---

## 问题29: 优化日志只记录最后一轮

**现象**: 流水线日志只显示最后一轮优化结果，无法对比各轮效果。

**解决**: 
- Stage 7 循环中新增 `rounds_history` 列表，每轮追加 `round_record`
- 记录每轮的 ReAct 步骤、通过/失败数、通过率、失败用例
- 日志中逐轮展示详情 + 优化效果对比表（轮次/通过/失败/通过率）

---

## 关键技术要点总结

1. **AntV X6 v2 自定义节点**: 使用 `Graph.registerNode()`（不是 `Node.registry.register()`），在创建 Graph 之前调用
2. **HTML 渲染**: 通过 `foreignObject` + `div` markup，使用 `attrs.content.html` 和 `node.setAttrByPath('content/html', ...)` 设置内容
3. **端口**: 在节点定义中配置 `ports` 属性，使用 CSS 控制显隐
4. **键盘快捷键**: 使用 DOM 事件而非 X6 的 `bindKey`
5. **撤销/重做**: 通过 History 插件 + Zustand 状态快照实现
6. **npm 兼容性**: 使用 `--legacy-peer-deps` 或精确版本号解决依赖冲突
7. **LLM 输出容错**: 必须对 LLM 返回的结构化数据做字段名映射、枚举值归一化、缺失字段填充
8. **Pydantic 防御**: 所有模型加 `model_config = {"extra": "ignore"}`，组合请求体用嵌套模型
9. **后端调试**: 端点加 try/except + traceback，先用 curl 隔离测试
10. **双向数据同步**: 增删改都要处理，不能只处理新增和删除
11. **X6 运行时 API**: 许多方法在 TS 类型定义中缺失，需要 `(graph as any)` 调用
12. **FastAPI 请求体**: 单个 JSON body → 单个 Pydantic 模型，多个参数需嵌套
13. **Vite 代理**: WebSocket 需要 `ws: true`
14. **Python Enum**: 枚举成员名大写
15. **Python 编码**: Windows 必须 `PYTHONUTF8=1`，脚本声明 `# -*- coding: utf-8 -*-`
16. **GitHub 认证**: 不再支持密码，必须用 SSH Key 或 PAT Token
17. **Git 代理**: 国内网络需 `git config http.proxy` 或换 Gitee
18. **流水线暂停-恢复**: 多条代码路径可能到达同一阶段，每条都要注册暂停/恢复逻辑
19. **skip 标志位**: 恢复函数要支持跳过已完成阶段，避免重复执行
20. **WebSocket 生命周期**: 流水线完成/暂停/错误时都要发送明确事件，不能让前端靠 onclose 推断状态
21. **LLM 兼容性**: 并非所有模型支持原生 function calling，文本格式更通用
22. **虚假完成检测**: 不能信任 LLM 声称"已完成"，必须用实际测试结果验证
23. **日志累积**: 迭代优化必须累积记录每轮结果，不能互相覆盖
24. **API Key 绝不可硬编码**: 所有密钥必须从环境变量/配置文件读取，代码默认值残留的真实 Key 会随 Git 历史永久泄露
25. **鉴权须在投产前实现**: 即使最简单的 Bearer Token 也比无鉴权好，可按需启用/跳过（本地开发留空）
26. **用户输入路径必须校验**: 所有文件操作端点的路径参数需用 `os.path.commonpath` + `os.path.realpath` 做沙箱限制
27. **Depends 嵌套有坑**: HTTPBearer 等类实例作为子依赖时 `request` 注入失败，改为直接从 Request 对象读取头
28. **WebSocket 不支持路由级 Depends**: 含 WebSocket 的路由需逐 HTTP 路由加 Depends，WebSocket 端点在函数内手工校验
29. **Windows asyncio 子进程用 to_thread**: `create_subprocess_exec` 在 ProactorEventLoop 下有兼容问题，`asyncio.to_thread(subprocess.run)` 更可靠

**Why:** 这些是项目开发中遇到的实际问题，记录了从设计文档到可运行代码的完整调试过程。

**How to apply:**
- 新节点类型参考问题1-2
- npm 版本兼容参考问题4
- X6 Grid API 参考问题12
- LLM 输出归一化参考问题8
- FastAPI 请求体设计参考问题13
- Python UTF-8 编码参考问题17
- 文件路径安全参考问题46
- API 鉴权参考问题44-45, 47-48
- asyncio 子进程参考问题49

---

## 问题30: 文件无法打开 — Vite 代理端口不匹配

**现象**: 点击"打开"按钮后文件列表为空，"加载文件列表失败"；所有 API 请求失败。

**根因**: `vite.config.ts` 代理 `/api` 到 `http://localhost:8001`，但后端在端口 `8000`。前端所有请求被代理到无进程端口。

**解决**: 改 `target` 为 `http://localhost:8000`。Vite 配置不支持 HMR，必须重启 dev server。

---

## 问题31: 生成的测试用例不包含用例ID

**现象**: 流水线 Stage 5 生成测试函数名为通用名（如 `test_initialization`），没有用例ID。

**根因（三层）**:
1. Sheet 污染: Excel 首个 Sheet "用例概览" 无"用例ID"列，旧代码 `headers[0]` 误取为ID
2. 列索引硬编码: `TestCaseViewer.tsx` 和 `testhub.py` 都用 `headers[0]/[1]/[2]/[4]/[5]` 硬编码
3. 提示词结构: `_build_test_prompt()` 源码放在用例需求之前，LLM 忽略用例ID

**解决**:
1. 前端 `findCol(headers, keywords)` 按关键词匹配列名，无ID列Sheet跳过
2. 后端 `_find_col()` 同理
3. 提示词重构: 用例需求置顶 + `TC-OTA-001 → def test_TC_OTA_001_xxx()` 强制映射示例 + 源码降级

---

## 问题32: 后端代码修改后不生效 — 僵尸进程

**现象**: 修改后端文件后流水线行为不变，新增日志不输出。

**根因**: 多个 Python 进程同时占 8000 端口，旧僵尸进程处理实际请求。`netstat -ano | findstr :8000` 显示两个 PID。

**解决**: `taskkill /F /IM python.exe` 全杀后重启。

---

## 问题33: Python 3.14 PYTHONUTF8 崩溃

**现象**: `Fatal Python error: preconfig_init_utf8_mode: invalid PYTHONUTF8 environment variable value`

**根因**: `set PYTHONUTF8=1` 在 Python 3.14 被拒绝。

**解决**: 改用 `python -X utf8`。同时修复 `start.bat`: `%~dp0` 替代硬编码、移除全杀进程、`npm run dev`。

---

## 问题34: Stage 6/7 LLM 模拟测试结果不可靠

**现象**: Stage 7 代码优化三轮，通过率在 4%~42% 随机跳变，有时负优化。

**根因**: `_execute_tests()` 把测试代码发给 LLM 让它"猜" PASS/FAIL。LLM 无 Python 运行时，结果完全随机。

**解决**: 用 `asyncio.create_subprocess_exec` 跑真实 `pytest -v --tb=short`，PYTHONPATH 确保 import。pytest 不可用时回退 LLM 模拟。

## 问题35: Stage 6 用 ReAct 盲修测试代码

**现象**: Stage 6 跑 2 轮 ReAct，大量 "No action parsed" 解析失败。

**根因**: ReAct action 解析（`_parse_action`）对 DeepSeek 格式匹配差；`diff_code` lambda 参数 bug。

**解决**: 去掉 `ReActEngine`/`ReActResult`。简化为：pytest → 提取编译错误 → LLM 一次性修复 → 重跑，最多2轮。失败阻断。

## 问题36: `Path(".").parent` 在 Python 3.13 不往上走

**现象**: "Test directory empty or missing"，文件明明存在。

**根因**: Python 3.9+ `Path(".").parent` 返回 `"."` 自身。`.parent.parent` 路径算错。

**解决**: 全部改为 `.resolve().parent.parent`，先展开绝对路径再取父目录。

## 问题37: `__pycache__` 导致改代码不生效

**现象**: 改后端代码重启后行为不变。

**根因**: `.pyc` 缓存优先于源码，修改 `.py` 后缓存未失效。

**解决**: `main.py` 加 `sys.dont_write_bytecode = True` 禁止生成 `.pyc`。

## 问题38: 测试结果截断导致数量不一致

**现象**: "47 通过 / 47 总计" 但 "全部通过 (39 个)"，数量矛盾。

**根因**: `new_test_results[:2000]` — 47 条 ~3055 字符被截到 2000。"47" 用预计算整数，"(39)" 从截断文本实时统计。

**解决**: 轮次记录去截断，Stage 6 展示上限提到 5000。

## 问题39: pytest 失败原因无法提取

**现象**: 日志只显示 "Test: xxx -> FAIL"，无失败原因。

**根因**: `_extract_failure_reason()` regex 不兼容 Windows `\r\n` 和 pytest `--tb=short` 的 `FAILURES` 段落。

**解决**: 三策略递进匹配 `E   ErrorType: message`，regex 全用 `\r?\n`。

---

## 新需求: 流水线日志文件级统计

Stage 7 每轮末尾加各文件 PASS/FAIL 表格。`_build_per_file_stats()` 按用例 ID 前缀（`TC_BASE`→BaseTask...）映射模块名，统计每文件通过率。

## 新需求: 后端日志加时间戳

`main.py` 配置 `logging.basicConfig(format="%(asctime)s | ...")` + 覆盖 uvicorn `LOGGING_CONFIG`。

## 新需求: UML 类图备注传递到代码生成

`_build_class_prompt` 加入 `c.note`（Business Rules）、关系元数据。`optimize_uml` 加规则保留 note。`_build_test_prompt` 加 `_extract_api_signatures()` 提取源码 API 签名。

---

## 问题40: 测试文件保存不清理旧文件导致残留

**现象**: 最新生成的测试用例是有问题的（LLM JSON 解析失败，原始响应被存成 .py），但 pytest 竟然跑起来了，显示 46 PASS。

**根因**: 
1. `generate_tests()` 的 `except JSONDecodeError` fallback 把 LLM 原始 JSON 响应存为 `test_main.py`（内容以 ```json 开头，不是合法 Python）
2. `_save_generated_files()` 只写新文件不删旧文件，上次残留的有效 `.py` 文件被 pytest 收集执行

**解决**: 
1. `generate_tests`/`generate_code` 的 JSON 解析失败 fallback 改为返回空 `{}`，记录 warning 日志
2. `_save_generated_files` 写测试文件前 `shutil.rmtree(test_dir)` 清空目录
3. `generate_tests`/`generate_code` 返回空时 Stage 3/5 标 FAILED 并阻断
4. `_execute_tests` 目录为空时直接报错，不用 LLM 模拟

**Why:** 三条防线确保：要么成功生成、要么明确失败，不存在"看起来跑了其实是旧数据"的假象。

## 问题41: 最终测试结果和第三轮不一致

**现象**: 流水线日志 Round 3 显示 49% 通过率，但"最终测试结果"显示 21%。

**根因**: Stage 7 的 for-else 结构中，`else` 分支在 3 轮循环结束后额外调用了一次 `_execute_tests`，将 `pipeline.stages[5].result["test_results"]` 覆盖为新值。这次额外的 pytest 调用结果与第三轮不同（21% vs 49%），导致 `_save_pipeline_log` 读到的最终数据被污染。

**解决**: 删除 `else` 分支中多余的 `_execute_tests` 调用。"最终测试结果"直接用最后一轮的数据，不需要额外跑一次 pytest。

**Why:** for-else 被误用为"兜底调用"，但数据已在循环内正确保存，else 的重复调用反而覆盖了正确结果。

## 问题42: 前端流水线完成后转圈不停 + 进度条不到100%

**现象**: Stage 7 全部完成后 Loading 图标一直转；进度条停在 86% 不达 100%。

**根因**: 
1. `running`/`completed` 是独立 state，靠 WS 事件回调手动切换。`pipeline_complete` 将 `manualRunning` 置 false 后，`completed = manualRunning && allStagesTerminal` 跟着变 false
2. `completedStages` 只计 `SUCCESS` 不计 `SKIPPED`/`FAILED`（Stage 1 被跳过 → 6/7 = 86%）

**解决**: `running` 和 `showCompleted` 从 pipeline 阶段状态直接推导，不依赖 `manualRunning`。`completedStages` 计入 `SKIPPED`/`FAILED`。

**Why:** 派生状态（derived state）优于手动维护的独立状态，避免事件时序问题导致的不一致。

## 问题43: 后端日志缺少时间戳

**现象**: 后端 uvicorn 和 logging 输出没有任何时间信息。

**根因**: uvicorn 默认日志格式和 Python `logging` 默认格式都不含 `%(asctime)s`。

**解决**: `main.py` 配置 `logging.basicConfig(format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")` + 覆盖 uvicorn `LOGGING_CONFIG` 的 formatter。

**Why:** 无时间戳导致无法追踪问题发生时间、无法关联流水线日志和后端日志。

---

## 问题44: API Key 硬编码在 config.py 默认值中

**现象**: Git 仓库历史中包含真实的 DeepSeek API Key `sk-3b6b0eaa3b374234a8047e0c60844b24`。

**根因**: `backend/app/core/config.py` 中 `deepseek_api_key: str = "sk-3b6b0eaa..."` 写死了真实 Key 作为默认值。即使 `.env` 被 `.gitignore` 排除，代码本身被提交到 Git 后 Key 永久暴露在历史记录中。

**解决**:
1. 移除默认值，改为 `Field(...)` 强制从 `.env` 读取（无 .env 时启动即报错，不会静默用旧 Key）
2. 添加 `field_validator` 检测已知泄露的前缀（`sk-3b6b0eaa` 等），命中则打印 WARNING
3. **需要手动去 DeepSeek 后台轮换 Key**（代码修复不会自动撤销已泄露的 Key）

```python
# 修复前
deepseek_api_key: str = "sk-3b6b0eaa..."  # ❌ 硬编码

# 修复后
deepseek_api_key: str = Field(..., description="必须从 .env 读取")  # ✅ 无默认值
```

**涉及文件**: `backend/app/core/config.py`

**Why:** 这是 P0 安全问题，泄露的 API Key 可被任何人用于调用 DeepSeek API，产生费用。

---

## 问题45: 全端点无鉴权

**现象**: 所有 API 端点（LLM 代码生成/聊天、流水线操作、文件写入）无需任何认证即可访问。

**根因**: 项目设计阶段未实现认证层，直接暴露所有端点。任何人知道服务地址就能调用 DeepSeek API 消耗配额。

**解决**: 新建 `backend/app/core/auth.py` 实现 Bearer Token 鉴权：

```python
async def require_auth(request: Request):
    settings = get_settings()
    if not settings.internal_api_token:
        return  # 本地开发：未配置则跳过
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        raise HTTPException(401)
    if not hmac.compare_digest(token, settings.internal_api_token):
        raise HTTPException(403)
```

**受保护端点**: `/api/llm/*`（路由级）、`/api/pipeline/*`（逐路由）、`/api/testhub/*`（路由级）、`/api/files/(save|upload|save-review|save-generated)`（逐路由）

**不受保护**: `/api/files/(list|open|browse|new|export)`（可公开读取）、`/api/health`

**配置**: 后端 `.env` 设 `INTERNAL_API_TOKEN=xxx`，前端 `.env.local` 设 `VITE_API_TOKEN=xxx`（两值相同）。都留空则鉴权跳过。

**涉及文件**: `backend/app/core/auth.py`, `backend/app/main.py`, `backend/app/api/files.py`, `backend/app/api/pipeline.py`, `frontend/src/services/api.ts`

**Why:** 防止未授权访问消耗 LLM API 配额或篡改文件。

---

## 问题46: 文件端点目录遍历漏洞

**现象**: `/api/files/open?filepath=/etc/passwd` 可读取系统任意文件；`/api/files/browse?path=../../` 可遍历任意目录。

**根因**: `open_file` 和 `browse_directory` 直接使用用户传入的路径参数，未做任何安全检查。

**解决**:
1. 新增 `_safe_path()` 函数：将用户路径解析后与项目根目录做 `os.path.commonpath` 前缀检查，不在项目内则返回 403
2. `open_file` 额外限制只能打开 `.uml` 文件
3. `upload_excel` 用 `os.path.basename()` 剥离路径 + 拒绝 `.` 开头的隐藏文件
4. `save_file` 用 `_sanitize_path_segment()` 净化文件名中的 `../` 和特殊字符
5. `save_generated_code` 对 `project_name` 和 `language` 字段做相同净化

```python
def _safe_path(user_path: str) -> str:
    project_root = os.path.abspath(os.path.join(settings.uml_dir, "..", ".."))
    candidate = os.path.abspath(os.path.join(project_root, user_path))
    real = os.path.realpath(candidate)  # 解析符号链接
    if os.path.commonpath([real, os.path.realpath(project_root)]) != os.path.realpath(project_root):
        raise HTTPException(403)
    return real
```

**涉及文件**: `backend/app/api/files.py`

**Why:** 不限制路径范围会暴露整个文件系统，攻击者可读取配置文件（含 API Key）、系统文件等敏感信息。

---

## 问题47: FastAPI HTTPBearer 子依赖注入失败

**现象**: 启动后端后流水线相关请求报 `TypeError: HTTPBearer.__call__() missing 1 required positional argument: 'request'`。

**根因**: 最初用 `HTTPBearer` 类实例作为子依赖：
```python
_security = HTTPBearer(auto_error=False)
async def get_token(creds = Depends(_security)):  # ❌ 子依赖中 request 注入失败
    ...
```
FastAPI 在处理 `Depends` 嵌套时，`HTTPBearer.__call__()` 需要的 `request` 参数无法自动注入到子依赖中。

**解决**: 放弃 `HTTPBearer`，直接从 `Request` 对象读取 `Authorization` 头：
```python
async def require_auth(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ")
    ...
```

**涉及文件**: `backend/app/core/auth.py`

**Why:** FastAPI 的 `Depends` 嵌套有局限性，类实例的 `__call__` 作为子依赖时参数解析路径与顶层不同。

---

## 问题48: WebSocket 路由级 Depends 与 HTTP 不兼容

**现象**: `Depends(require_auth)` 在包括 WebSocket 端点的路由上使用时，WebSocket 握手阶段报 `TypeError: require_auth() missing 1 required positional argument: 'request'`。

**根因**: FastAPI 的 `Depends` 依赖注入机制是为 HTTP 端点设计的，WebSocket 端点使用不同的依赖解析路径。路由级 `dependencies=[Depends(require_auth)]` 会对该路由下所有端点（包括 WebSocket）生效，导致 WebSocket 端点尝试注入 `request` 时失败。

**解决**: 
1. 从路由级移除 `Depends`
2. 为每个 HTTP 路由单独添加 `@router.post("...", dependencies=[Depends(require_auth)])`
3. WebSocket 端点用手工校验：`if not hmac.compare_digest(token, settings.internal_api_token): ws.close()`

**涉及文件**: `backend/app/main.py`, `backend/app/api/pipeline.py`

**Why:** WebSocket 和 HTTP 是两套不同的 ASGI 协议，FastAPI 的依赖注入对两者的支持程度不同。

---

## 问题49: Windows asyncio.create_subprocess_exec NotImplementedError

**现象**: 流水线 Stage 6 执行测试时报 `NotImplementedError`，回退到 LLM 模拟测试（凭空猜 PASS/FAIL），导致测试结果不准确、优化方向错误。

**根因**: Windows 默认的 `ProactorEventLoop` 不完全支持 `asyncio.create_subprocess_exec`，某些场景下抛 `NotImplementedError`。这是 Python asyncio 在 Windows 上的已知限制。

**解决**: 用 `asyncio.to_thread()` + 同步 `subprocess.run()` 替代：
```python
# 修复前
proc = await asyncio.create_subprocess_exec(*cmd, ...)  # ❌ Windows 报错

# 修复后
result = await asyncio.to_thread(subprocess.run, cmd, ...)  # ✅ 线程池中同步执行
```

**日志区分**: 
- 真实 pytest: `[TestExec] ========== Running real pytest ==========`
- LLM 模拟: `[TestExec] ========== LLM SIMULATION MODE (not real pytest!) ==========`（WARNING 级别，醒目分隔线）

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** 真实 pytest 执行是流水线 Stage 6/7 的核心，LLM 模拟结果不可靠，会导致代码优化方向完全错误。

---

## 问题50: `__pycache__` 残留导致 pytest 0 tests collected

**现象**: 流水线 Stage 6 报 `import file mismatch: imported module has __file__ at old/path which is not the same as new/path`，collect 0 个测试。

**根因**: 旧测试在 `generated/test/Untitled/python/` 被删除，但 `__pycache__` 残留的 `.pyc` 仍然指向旧路径。pytest 的 import 缓存优先命中 `.pyc`，发现路径不匹配就报错跳过。

**解决**: 
1. `_execute_tests` 跑 pytest 前自动遍历测试目录和源码目录，删除所有 `__pycache__/` 子目录
2. 手动清理旧 `generated/test/Untitled/` 和 `generated/src/Untitled*/` 目录

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** Python 的 `.pyc` 缓存记录了文件的绝对路径，文件迁移后缓存失效但不会被自动清理。

---

## 问题51: Stage 5 JSON 解析失败导致流水线中断

**现象**: `generate_tests` 报 "LLM returned no valid test files"，Stage 5 FAILED。

**根因（两层）**:
1. `generate_tests`、`adapt_code_to_uml`、`update_tests_incremental` 调用 LLM 时**没有** `json_mode=True`，LLM 可能返回解释文字+JSON 混排
2. `clean_llm_json_response` 只能处理以 ` ``` ` 开头的响应，处理不了文本中间夹 JSON 的情况

**解决**:
1. 三个函数全部加上 `json_mode=True`
2. `clean_llm_json_response` 重写：三层回退 — ` ```json ``` ` 代码块 → ` ``` ``` ` 通用代码块 → 括号配对提取 `{...}`

**涉及文件**: `backend/app/services/code_generator.py`, `backend/app/services/tools.py`

**Why:** `json_mode` 是 DeepSeek 的强制结构化输出开关，不开则 LLM 输出格式不可控。

---

## 问题52: Stage 7 三轮优化完全无改善

**现象**: 3 轮优化后通过率完全不变，失败的用例始终是同样的几个。

**根因**: `_optimize_source_from_tests` 的 prompt 只传了失败行（如 `AttributeError: module does not have 'some_func'`），LLM 看不到测试代码，猜不出缺失函数的签名和语义，只能反复改一些无关代码。

**解决**: prompt 中加入完整测试代码（上限 4000 字符），LLM 能看到测试怎么调用缺失函数，就能正确添加。同时加 `json_mode=True`。

**核心原则**: Stage 7 只改源码、不改测试 — LLM 可以通过测试代码了解 API 期望，但不能修改测试文件。

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** 只看报错不看测试，等于蒙着眼睛修代码。

---

## 问题53: Stage 6 误拦截对象级 AttributeError

**现象**: `'CrowTask' object has no attribute 'clear_scheduled_crow'` 被 Stage 6 当做编译错误拦截并尝试修复，但 LLM 修了 2 轮改不好，Stage 6 FAILED 阻断流水线。

**根因**: `_extract_fatal_errors` 的 fatal 关键词包含 `"has no attribute"`，不区分模块级和对象级。模块级（`module 'X' has no attribute`）是编译错误该修；对象级（`'Foo' object has no attribute`）是源码缺方法，应交给 Stage 7。

**解决**: 精炼关键词 — 排除 `' object has no attribute`，保留 `"does not have the attribute"`（模块级）和 `module` 相关模式。

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** Stage 6 的职责是"让代码能跑起来"，不是"让代码逻辑正确"。

---

## 问题54: 未选目录时路径回退到旧嵌套结构

**现象**: 用户选 `generated/test` 作为测试目录，但 `generated/test/Untitled/python/` 下又生成了新文件。

**根因（多处）**:
1. `_save_generated_files` 的 Stage 6 保存调用漏了 `target_test_dir` 参数
2. 默认保存路径用了 `generated/{src,test}/{project}/{language}/` 嵌套结构，与用户选择扁平 `generated/{src,test}/` 时不统一

**解决**:
1. 补上漏掉的 `target_test_dir=test_dir` 参数
2. 将默认路径统一为 `generated/src/` 和 `generated/test/`（扁平），去掉 project/language 子目录

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** 路径不一致导致文件散落多处，pytest 可能收集不到或收集错误的文件。

---

## 问题55: 目录选择刷新后丢失

**现象**: 每次页面刷新或关闭重开，源码/测试目录都要重新选择。

**根因**: `pipelineSourceDir` 和 `pipelineTestDir` 只存在 Zustand 内存中，刷新即丢失。

**解决**: 默认值从 `localStorage` 读取，setter 同步写入 `localStorage`。目录选择框从"仅空状态可见"改为"非运行状态始终可见"。

**涉及文件**: `frontend/src/stores/uiStore.ts`, `frontend/src/components/PipelineConsole/PipelineConsole.tsx`

**Why:** 开发场景下浏览器经常需要重启，目录选择应该持久化。`localStorage` 是最简单的持久化方案。

---

## 问题56: 时序图消息 Y 位置保存后丢失

**现象**: 保存 `.umlproj` 重新打开后，消息连线的垂直位置变化。

**根因**: 消息 Y 坐标由 `MSG_Y_BASE + msg.order * 40` 计算。删除中间消息后 `order` 值出现间隔（如 1, 3, 5），重开时 Y 计算跳变。

**解决**: 消息 Y 改为按 `order` 排序后的**索引位置**计算（`MSG_Y_BASE + (idx+1) * 40`），确保 Y 始终连续。后续进一步加 `msg.y` 显式持久化字段，创建时计算并存下 Y，拖动后 `edge:change:source/target` 事件更新 Y。

**涉及文件**: `frontend/src/components/Canvas/SeqEditor.tsx`, `frontend/src/types/sequence.ts`, `frontend/src/stores/diagramStore.ts`

**Why:** `order` 是逻辑序号不能改，但 Y 位置需要紧凑连续显示。

---

## 问题57: 时序图消息连线切换视角后位置丢失

**现象**: 时序图→类图→时序图切换后，消息连线的拖动位置丢失回默认。

**根因**: 消息拖动后新 Y 坐标保存在 `msg.y` 字段中，但切换视角时 SeqEditor 组件重新 mount，依赖 `key={activeIdx}` 强制重建。原 `setDiagram`（legacy）未保留其他图数据。

**解决**: 
1. `SeqMessage` 新增 `y` 字段显式持久化 Y 坐标
2. App.tsx 给编辑器加 `key={activeIdx}` 确保切换时强制重建
3. `setDiagram` 改为用 `_updateActiveDiagram` 只更新当前活动图，不覆盖其他图

**涉及文件**: `frontend/src/components/Canvas/SeqEditor.tsx`, `frontend/src/App.tsx`, `frontend/src/stores/diagramStore.ts`

**Why:** 组件销毁重建时必须显式持久化位置数据，否则默认公式重算会覆盖拖动结果。

---

## 问题58-59: 优化设计接受后类图消失

**现象**: 时序图优化接受结果后，项目中的类图对象直接消失。

**根因**: `setDiagram`（legacy 函数）把 `project.diagrams` 替换为 `[diagram]` 单元素数组：`const project = { ...get().project, diagrams: [diagram], active_diagram_index: 0 }`。接受优化时 DiffViewer 调用 `setDiagram(optimizedDiagram)`，所有其他图被清除。

**解决**: `setDiagram` 改为用 `_updateActiveDiagram` 只更新当前活动图，保留 `project.diagrams` 中的其他图不变。

**涉及文件**: `frontend/src/stores/diagramStore.ts`, `frontend/src/components/DiffViewer/DiffViewer.tsx`

**Why:** 任何"更新当前图"的操作都应该用 `_updateActiveDiagram` 而非整体替换。

---

## 问题60: 优化设计弹窗始终显示类图提示

**现象**: 时序图视角点击"优化设计"，弹窗标题和提示仍显示"类图包含 X 个类"，placeholder 仍是类图示例。

**根因**: Toolbar 优化弹窗、DiffViewer 面板均硬编码了类图相关文本，未根据 `diagram_type` 切换。

**解决**:
1. Toolbar 优化弹窗标题/描述/placeholder 按 `diagram_type` 动态切换
2. DiffViewer 标题改为 `originalDiagram?.diagram_type === 'sequence' ? '时序图优化对比' : 'UML 优化对比'`
3. 后端 `optimize_uml` 根据 `diagram_type` 使用不同 prompt 和校验规则

**涉及文件**: `frontend/src/components/Toolbar/Toolbar.tsx`, `frontend/src/components/DiffViewer/DiffViewer.tsx`, `backend/app/services/code_generator.py`

**Why:** 多图类型的 UI 文本必须根据 `diagram_type` 动态切换，不能硬编码单一类型。

---

## 问题61: 打开已保存项目再保存生成新文件

**现象**: 打开 `.umlproj` → 修改 → Ctrl+S 保存，生成了一个新的时间戳文件而非覆盖原文件。

**根因**: `handleSave` 调用 `saveProject(project)` 未传 `currentFilepath`；后端 `save_project` 无 filepath 时自动生成新文件名。

**解决**: 前端 `handleSave` 传 `currentFilepath`；后端 `save_project_endpoint` 判断参数含路径分隔符时当完整路径用（`safe_path` 校验后直接写入）。

**涉及文件**: `frontend/src/components/Toolbar/Toolbar.tsx`, `backend/app/api/files.py`

**Why:** 编辑器保存逻辑必须支持"覆盖已打开文件"。

---

## 问题62: 时序图生命线 x 坐标变成负值

**现象**: 时序图打开后只能看到最右边一条生命线，左边生命线在屏幕外。日志显示 `Lifeline x: [-320, 96.5]`。

**根因**: 双击创建生命线时使用 `graph.clientToLocal(x, y)` 转换坐标，React StrictMode 下组件双 mount 导致 X6 graph 实例状态异常，`clientToLocal` 返回了错误的负数坐标。

**解决**:
1. 双击创建改为直接用屏幕坐标 `e.clientX - rect.left - 70`（不经过 `clientToLocal`）
2. sync 时自动检测 `x < 50` 的生命线并修正到 150-450 范围

**涉及文件**: `frontend/src/components/Canvas/SeqEditor.tsx`

**Why:** X6 的 `clientToLocal` 在 StrictMode 双 mount 场景下不可靠，直接使用 DOM 坐标更安全。

---

## 问题63: 浏览 API 不显示 .umlproj 文件

**现象**: 文件打开对话框只能看到 `.uml` 文件，看不到 `.umlproj` 项目文件。

**根因**: `browse_directory` 端点只过滤 `name.endswith(".uml")`。

**解决**: 同时过滤 `.uml` 和 `.umlproj`，增加 `type: "project" | "diagram"` 字段区分。前端文件对话框按 type 分类显示（📦 项目文件在前，📄 单图文件在后）。

**涉及文件**: `backend/app/api/files.py`, `frontend/src/components/Toolbar/Toolbar.tsx`

**Why:** 引入 `.umlproj` 格式后文件浏览必须同时支持新旧格式。

---

## 问题64: 组件图不支持大组件嵌套小组件

**现象**: 组件图只能创建平级组件，无法描述"大组件包含子组件"的架构。

**根因**: `CompNode` 无 `parent_id` 字段，CompEditor 未使用 X6 embedding。

**解决**: `CompNode` 新增 `parent_id`、`width`、`height`。双击组件内部创建子组件（自动 embed）；父组件拖动时子组件跟随。CSS 区分父（橙黄实线）vs 子（白底虚线）。

**涉及文件**: `backend/app/models/uml.py`, `frontend/src/types/component.ts`, `frontend/src/components/Canvas/CompEditor.tsx`, `CompEditor.css`

**Why:** 标准 UML 组件图支持嵌套，大组件可包含子组件展示内部结构。

---

## 问题65: Ctrl+C/V 复制对象后尺寸丢失

**现象**: 调整过大小的组件/类，Ctrl+C→Ctrl+V 后副本恢复默认尺寸。

**根因**: 粘贴时 `addComponent`/`addClass` 用默认参数创建新对象，未传入剪贴板中的 `width`/`height`/`size`。

**解决**: 粘贴后立即用 `updateComponent`/`updateClass` 把剪贴板中所有属性（尺寸、接口、方法、备注）覆盖到新对象。

**涉及文件**: `frontend/src/components/Canvas/UMLEditor.tsx`, `SeqEditor.tsx`, `CompEditor.tsx`

**Why:** 复制操作应完全保留源的视觉和逻辑属性。

---

## 问题66: 网格在时序图和组件图不生效

**现象**: 工具栏网格开关只能控制类图的网格显隐，切换到时序图/组件图后网格不受控。

**根因**: SeqEditor/CompEditor 创建 X6 graph 时网格参数硬编码（`grid: { size: 20, visible: true }`），未从 `diagram.grid_*` 读取，也没有 grid sync effect 响应工具栏变更。

**解决**: 初始化时从 `useDiagramStore.getState().diagram` 读取网格参数；增加独立的 `useEffect` 监听 `grid_visible`/`grid_size`/`grid_color`/`grid_thickness` 变化并更新 X6 graph。

**涉及文件**: `frontend/src/components/Canvas/SeqEditor.tsx`, `CompEditor.tsx`

**Why:** 所有图类型应共享统一的视图设置。

---

## 问题67: 右侧栏关闭后无法恢复

**现象**: 点击右侧面板 ❌ 关闭后，没有入口重新打开。

**根因**: 关闭逻辑只有 `toggleRightPanel`，无反向入口。

**解决**: 当 `rightPanelVisible === false` 时在右上角显示蓝色浮动圆形按钮（齿轮图标），点击重新打开面板。

**涉及文件**: `frontend/src/App.tsx`

**Why:** 可逆操作必须有双向入口。

---

## 问题68: 组件图优化弹窗提示未适配

**现象**: 组件图视角点"优化设计"，placeholder 仍显示"将 User 和 Order 改为聚合关系"等类图示例。

**根因**: Toolbar optimize modal 的 placeholder 只有 `sequence ? ... : class` 二元判断。

**解决**: 增加 `component` 分支，placeholder 改为"将 AuthService 拆分为 AuthProvider 和 TokenManager"等组件图示例。后端 `optimize_uml` 增加 `dt == "component"` 分支。

**涉及文件**: `frontend/src/components/Toolbar/Toolbar.tsx`, `backend/app/services/code_generator.py`

**Why:** 三种图类型的 UI 文本必须全覆盖。

---

## 问题69: 类图标签显示 "Untitled"

**现象**: Project 中类图 Tab 显示 "Untitled"，时序图/组件图显示"时序图"/"组件图"。

**根因**: Toolbar 标签逻辑对 sequence/component 硬编码类型名，对 class 直接用 `d.name`（默认 "Untitled"）。

**解决**: 统一规则：自动生成的名字（`/^(class|sequence|component)_\d+$/` 或 `Untitled`）显示类型标签，用户改过的名才显示自定义名。tooltip 同时显示原名和类型。

**涉及文件**: `frontend/src/components/Toolbar/Toolbar.tsx`

**Why:** 多图 Project 中标签命名应一致。

## 问题44: ReAct Engine 升级为原生 Function Calling

**现象**: 原 ReAct 引擎使用文本正则解析 THOUGHT/ACTION/```json 格式，DeepSeek 格式匹配不稳定，导致大量 "No action parsed" 解析失败。Stage 6 曾因此废弃 ReAct。

**根因**: 文本解析依赖正则 + JSON 提取，LLM 输出格式不可控。`_parse_action` / `_extract_code` 匹配率低。

**解决**: 重写 ReActEngine 使用 DeepSeek 原生 Function Calling（OpenAI 兼容 `tools` 参数）。LLM 的 `tool_calls` 以结构化 JSON 返回，无需文本解析。新增 `chat_with_tools()` 函数发送 `tools` 参数并解析 `response.choices[0].message.tool_calls`。工具结果以 `{"role": "tool", "tool_call_id": ..., "content": ...}` 格式回传给 LLM。同时将 `validate_syntax` 工具参数从 `code: string` 改为 `code_files: object`，与 `check_imports`/`run_module` 保持一致，LLM 一次调即可验证全部文件。

**涉及文件**: `backend/app/services/react_engine.py`, `backend/app/services/llm_service.py`, `backend/app/services/tools.py`

**Why:** 原生 Function Calling 比文本解析稳定得多，且支持 strict mode 保证参数正确性。

## 问题45: LLM 返回多个 tool_calls 导致 400 错误

**现象**: ReAct 第二轮报 `Error code: 400 - "An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id' (insufficient tool messages)"`.

**根因**: 原始代码只处理 `tool_calls[0]`（第一个工具调用），但往 messages 里写入了全部的 `tool_calls` N 个声明，却只回复了 1 个 `role: "tool"` 消息。DeepSeek API 校验 N 个 tool_call_id 必须对应 N 个 tool 回复。

**解决**: 改为遍历全部 `tool_calls`，逐个执行工具，逐个收集结果。同轮合并为一个 `ReActStep`（action 逗号拼接、observation 分行带 `[tool_name]` 前缀），一次性写入 1 条 assistant 消息 + N 条 tool 回复。

**涉及文件**: `backend/app/services/react_engine.py`

**Why:** 必须遵守 OpenAI Function Calling 协议——每个 tool_call_id 都要有对应回复。

## 问题46: 流式 ReAct 循环忘记设置 finish_triggered

**现象**: Round 4 LLM 调了 `finish_optimization`，但流水线继续跑 Round 5 空响应，最终 "Incomplete after 5 rounds"。

**根因**: `_run_loop_with_tools_stream` 是手动编写的新函数，for 循环内匹配到 `finish_optimization` 时只赋值了 `finish_code`/`finish_summary`，漏写 `finish_triggered = True`。导致循环末尾的 `if finish_triggered:` 条件永远为 False，永不退出。

**解决**: 在 `finish_optimization` 匹配分支首行补上 `finish_triggered = True`。

**涉及文件**: `backend/app/services/react_engine.py`

**Why:** 手动复制循环逻辑时最容易漏初始化和状态标志。

## 问题47: listen_for_commands 0.5 秒超时导致流水线"假完成"

**现象**: Stage 1 UML 优化完成 → Stage 2 等待确认 → 前端直接显示"流水线执行完成"，没有弹出确认对话框。

**根因**: `listen_for_commands()` 中 `asyncio.wait_for(ws.receive_text(), timeout=0.5)` 设置了 0.5 秒超时。用户未在 0.5 秒内确认 → `TimeoutError` → 函数静默返回 → `break` 退出 `async for` 循环 → 执行 `ws.send_json({"event": "pipeline_complete"})`。

**解决**: 去掉 `asyncio.wait_for` 包装，改为 `await ws.receive_text()` 永久阻塞直到用户消息到达。

**涉及文件**: `backend/app/api/pipeline.py`

**Why:** 命令监听本应阻塞等待，超时返回是逻辑错误。

## 问题48: confirm_stage 收到字符串而非 StageName 枚举

**现象**: 修复 timeout 后，用户点击确认时 WS 连接中断，报 `'str' object has no attribute 'value'`。

**根因**: 问题47 的 timeout 让确认路径从未被执行过，该潜伏 bug 一直存在。`listen_for_commands` 里 `msg.get("stage", "dev_confirm")` 返回字符串，直接传给 `confirm_stage(stage=...)`，函数内部调用 `stage.value` 炸了（字符串无 `.value` 属性）。

**解决**: 调用方用 `StageName(msg.get("stage", "dev_confirm"))` 包装为枚举。

**涉及文件**: `backend/app/api/pipeline.py`

**Why:** WS 消息中的值与后端枚举之间没有自动转换，需要手动包装。

## 问题49: Stage 3 代码生成无验证看护

**现象**: Stage 3 代码生成仅检查 `current_code` 是否为空，不验证语法/导入/运行正确性。LLM 产出 SyntaxError/ImportError 代码到 Stage 6 才暴露，且 Stage 6 只修测试代码不修源码。

**解决**: 在 Stage 3 代码生成后插入 ReAct 验证门（max 5 轮）。工具：`check_imports`（ast.parse + subprocess 导入）、`run_module`（子进程导入主模块）、`check_change_ratio`（已有项目变更比例检查）。验证通过才标记 SUCCESS，失败则阻断后续阶段。

**涉及文件**: `backend/app/services/pipeline_service.py`, `backend/app/services/react_engine.py`, `backend/app/services/tools.py`

**Why:** 早期发现问题，避免到 Stage 6 才发现源码不可运行。

## 问题50: check_change_ratio 服务端拦截

**现象**: prompt 里写 MANDATORY 要求调 `check_change_ratio`，但 LLM 会在代码未修改时跳过。

**解决**: 在 ReAct 引擎的 `finish_optimization` 处理前加入服务端拦截：若 `max_change_ratio > 0` 且 LLM 未调过 `check_change_ratio`，自动执行。超标则注入失败消息让 LLM 修复，通过则放行。

**涉及文件**: `backend/app/services/react_engine.py`

**Why:** 不依赖 LLM 自觉性，代码层面强制保证。

## 问题51: ReAct 推理逐轮推送

**现象**: ReAct 验证全部完成后才一次性展示推理过程，等待期间前端无反馈。

**解决**: 新增 `_run_loop_with_tools_stream` 异步生成器，每轮处理完成后 `yield {"react_steps": [...], "round": N}`。pipeline_service 用 `async for` 消费，每轮推一个 `stage_update` 到前端。

**涉及文件**: `backend/app/services/react_engine.py`, `backend/app/services/pipeline_service.py`

**Why:** 实时反馈让用户体验更好，不用等全部完成才知道进度。

## 问题52: 用例检视恢复后跳回 Stage 3

**现象**: 流水线用例检视确认后，状态机又跳到代码生成阶段（Stage 3），而非继续 Stage 5（测试增量生成）。

**根因**: `resume_pipeline()` 中无论 `skip_code_gen` 是否为 True，ReAct 验证门都无条件执行。验证门 yield `_update_stage(CODE_GEN, RUNNING)` → 前端看到 Stage 3 运行中。且 `_update_stage(CODE_GEN, SUCCESS, "Using cached code")` 覆盖了首次运行时的 Stage 3 日志。

**解决**: 验证门增加 `need_code_gen` 守卫（`if current_code and need_code_gen`），缓存路径跳过。去掉 `_update_stage(CODE_GEN, SUCCESS, "Using cached code")`，恢复时不再改写 Stage 3 状态。

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** 状态机应只推进不回溯，缓存的阶段不应重复执行。

## 问题53: 流水线日志观察结果截断

**现象**: 合并后的多工具结果（如 `[check_imports] ... [run_module] ...`）在日志中被截断到 150 字符，看不到 `run_module` 和 `[auto:check_change_ratio]` 结果。

**解决**: `_save_pipeline_log` 中 Stage 3 观察结果截断从 150 提升到 800 字符。

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** 合并多工具后单步输出变长，需要更大的截断上限。

## 问题54: UML 优化结果未写入 DiffViewer

**现象**: Stage 1 UML 优化成功，前端切换到 diff 面板但内容空白。`llm_optimize_*.md` 日志也只有 prompt 没有响应。

**根因**: 
- PipelineConsole 在 `uml_optimize` 成功时只切面板（`setRightPanelTab('diff')`），未调 `setOptimizationResult` 传递优化数据给 DiffViewer。
- `optimize_uml` 函数只保存 prompt 到日志，未追加 LLM 响应。

**解决**: 
- Stage 1 成功时从 `pipeline.stages[0].result` 提取 `optimized`/`diff`/`changes_summary`，调 `setOptimizationResult(original, optimized, diff, '')`。
- `optimize_uml` 的 LLM 调用后追加响应到同一个日志文件。

**涉及文件**: `frontend/src/components/PipelineConsole/PipelineConsole.tsx`, `backend/app/services/code_generator.py`

**Why:** 数据不传递，UI 当然空白。日志不全无法排查 LLM 行为。

## 问题55: validate_syntax 冗余移除

**现象**: 验证工具有 `validate_syntax` + `check_imports` 两个，前者用 `ast.parse` 做语法检查，后者第一步也是 `ast.parse`。LLM 多调一个工具多耗一轮。

**解决**: 从 `create_validation_tools()` 移除 `validate_syntax`，`check_imports` 描述更新为"语法+导入一体检查"。验证工具从 5 个减到 4 个。

**涉及文件**: `backend/app/services/tools.py`, `backend/app/services/react_engine.py`

**Why:** 减少冗余工具 = 减少轮次消耗。

## 问题56: 新工程不应启用 change_ratio 检查

**现象**: 从 UML 新建项目时 `max_change_ratio=50` 仍生效，ReAct 引擎注入 `check_change_ratio` 工具。但新工程没有原始代码对比，检查结果总是 0%。

**解决**: pipeline_service 中 `max_change_ratio` 仅在 `source_dir` 非空（已有项目代码适配）时传入 ReActEngine，新工程传 0 禁用。

**涉及文件**: `backend/app/services/pipeline_service.py`

---

## 问题57: 流水线停止按钮不生效

**现象**: 流水线运行中（代码生成、测试生成阶段）点击停止按钮，流水线继续执行不停止。

**根因**: WebSocket 消息读取与流水线执行是串行的。`ws.receive_text()` 只在暂停点被调用，活跃执行 LLM 调用期间没人读 WebSocket。

**解决**: 
- 后台 reader + `asyncio.Queue`：`background_reader` 持续读消息入队
- 主循环每次 yield 前 `check_stop()` 清空队列查停止标志
- `wait_for_*` 统一从队列取消息，停止优先
- 主循环后统一出口发送 stopped 事件 + 完整 pipeline 状态

**涉及文件**: `backend/app/api/pipeline.py`, `backend/app/services/pipeline_service.py`, `frontend/src/components/PipelineConsole/PipelineConsole.tsx`

**Why:** 停止是用户可控退出的基本保障。

## 问题58: 停止后状态机未重置 + spinner 持续旋转

**现象**: 停止后 RUNNING 阶段仍为 RUNNING，`_stopped` 标志残留，loading 图标一直转。

**根因**: `stop_pipeline()` 只设标志不更新阶段状态；`resume_pipeline` 内部 `return` 导致 stopped 事件未发送。

**解决**:
- `stop_pipeline()` 遍历 RUNNING 阶段 → FAILED + "[已停止]"
- 新增 `_clear_stop_flag()` 在 finally 清理
- 循环后统一 `check_stop()` 确保停止场景也发出 stopped 事件
- 所有 stopped 事件附带 `data: pipeline.model_dump()`

**涉及文件**: `backend/app/services/pipeline_service.py`, `backend/app/api/pipeline.py`, `frontend/src/components/PipelineConsole/PipelineConsole.tsx`

**Why:** 状态机不重置 = 下次启动状态混乱，UI 与实际不一致。

## 问题59: Stage 5b ReAct 测试校验 import 全失败

**现象**: ReAct 校验测试代码报 `ModuleNotFoundError`，3 轮耗尽，"Incomplete after 3 rounds"。

**根因**: ReAct 只传入 `test_files`，`check_imports` 临时目录只有测试文件没有源文件，`from base_task import BaseTask` 必然失败。

**解决**: 传入 `{**current_code, **test_files}` 合并字典，结果用 `_is_test_file()` 过滤只取测试文件更新。

**涉及文件**: `backend/app/services/pipeline_service.py`

**Why:** import 检查需要完整模块依赖闭包才能正确解析。

## 问题60: 流水线 7→6 阶段 + Stage 6 移除

**现象**: 第 6 阶段（用例调试执行）仅做简单编译检查+LLM fix，功能单薄与 Stage 5b ReAct 重叠。

**解决**: 删除 `TEST_EXEC`，Stage 5 后引入 ReAct 测试校验，Stage 6 合并测试执行+代码优化。

**涉及文件**: `backend/app/models/pipeline.py`, `backend/app/services/pipeline_service.py`, `frontend/src/types/pipeline.ts`, `frontend/src/components/PipelineConsole/PipelineConsole.tsx`

**Why:** ReAct 替代简单 LLM fix 更可靠，合并减少概念复杂度。

## 问题61: dev_review.txt 评审记录统一收编

**现象**: UML 评审 → `dev_review.txt`，用例审核 → `test_review.txt`，两处分散；跳过 UML 优化无记录。

**解决**: `save_review` 端点统一两种格式，用例审核也写入 `dev_review.txt` 带 `[用例审核]` 前缀；新增 `_save_uml_review_record()` 处理跳过场景。

**涉及文件**: `backend/app/api/files.py`, `backend/app/api/testhub.py`, `backend/app/services/pipeline_service.py`, `frontend/src/components/DiffViewer/DiffViewer.tsx`

**Why:** 统一入口便于追溯完整评审决策链路。

## 问题62: uploads/ 目录反复自动生成

**现象**: 删了 `backend/uploads/` 重启又出现，Excel 上传后文件残留。

**根因**: `upload_dir` 在三处 `os.makedirs(..., exist_ok=True)`，上传后不清除。

**解决**: 删除 `upload_dir` 配置，改用 `tempfile.NamedTemporaryFile` 解析后自动清除。

**涉及文件**: `backend/app/core/config.py`, `backend/app/main.py`, `backend/app/services/file_service.py`, `backend/app/api/files.py`, `.gitignore`

**Why:** 临时文件不需要持久化。

## 问题83: 流水线 UML 优化只优化类图

**现象**: 流水线 Stage 1 "UML优化" 只接收类图 `diagram: UmlDiagram`，项目的时序图和组件图不会进入优化。

**根因**: `run_pipeline` Stage 1 调用 `optimize_uml(diagram, instructions)`，参数只有类图；`sequence_diagram` / `component_diagram` 参数虽然签名中有，但只在 Stage 3 代码生成时使用，Stage 1 完全没传。

**解决**: Stage 1 改为调用 `optimize_project(class_dict, seq_dict, comp_dict, instructions)`，三种图一起交叉校验综合优化。`result["optimized"]` 结构变为 `{"class": {...}, "sequence": {...}, "component": {...}}`，从 `result["optimized"]["class"]` 提取优化后类图供下游阶段使用。

同步更新:
- DiffViewer 增加图类型切换标签（类图/时序图/组件图）、一致性报告展示
- uiStore 新增 `originalDiagrams` / `optimizedDiagrams` / `diffContents` / `activeDiffDiagramType` 等字段
- PipelineConsole 从 project 收集三种图的原始版本，生成 per-type diff
- 接受操作将三种优化图分别写入 project 中对应 diagram_type 的位置
- 拒绝重优化调用 `apiOptimizeProject`（全局 API）而非 `apiOptimizeUml`（单图 API）

**涉及文件**: `backend/app/services/pipeline_service.py`, `backend/app/api/pipeline.py`, `frontend/src/stores/uiStore.ts`, `frontend/src/components/DiffViewer/DiffViewer.tsx`, `frontend/src/components/DiffViewer/DiffViewer.css`, `frontend/src/components/PipelineConsole/PipelineConsole.tsx`, `frontend/src/App.css`

**Why:** 流水线追求完整的自动化设计验证，不应局限于单一图类型。

## 问题84: 优化结果缺少 diagram_type 画布切换异常

**现象**: 右侧栏 DiffViewer 选中时序图/组件图标签，点击"画布: 优化版"后主画布渲染错误编辑器（如时序图数据渲染成类图编辑器）。

**根因**: LLM 返回的优化结果（如 `{"lifelines": [...], "messages": [...]}`）是纯内容不含 `diagram_type`、`name` 等元数据字段。`setDiagram(optimizedDiagram)` 后 `App.tsx` 按 `diagram.diagram_type || 'class'` 分发编辑器，缺失时默认落到 `UMLEditor`。

**解决**: 存储优化结果时用 `{ ...orig, ...opt }` 合并原始图的元数据字段（diagram_type、name、id、grid_visible 等），保证 `diagram_type` 正确。

同步修复:
- `handleTypeSwitch` 切换标签时显式 `setDiagram(originalDiagrams[type])` 恢复原始版（因 project 中存储的可能是之前 toggle 留下的污染版）
- `handleToggleCanvas` 只在 `targetIdx !== currentActiveIdx` 时调用 `setActiveDiagram`，避免多余重置

**涉及文件**: `frontend/src/components/PipelineConsole/PipelineConsole.tsx`, `frontend/src/components/DiffViewer/DiffViewer.tsx`

**Why:** 图的元数据完整性是所有编辑器正确分发的基石。

## 问题85: 全局优化弹窗提示全是类图内容

**现象**: 流水线 Stage 1 弹窗标题"UML 优化需求"，描述"当前类图包含 X 个类，Y 条关系"，placeholder 示例全是类图场景。

**根因**: PipelineConsole 的优化指令 Modal 硬编码了类图相关文案。

**解决**: 弹窗改为动态展示项目中所有图类型及元素数量（如"类图(5元素)、时序图(3元素)、组件图(2元素)"），标题改为"全局设计优化"，placeholder 示例改为跨图类型场景（检查时序图方法匹配、组件依赖优化、命名规范统一等）。

**涉及文件**: `frontend/src/components/PipelineConsole/PipelineConsole.tsx`

**Why:** UI 文案应与实际功能一致，避免误导用户。

## 问题86: `getState().diagram` 取到非类图导致画布串图

**现象**: 右侧栏"全局优化对比"选中类图标签，点击原始版/优化版，主画布在类图和时序图之间切换。

**根因**: PipelineConsole 事件处理中 `const classOrig = useDiagramStore.getState().diagram` 取的是**当前激活图**而非类图。当项目有多张图且激活区不是类图时，`classOrig` 实际是时序图或组件图。后续 `optimizeds['class'] = { ...classOrig, ...classOpt }` 的 `diagram_type` 就错了。

**解决**: 改为从 `project.diagrams` 按 `diagram_type === 'class'` 显式查找：
```typescript
const classOrig = project.diagrams.find(d => (d.diagram_type || 'class') === 'class');
```

**涉及文件**: `frontend/src/components/PipelineConsole/PipelineConsole.tsx`

**Why:** `getState().diagram` 是便利访问器但语义模糊，按类型查找更安全可靠。

**Why:** 变更比例检查只对已有代码项目有意义。
