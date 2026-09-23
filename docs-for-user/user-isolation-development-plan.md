# DeepTutor 单实例多用户隔离开发计划

本文档用于 `feature/user-isolation` 分支。目标是在一个 DeepTutor 部署中给多名用户提供独立的数据空间，同时尽量保持与官方 `main` 的合并成本可控。

## 1. 已确定的产品边界

- 采用一个 DeepTutor 服务实例，**多用户隔离路径不引入 PocketBase 或第二套控制平面**。理由：本方案的隔离核心在文件系统与进程层（`CurrentUser → PathService` 的路径 scope、per-scope SQLite、grant 体系、`token_version` 即时吊销），PocketBase 只能覆盖账号认证与表级 ACL，且其 auth-refresh 缓存（60s/worker）会把不变量 6 的"立即吊销"退化为有界延迟。PocketBase 作为遗留单用户兼容模式保留：compose 中 profile 默认关闭、env 转发需显式设置 `DEEPTUTOR_ALLOW_INTEGRATION_ENV_OVERRIDES`，与内置多用户认证互斥——同时开启时 `assert_supported_backend()` 直接拒绝启动。若未来确需外部身份源，优先评估标准 OIDC 接入而非维护 PB sidecar。
- 保留现有的 `identity.py`、`auth.py`、`CurrentUser`、`UserScope`、`PathService`、`SessionStore` 和 grant 机制；二次开发集中在隔离 seam 和薄适配层。
- 系统只有一个管理员角色。普通用户只能访问自己的数据；共享知识库、管理员数据和其他用户数据都必须经过显式授权。
- 默认使用现有 JSON/SQLite 存储。用户规模明显增长后，再把用户目录和会话适配器替换为数据库实现。
- 初期只启用一个 worker 和 `MemoryCoordinator`。普通用户的代码执行能力默认关闭，等操作系统级 sandbox 能按用户划分工作目录后再开放。
- `DEEPTUTOR_WORKSPACE_ROOT` 只作为管理员/单用户兼容模式的部署级 workspace；普通用户不能因为环境变量或伪造 settings 而进入该目录。

## 2. 必须保持的安全不变量

1. 每个 HTTP/WebSocket/SDK 请求都先解析 `CurrentUser`；请求上下文中的 scope 解析失败必须报错，不能静默退回管理员路径。
2. 用户数据路径只能由 `PathService` 产生，业务代码不能自行拼接 `data/users/<id>` 或使用全局 singleton。
3. 普通用户的 session、turn、事件流、记忆、知识库、笔记、生成文件和 workspace 必须位于自己的 scope 下。
4. 用户 A 拿到用户 B 的 session/turn ID 时，所有读取、订阅、取消和输入操作都应返回“资源不存在/无权访问”的等价结果，并且不能触发 coordinator 或后端任务。
5. 管理员共享数据只通过显式 grant 暴露；默认没有跨用户读写权限。
6. 被禁用账号的旧 token、WebSocket 和正在等待的交互不能继续创建新任务。
7. 普通用户不能通过 settings、环境变量、路径穿越、符号链接或直接提交绝对路径绕过自己的根目录。
8. 普通用户的 `exec` 默认不可用；开启前必须确认 runner 的容器、挂载目录、临时目录和产物目录均按用户隔离。

## 3. 目标结构

```
data/
├── system/                 # 用户表、审计、迁移元数据
├── user/                   # 管理员/单用户兼容空间
└── users/
    └── <user-id>/
        ├── settings/
        ├── sessions/
        ├── memory/
        ├── knowledge/
        ├── notebooks/
        ├── workspace/
        └── outputs/
```

请求链路保持为：

```
入口（HTTP / WebSocket / SDK / CLI）
  -> CurrentUser + UserScope
  -> PathService / SessionStore / Workspace adapter
  -> 现有 orchestrator、capability、tool
```

隔离逻辑放在 `CurrentUser -> PathService/adapter` 这个 seam。Agent、Tool、Capability 的业务接口不增加 `user_id` 参数，避免把租户概念扩散到整个系统。

## 4. 分阶段实施计划

### 阶段 0：基线和契约

**目标**：先固定当前行为和测试入口，再继续修改。

- 记录当前分支、`origin`/`upstream`、Python/Node 版本和依赖安装命令。
- 为上述不变量建立一份测试矩阵，先标记现有测试覆盖和缺口。
- 不改 Agent、Capability、Tool 注册表和官方插件接口。
- 为每个阶段使用一个小提交，提交标题只描述一个隔离主题，方便以后处理 Sync fork 冲突。

**验收**：能在干净环境安装开发依赖；目标测试可以被 pytest 收集；`git diff --check` 和架构检查通过。

### 阶段 1：路径解析 fail-closed（当前批次已完成未提交）

**负责模块**：`deeptutor/services/path_service.py`、对应回归测试。

- 删除 scope 解析失败时返回管理员 singleton 的宽泛 fallback。
- 保留无请求上下文时 CLI/后台任务的显式管理员兼容路径。
- 增加“解析失败必须传播”和“无请求上下文仍可运行”的测试。

**验收**：请求路径解析异常不会改变成管理员路径；CLI 兼容行为不回归。

### 阶段 2：workspace 和持久化根目录隔离（当前批次已完成未提交）

**负责模块**：`deeptutor/services/workspace/service.py`、workspace 测试。

- 普通用户的 allowed roots 仅包含自己的默认根和其子目录。
- 管理员继续支持 `DEEPTUTOR_WORKSPACE_ROOT` 的部署级兼容模式。
- 读取已保存 workspace binding 时重新验证根目录，拒绝伪造 settings、兄弟用户目录和 deployment root。
- 继续通过现有 `PathService` 产生 session、memory、KB、notebook 和 output 路径。

**验收**：普通用户即使设置 deployment root，也解析到自己的 scope；伪造 binding 直接失败；管理员旧配置仍可读取。

### 阶段 3：turn 归属和实时操作保护（当前批次已完成未提交）

**负责模块**：`deeptutor/app/service.py`、应用层测试。

- `subscribe_turn` 在访问 durable events 或 coordinator 前，先在当前用户的 `SessionStore` 中查找 turn。
- `cancel_turn`、`submit_user_reply`、`submit_user_input` 复用同一归属检查。
- 缺失 turn 返回空流或 `False`，不能读取另一个 store 的租约、事件或命令。
- 增加两个 store 共用 coordinator 时的跨用户回归测试。

**验收**：用户 A 无法通过用户 B 的 ID 观察、取消或注入交互；拥有者的正常事件流仍工作。

### 阶段 4：用户管理和 grant 审计

**建议模型**：廉价模型先做只读审计和测试补齐；我负责最终接口审查。

**检查范围**：

- `deeptutor/multi_user/identity.py`、`deeptutor/multi_user/auth.py` 及其路由：创建、禁用、重置、管理员校验、token 失效。
- 所有管理员接口必须同时检查“已认证”和“管理员角色”。
- grant 默认最小权限；读/写/管理权限的命名、持久化和撤销行为保持一致。
- 日志、错误响应和前端状态不能泄露密码、token、其他用户路径或完整用户列表给普通用户。
- 为禁用账号、错误角色、跨用户 grant、过期 token 增加 API 测试。

**验收**：已有身份实现足够时只补测试和文档；只有发现明确缺口才改实现，不新建第二套用户系统。

### 阶段 5：文件、知识库和资源访问面审计

**建议模型**：廉价模型按文件组执行；每组不得同时修改同一文件。

逐项检查：

- output-file 路由、下载/预览、附件处理、notebook 和 memory 工具。
- RAG/knowledge base 的创建、查询、删除、导入和共享 grant。
- CLI 与 WebSocket 是否存在直接调用 `PathService.get_instance()`、`get_admin_path_service()` 或裸 `Path(...)` 的路径。
- 所有用户提供的相对路径都经过 canonicalize、根目录检查和符号链接检查。
- 错误分支不能把全局目录列表、绝对路径或其他用户资源返回给调用者。

**验收**：补齐 A/B 用户矩阵测试、路径穿越和符号链接测试；发现的每个路径入口都有明确的 owner 和测试。

### 阶段 6：执行能力和部署配置

**负责模型**：廉价模型可以先更新文档、配置校验和启动检查；涉及 runner 挂载或权限模型的代码由我复核。

- 普通用户默认关闭 `exec` 和共享 runner。
- 检查 `docker-compose.yml`、runner 镜像和 `/workspace` 挂载，避免普通用户共用宿主目录。
- 若需要开启执行，按用户创建 runner workspace、临时目录和产物目录，并限制网络、CPU、内存和生命周期。
- 为反向代理、TLS、secure cookie、密钥注入、备份和恢复写部署文档。
- 启动时打印/校验当前 isolation mode，防止把单用户兼容配置误当成安全多用户模式。

**验收**：默认配置不会让两个普通用户共享 `/workspace`；部署文档能从空目录启动并完成一次 A/B 登录和隔离验证。

### 阶段 7：集成、同步和发布

- 在支持的依赖环境中运行目标测试、完整 pytest、前端类型检查和架构检查。
- 用 Docker 做最小端到端 smoke test：创建两个用户、各自创建 session/KB/文件、交叉访问、禁用账号。
- 从 `upstream/main` fetch 后，在独立 `sync/upstream-<date>` 分支 rebase/merge，先解决官方冲突，再把结果合回功能分支。
- 对每个官方 Sync PR 做一次隔离回归矩阵，不能只依赖 CI 触发成功。
- 用户明确要求后才 commit/push；当前阶段不自动提交或推送。

## 5. 廉价模型的执行协议

每个子任务必须写成一个小而封闭的工作单：

1. 指定唯一目标、允许修改的文件和禁止触碰的目录。
2. 明确现有接口和必须保持的不变量。
3. 要求先读现有测试，再补最小回归测试。
4. 禁止 commit、push、重置其他人的改动或大范围格式化。
5. 返回修改文件、行为变化、测试命令和未解决问题。
6. 主代理收到结果后执行 diff review、`git diff --check`、编译/类型检查和目标测试；失败时只把具体失败信息发回原子任务。

推荐的任务顺序：路径解析 → workspace → turn 归属 → 身份/grant 审计 → 文件/KB 审计 → 部署配置 → 集成测试。前三项已经由廉价模型完成第一版，尚未提交。

## 6. 验收测试矩阵

| 场景 | 预期 |
| --- | --- |
| A/B 同时创建 session、turn、事件 | 两边只能看到自己的数据 |
| A 使用 B 的 session/turn ID | 返回不存在/无权；不触发 coordinator |
| A 订阅 B 的实时流 | 空流或拒绝；不泄露事件内容 |
| A 读取 B 的 memory、KB、notebook、output | 拒绝 |
| A 伪造 settings 指向 deployment root 或兄弟目录 | 拒绝 |
| 相对路径穿越、绝对路径、符号链接 | 拒绝 |
| 管理员访问自己的兼容 workspace | 保持兼容 |
| 管理员授予只读 grant | 只能按 grant 读取，不能写入/删除 |
| 账号禁用后使用旧 token | 不能创建新请求或新 turn |
| 普通用户启用 exec | 默认拒绝；无明确 sandbox 不上线 |

## 7. 当前状态

廉价模型已完成阶段 1、2、3 的第一版代码和回归测试，改动仍在工作树中，未 commit、未 push。`compileall` 和 `git diff --check` 已通过；当前环境缺少 pytest 及部分运行依赖，因此完整测试需要先按项目的开发依赖重新建立环境。下一批适合交给廉价模型的是阶段 4 的身份/grant 审计，但在继续扩展前应先由主代理审查前三批 diff 并建立可运行的测试环境。

阶段 0 与阶段 6 的文档交付物：

- 测试矩阵（不变量 → 测试映射 + 缺口表）：`user-isolation-test-matrix.md`
- 部署文档（反向代理/TLS/secure cookie/密钥注入/备份恢复 + A/B 验证手册）：`user-isolation-deployment.md`
