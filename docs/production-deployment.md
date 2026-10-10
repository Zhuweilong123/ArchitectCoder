# P0 安全修复与部署验证

本次修复适用于单用户本地运行和独立实例私有化试点。共享 API token 不提供多用户身份、角色或租户隔离。

## 修复后的行为

| 场景 | 结果 |
|---|---|
| 配置 token 后，未认证读取目录或项目 | HTTP 401 |
| 错误 Bearer token | HTTP 403 |
| 正确 token，读写已配置工作区 | 正常打开、保存、浏览 |
| `safe=false` 读取或写入工作区之外 | HTTP 403，文件不被覆盖 |
| 符号链接指向工作区外 | 拒绝打开，不出现在浏览结果中 |
| 浏览器聊天、网络断线重连 | 每次通过 WebSocket subprotocol 发送凭证，URL 不含 token |
| 凭证错误或浏览器来源不被允许 | WebSocket 1008；界面提示原因，停止自动重连 |
| 非本机监听但未开启严格生产模式 | 启动失败 |
| 严格模式缺少安全配置 | 启动失败并给出配置项名称 |

## 本地查看效果

1. 在 `backend/.env` 设置随机 `INTERNAL_API_TOKEN`，在 `frontend/.env.local` 设置相同的 `VITE_API_TOKEN`。不要提交这两个文件。
2. 保留 `API_HOST=127.0.0.1`、`STRICT_PRODUCTION=false`。本地无 token 的开发模式仍可使用。
3. 外部项目需在后端设置 `WORKSPACE_ROOTS`（多个目录用逗号分隔），然后重启后端。客户端的 `safe` 参数不能改变这项策略。
4. 重启前端开发服务，或重新构建静态前端，再打开聊天和项目文件。修改 Vite 凭证后必须重新构建/重启。
5. 不带凭证访问 `http://localhost:8001/api/files/browse` 应得到 401；有凭证访问未配置的外部目录应得到 403。聊天凭证错误时应出现明确提示。

聊天浏览器的 Origin 必须包含在 `CORS_ORIGINS` 中，默认允许本地 `http://localhost:3000`。原生客户端仍支持 Authorization Bearer 握手；旧 URL token 方式不再接受。

## 私有化生产配置

以 [`backend/.env.production.example`](../backend/.env.production.example) 为参考填写 `backend/.env`。示例里的 token 和工作区占位值故意不能通过启动检查。

- 设置 `STRICT_PRODUCTION=true`、`DEBUG=false`。
- 使用至少 32 字节的随机 token，例如运行 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成后写入配置。前端 token 也需同步。
- 设置已有的绝对 `WORKSPACE_ROOTS`，不能使用磁盘根目录。严格模式不再默认开放整个应用源码仓库；项目存储和运行产物目录仍可访问。
- `CORS_ORIGINS` 使用 JSON 数组，填写实际站点的协议、域名和端口，不使用通配符。
- 默认通过 `127.0.0.1:8001` 接反向代理，远程浏览器使用 HTTPS/WSS；代理需转发 Origin 和 WebSocket upgrade/subprotocol 请求头。只记录请求 URL，不记录含凭证的 Authorization、Sec-WebSocket-Protocol 请求头。
- 需要直接绑定网络接口时显式设置 `API_HOST`；非本机绑定必须开启严格模式。

用 `cd backend` 后的 `python -X utf8 -m app.main` 启动后端。生产前端使用 `npm ci`、`npm run build` 构建，由反向代理提供 `frontend/dist` 并转发 `/api`，不使用 Vite 开发服务器作为生产入口。

前端构建中的共享 token 对获准使用此实例的浏览器用户可见，因此只能用于单用户或共享信任边界的独立实例。严格模式也不代表命令已具备操作系统隔离；容器 worker、资源限额、备份恢复和多用户身份仍需独立验收。

## 回归检查

```text
cd backend
python -m pytest -q
```

```text
cd frontend
npm run build
npm run test:security
```

后端安全测试通过真实 HTTP/WebSocket 传输验证认证、重连、来源限制、文件读写和符号链接边界；生产配置测试覆盖弱 token、通配来源、工作区缺失和非本机监听。前端安全测试验证 URL 无凭证、重连复用认证和认证失败停止重试。CI 已覆盖 `dev-*` 分支、后端测试及前端构建和回归。
