# 多用户隔离部署指南（Phase 6）

对应 `user-isolation-development-plan.md` 阶段 6 的部署要求：反向代理、TLS、
secure cookie、密钥注入、备份与恢复。容器运行机制（镜像、端口、卷、rootless
Podman）见 [CONTAINERIZATION.md](CONTAINERIZATION.md)，本文只讲与用户隔离
直接相关的部署决策。

## 1. 隔离模式与启动自检

后端启动时（`deeptutor/api/main.py` 的 lifespan）会做以下检查：

1. `assert_supported_backend()` —— 内置多用户认证与 PocketBase 控制面**不可组合**，
   误配置直接拒绝启动（`deeptutor/services/auth.py`）。
2. 内置认证开启时必须 `backend_workers=1`；多 worker 拒绝启动，注册/邀请码接口也会
   拒绝该配置。用户和邀请码的 JSON 事务锁及内存限流只支持单进程。
3. 创建或读取独立的 `data/system/auth/registration_proxy_secret`（0600），供 Next 注册桥
   证明真实客户端地址；密钥损坏不静默替换。
4. `log_isolation_mode()` —— 在启动日志里声明当前隔离姿态：

| 启动日志 | 级别 | 含义 |
| --- | --- | --- |
| `Isolation mode: single-user compatibility` | **WARNING** | 认证关闭：所有请求以本地管理员身份运行在共享 `data/` 工作区。**这不是安全的多用户模式**。默认日志级别（WARNING）下即可见。 |
| `Isolation mode: multi-user isolated` | INFO | 认证开启：每个账号的工作区隔离在 `data/users/<user_id>/` 下。需 `main.yaml` 设 `logging.level: INFO` 才显示。 |
| `DEEPTUTOR_WORKSPACE_ROOT=... is a deployment-wide shared root` | WARNING | 认证开启但同时配置了部署级共享根。该根只对管理员可达，绝不能当作每用户工作区使用。 |

生产多用户部署的验收标准之一：日志里**不得出现** `single-user compatibility` 的
WARNING；需要确认 isolated 姿态时把 `main.yaml` 的 `logging.level` 临时调到
`INFO` 重启查看。

## 2. 从空目录启动并完成 A/B 隔离验证

```bash
mkdir -p data/user/settings
cat > data/user/settings/auth.json <<'JSON'
{ "version": 1, "enabled": true, "registration_trusted_proxies": [] }
JSON
cat > data/user/settings/system.json <<'JSON'
{ "version": 1, "backend_workers": 1 }
JSON
# 用 docker：DEEPTUTOR_WORKSPACE_HOST 必须是绝对路径
python scripts/docker_compose.py -f docker-compose.yml up -d
```

上述写文件命令只用于空目录。已有部署应合并配置，保留现有账号与设置。
如果希望在网页创建第一个管理员，`auth.json` 不填有效的 `username` / `password_hash`
组合；配置了 bootstrap 管理员后，应使用该账号登录并发码。然后：

1. 浏览器打开 `http://<host>:3782/register`，注册账号 A。只有可正常读取的用户库确实为空、
   且无配置 bootstrap 管理员时，A 才无需邀请码并成为 admin。损坏的身份库返回不可用，不能
   被解释为空库；应修复或恢复原数据。
2. 以 A 登录，进入 `/admin/users` 的“邀请码”tab，生成一个单次码并复制；用另一个浏览器
   注册 B，然后在登录页登录。B 为立即启用的普通 Standard 账号，无自动 grant；根据需要配置
   个人模型或让管理员授权。原“用户”tab 仍可直接创建 B，无需邀请码。
3. 隔离验证：
   - A 与 B 分别登录，确认各自会话列表互不可见（turn 存储按用户 SQLite 隔离，
     `tests/app/test_turn_application_service.py::test_turn_operations_cannot_cross_session_store_ownership`）；
   - 确认 B 的数据位于 `data/users/<id_B>/`，管理员 A 继续使用管理员兼容数据根 `data/`
     （其中用户工作目录在 `data/user/`），B 不可访问该管理员根；
   - B 尝试访问 A 的会话/KB 应得到 403/404 而非数据（fail-closed，见
     `tests/multi_user/test_scope_fail_closed.py`）；
   - 未认证请求访问受保护的业务 API 应得到 401，而不是管理员数据；注册状态、登录和注册
     等公开认证接口按各自契约处理。

邀请码完整码只在生成时展示一次，关闭结果、切换 tab 或刷新后不再可见；丢失时撤销重发。
默认一次生成 1 个码、每码可用 1 次、7 天过期。可设置批量 1–100、次数 1–1000、有效期
1–365 天或永久（API `null`）、备注最多 200 字符。撤销只影响后续注册，删除账号不返还次数。
参数、接口和故障恢复说明见 [邀请码注册实现记录](invite-registration-implementation.md)。

## 3. 反向代理与 TLS

单容器形态下浏览器只与前端 `:3782` 通信，通常 `/api/*` 与 `/ws/*` 由容器内 Next.js
中间件（`web/proxy.ts`）转发到内部 `:8001`。`POST /api/auth/register` 单独由 Next Pages
API 桥处理，保留真实客户端地址后签名转发。因此：

- 反向代理 / TLS 终结点只需 upstream 到 `:3782`，**不要**把 `:8001` 发布到
  公网（它是可选的调试端口）。
- WebSocket：代理必须放行 `/ws/*` 的 `Upgrade`/`Connection` 头，超时设置要
  长于对话 turn 时长。
- CORS 用**前端 origin** 而不是 API URL：认证开启时在
  `data/user/settings/system.json` 配置
  `"cors_origins": ["https://deeptutor.example.com"]` —— 精确 origin 列表。

### 注册限流与代理地址

Next 注册桥从 `req.socket.remoteAddress` 取 IP；默认不信任 `X-Forwarded-For`。
若在 Next 前部署反向代理，在 `auth.json` 的 `registration_trusted_proxies` 中列出它的
精确 IP（最多 32 项；默认 `[]`，不支持 CIDR）。仅 Next 使用此配置，从最近一跳向外解析
可信代理链。不要填浏览器来源或不受控代理。

Next 对 IP、时间、方法、路径与 body 摘要做 HMAC，Python 后端仅接受 60 秒内的有效签名，
否则只按自己的真实 socket peer 限流。后端永不采用未签名 XFF，即使通过编码路径进入
通用 rewrite 也不能借此选择限流桶。10 次无效邀请码/15 分钟触发 900 秒冷却，
第 10 次返回 429；成功注册清桶。10000 个桶满时新 IP 返回 60 秒重试，过期桶会清理。

Next 与 API 需读取同一 runtime 中的独立 `registration_proxy_secret`；默认同机/同容器已有
共享数据树。Next 使用 `DEEPTUTOR_HOME`，未设置时从 web 工作目录的上一级定位数据。
先让后端启动生成密钥；前端缺密钥或读取失败返回 `503 registration_proxy_unavailable`，
不会改信任客户端头或静默共用 IP 桶。

分离部署只需向 Next 提供对应桥接密钥和 auth 配置，无需给它 JWT 密钥。若反向代理直接
把注册请求转到 API、绕过 Next，后端将按代理 socket 地址共用桶；使用前置代理每 IP 限流，
或将注册恢复到标准 Next 路径。部署时实际验证两个客户端各自计数、伪造 XFF 不换桶。

## 4. Secure cookie

`dt_token` cookie 恒为 `HttpOnly`；其余属性来自 `auth.json`
（`deeptutor/services/config/runtime_settings.py` 的默认值：

```json
{ "version": 1, "enabled": true, "cookie_secure": true, "token_expire_hours": 24 }
```

- `cookie_secure: true` 时 cookie 带 `Secure` 且 `SameSite=None`；为 `false`
  （默认，适配 localhost 明文）时 `SameSite=Lax`。**任何经 TLS 的生产部署都应
  设为 `true`**，否则浏览器在 HTTPS 下虽仍接受无 `Secure` 的 cookie，但跨站
  场景（如把 UI 嵌入第三方页面）会被 `SameSite=Lax` 卡住。
- `token_expire_hours` 控制登录态时长；改小只影响新签发的 token。
- 同页面的 WebSocket 认证也读取同一 cookie（`ws_require_auth`），无需额外配置。

## 5. 密钥注入

| 密钥 | 位置 | 说明 |
| --- | --- | --- |
| JWT 签名密钥 | `data/system/auth/auth_secret` | 首次启动自动生成（`load_or_create_auth_secret`）。HS256 签名所有 `dt_token`。**随数据树一起备份**：丢失或轮换会让所有已发 token 失效（用户重新登录即可，无数据损失）。文件以私有权限写入（`write_secret_text`）。 |
| bootstrap 管理员 | `auth.json` 的 `username` + `password_hash` | 可选。只存在于内存 overlay，不落盘到 `users.json`；用 `python -c "from deeptutor.services.auth import hash_password; print(hash_password('...'))"` 生成 bcrypt 哈希。多用户部署建议留空，走首注册提升 admin。 |
| 账号凭据 | `data/system/auth/users.json` | 内置身份库。改密码/角色/禁用会自增 `token_version`，旧 token 即刻失效，无需黑名单。 |
| 邀请码与兑换历史 | `data/system/auth/invites.json` | 只保存 SHA-256 摘要和末四位提示，不含完整邀请码；与用户库一起备份。 |
| 注册代理证明密钥 | `data/system/auth/registration_proxy_secret` | 后端启动自动创建，0600；Next 只用它签名真实来源 IP，与 JWT `auth_secret` 分开。Next/API 两端必须读取同一份。 |
| PocketBase 凭据 | 环境变量 `POCKETBASE_ADMIN_EMAIL`/`POCKETBASE_ADMIN_PASSWORD` 等 | 仅遗留单用户兼容模式；由 `INTEGRATION_PROCESS_OVERRIDE_KEYS` 白名单转发进容器，且必须设置 `DEEPTUTOR_ALLOW_INTEGRATION_ENV_OVERRIDES`。与内置多用户认证互斥（启动自检拒绝）。多用户部署不要启用。 |

除上述白名单外，容器入口会忽略其余环境变量 —— 端口、认证、路径一律以
`data/user/settings/*.json` 为准（见 AGENTS.md 的例外说明）。

## 6. 备份与恢复

Compose 形态把**整个** `./data` 树挂进容器（`./data:/app/data`），一个目录即
全部状态：

```
data/
├── user/            # 运行时设置 + 管理员工作区（settings/*.json 在 user/settings）
├── users/           # 每用户工作区（隔离边界本体，逐目录归属账号）
├── system/          # auth/（账号、邀请码、密钥、可能存在的注册journal）、grants/、audit/、user-secrets/
├── knowledge_bases/ # RAG 索引
├── memory/          # 记忆库
├── partners/        # IM 伙伴状态
├── cli-apps/        # 已安装 CLI 应用（runner 只读挂载）
└── redis/           # turn 协调 AOF（可重建，优先级最低）
```

备份：

```bash
python scripts/docker_compose.py -f docker-compose.yml stop
tar -czf deeptutor-data-$(date +%F).tgz data/
python scripts/docker_compose.py -f docker-compose.yml up -d
```

恢复：

1. 停容器，解包到目标 `data/`；
2. 修正属主：容器内进程以 UID 1000 运行 ——
   `sudo chown -R 1000:1000 data/`（rootless Podman 用 `-v ...:U` 自动处理）；
3. `up -d` 后核对启动日志出现 `multi-user isolated`；
4. 用任一账号重新登录验证（`auth_secret` 一致则旧 cookie 仍有效）。

`auth/registration-journal.json` 持久化后即表示注册事务已提交；若备份中有该文件，保留它，
恢复后系统会在下一次身份/邀请码访问前向前完成用户与兑换计数的写入。不要单独删 journal、
减邀请码次数或混用不同时间点的 `users.json` / `invites.json`。存储损坏或恢复失败会返回不可用，
不会将其视为可重新创建首管理员的空部署。响应丢失时，账号可能已创建，先尝试登录或查兑换记录。

注意：`docker-compose.ghcr.yml` 的历史版本只挂载了三个子树，升级首次 `up -d`
前要按 CONTAINERIZATION.md 的迁移说明把容器内状态 `docker cp` 出来，否则
`auth_secret` 会重新生成、全部账号丢失。

## 7. 执行（exec）与 runner 姿态

- 普通用户默认**没有**宿主执行能力（`deeptutor/multi_user/execution_access.py`
  拒绝；`tests/multi_user/test_scope_fail_closed.py` 覆盖）。
- `docker-compose.yml` 的 sandbox-runner sidecar 只读挂载管理员/单用户兼容
  工作区、可写仅限 `outputs/`，**不**接收 `data/system`、`data/users`、用户设置
  或凭据；不给 host `ports:`。
- 若要给普通账号开放执行，必须按账号提供私有 runner workspace、临时目录与产物
  根目录（spec 阶段 6），并保留网络/CPU/内存/生命周期限制 —— 当前默认部署不
  满足此前不要放开。

## 8. 验收清单

- [ ] 启动日志出现 `Isolation mode: multi-user isolated`
- [ ] `auth.json` 设置 `cookie_secure: true`（经 TLS 部署）
- [ ] `cors_origins` 只含实际前端 origin
- [ ] `:8001` 未发布到公网；runner 无 host 端口
- [ ] A/B 两账号会话、KB、文件互不可见；未认证请求 401
- [ ] `data/system/auth/auth_secret` 已纳入备份，恢复演练通过
- [ ] 普通账号 exec 被拒（B 账号在对话里让模型跑代码应得到拒绝提示）

邀请码新增验收：按 §2 完成真实浏览器发码/注册/登录，检查次数及撤销；按 §3 检查真实代理链
中的两个客户端独立限流。当前已验证与待验证状态集中在
[实现记录](invite-registration-implementation.md#验证记录)，本部署说明本身不是验收通过记录。

2026-09-23 的代码验证已完成后端目标组合 401 项、完整前端 Node 1189 项、Vitest 323 项及
类型/契约/架构等检查；`.next-invite-qa` 独立目录生产构建成功。真实 Chrome + Next + 完整
FastAPI 使用隔离数据目录的注册验收、API 重启持久化检查已通过，具体场景见实现记录。
代理 IP 仅在本机临时配置中模拟；Docker、真实外部反向代理链和既有 WS 撤权验收仍未据此关闭。
