# UML 2.5.1 小型跨图完整案例

这是跨图案例的说明文档。案例的唯一数据源是同目录下的 [`cross_diagram_example.umlproj`](./cross_diagram_example.umlproj)，本文件不重复嵌入 JSON，避免 Markdown 与可加载项目发生漂移。

## 文件职责

- `cross_diagram_example.umlproj`：可被设计器直接加载的完整案例数据。
- `cross_diagram_example.md`：案例范围、跨图引用关系和检查要点，供 skill 阅读。
- 修改案例时，以 `.umlproj` 为准；Markdown 只同步结构说明，不复制对象数据。

## 案例范围

这是一个小型登录系统，包含：

- 组件图：`WebApp → AuthService → UserRepository`
- 类图：`AuthService` 组件类图、`UserRepository` 组件类图
- 时序图：用户提交登录、认证服务查询用户并校验密码
- 跨图引用：`component_id`、`class_ref`、组件接口和类实现关系

## 跨图阅读顺序

1. 先看组件图，确认组件边界、提供接口和所需接口。
2. 根据类图的 `component_id`，查看每个组件内部的接口与实现类。
3. 根据时序图生命线的 `class_ref`，回到对应类图确认参与交互的类。
4. 检查消息方向、调用类型、返回消息以及 `opt` 片段是否与业务流程一致。

## 关键引用关系

| 起点 | 引用或契约 | 终点 | 检查目的 |
|---|---|---|---|
| `WebApp` 组件 | `required_interfaces: IAuthService` | `AuthService` 组件 | 所需接口有对应提供者 |
| `AuthService` 组件 | `required_interfaces: IUserRepository` | `UserRepository` 组件 | 跨组件依赖方向正确 |
| `AuthService` 类 | `realization` | `IAuthService` 接口 | 类实现组件对外契约 |
| 时序图 `AuthService` 生命线 | `class_ref` | `class_auth_service` | 生命线绑定到真实类 |
| 时序图 `UserRepository` 生命线 | `class_ref` | `class_user_repository` | 跨组件类引用可追溯 |
| 登录查询消息 | `sync` / `return` | 用户查询与返回 | 调用和返回语义成对 |

## 规范检查重点

- 每张跨图内部图设置正确的 `component_id`。
- 所有 `class_ref` 都指向项目中存在的类 ID。
- 组件所需接口必须能在项目中找到匹配的提供接口。
- 接口实现使用 `realization`，业务调用依赖使用 `dependency`。
- 时序图返回消息使用 `return`，自身行为使用 `self`，可选流程使用 `opt`。
- ID 在整个项目范围内保持唯一，不能只在单张图内唯一。

## 如何查看

直接在 UML 设计器中打开 [`cross_diagram_example.umlproj`](./cross_diagram_example.umlproj)。如果需要生成或修改案例，先更新 `.umlproj`，再根据实际结构维护本说明文档。
