# 邀请码注册实现记录

记录日期：2026-09-23。此文供后续复核本轮自行确定的实现细节，配合
[ADR-0005](adr/0005-invite-code-registration.md) 和 [部署指南](user-isolation-deployment.md) 阅读。
修改基线为 `feature/user-isolation` 的 `7ed6c990`；本轮保留既有隔离分支，没有合入最新
`main`，将准入功能与上游同步分别验证。最终验证结果见本文末尾，不将分批测试数量相加。

## 已实现的使用流程

1. 内置认证开启、用户库可读且确实没有账号、`auth.json` 未配置 bootstrap 管理员时，
   首次注册无需邀请码并成为 admin。只有用户名或空密码哈希不构成有效 bootstrap 配置。
2. 管理员登录 `/admin/users`，在“邀请码”tab 选择批量数、可用次数、有效期及备注，生成后
   立即复制完整码。页面只有“用户”和“邀请码”两个 tab，本次没有新增设备凭证管理 UI。
3. 受邀者在 `/register` 填用户名、密码及邀请码。成功后跳转登录页；新账号立即启用，
   固定普通用户与 Standard 预设，不自动获得模型、知识库或其他 grant。
4. 登录页提示受邀者配置个人模型或联系管理员授权。管理员仍可在“用户”tab 直接建号，
   包括已有 learner/custom 预设；该路径无需邀请码。
5. 管理员可按页查看邀请码状态、兑换者 ID/用户名/时间并撤销邀请码。撤销不影响已有账号，
   删除兑换者账号不返还次数，历史兑换记录保留。

完整码仅生成响应返回一次，前端只保存在组件内存；关闭结果、切换 tab、刷新或离开页面
都会失去完整码。列表只展示末四位，无法恢复明文；丢失后撤销并重发。注册页读取状态失败
时禁止提交并提供重试，提交失败后重新读取状态，以处理填写期间别人完成首注册的情况。

## 本轮确定的规则与理由

| 决定 | 具体规则与理由 |
| --- | --- |
| bootstrap 判断 | 用户库可读、无任何账号、无配置管理员三个条件同时满足；损坏 JSON/不可读文件/损坏事务日志不能重新打开无码注册 |
| 只控制准入 | 邀请码不携带 role 或 preset，受邀账号固定 `user` / `standard` / enabled，无 grant，避免入场凭据隐式授予管理员资源 |
| 保留两种建号方式 | 新增 create-only 路径给公开注册与管理员创建，重名返回冲突；保留 `save_user` 原有更新行为，兼容已有身份管理 |
| 码格式 | 随机 12 位 Crockford Base32，显示 `XXXX-XXXX-XXXX`；首尾空白、大小写、连字符不影响匹配，`O→0`、`I/L→1`，便于手工复制输入 |
| 最小默认授权 | 默认一次使用、7 天过期、一次生成一个；管理员可明确扩大范围或选择永久有效 |
| 明文不持久化 | `invites.json` 保存归一化码的 SHA-256 摘要与末四位 `code_hint`；列表、撤销响应、审计不包含明文或摘要 |
| 冲突顺序 | 先检查有效邀请码再报告用户名重名；未知、过期、耗尽、撤销统一错误，不能通过注册接口查询码状态 |
| 事务提交 | 用户和邀请码属于一次准入事务；日志持久化是提交点，提交后只向前恢复，不能把响应丢失当成未建号 |
| 单 worker | 启动及注册/邀请码接口拒绝 `backend_workers != 1`，进程内锁和内存限流不宣称跨 worker 一致性 |
| 界面范围 | 只做用户/邀请码两 tab；注册后仍跳登录，不新增自动登录、设备 UI 或账户审批步骤 |
| 导航兼容 | Pages API 为真实 socket 提供入口，也使 App Router 导航 hooks 可空；36 处 UI 调用补兼容，持久化类型声明并等待路由就绪，避免丢失深链 |
| 分支范围 | 在已有用户隔离分支实现；最新 main 的合并另行安排，避免掩盖本功能的回归来源 |

### 实施中补充的兼容与测试选择

Next Pages API 与 App Router 并存后，`usePathname`、`useSearchParams`、`useParams` 会出现
可空类型。本轮修复 36 处 UI 导航调用，并常驻 `web/types/navigation-compat.d.ts`；Pages 目录
纳入类型检查与架构扫描，避免只在生成构建类型后才暴露问题。Knowledge、Reading、Watching
等路由在路径、查询和路由参数就绪后才初始化；登录保留 `next`，学习创建和视频账号回调
也等参数就绪后再消费，避免把暂时为 null 误当成用户没有提供深链。
新增 `navigation-compat.spec.tsx` 与 `navigation-ready-components.spec.tsx` 共 5 项回归，
覆盖登录跳转、聊天 session、知识库深链、视频回调及学习创建参数。

回归还发现既有设备 heartbeat 测试依赖真实时钟：UTC 午夜前五分钟运行时，测试中的
`+300 秒` 会意外跨日，干扰本来要验证的当日限额。现将该测试起点固定为当天 UTC 中午，
保留显式 `+86400 秒` 的跨日用例；这是测试时钟稳定性修复，没有改变生产 heartbeat 行为。
单 worker 启动与可信代理配置新增 11 项回归，已包含在下面同一次后端运行的 401 项中。

## 接口契约

所有路径以下端 API 为准，网页的 `POST /api/auth/register` 经过 Next 注册桥。内置认证
关闭或 PocketBase 模式时，注册状态返回不可用，注册及邀请码管理返回 403。

| 请求 | 权限与响应 |
| --- | --- |
| `GET /api/auth/registration-status` | 公开，`{available,is_first_user,invite_required}`；不可用时三个值都为 `false` |
| `POST /api/auth/register` | 公开，`{username,password,invite_code?}`；成功 201，返回 `ok,user_id,username,role,preset,is_first_user,is_admin` |
| `GET /api/auth/invites?offset=0&limit=50` | 仅管理员，`{items,total}`；offset 非负，limit 1–100，默认 50 |
| `POST /api/auth/invites` | 仅管理员，参数见下表；成功 201，`{invites:[记录+code]}`，只有此响应包含完整码 |
| `POST /api/auth/invites/{id}/revoke` | 仅管理员，`{invite,ok:true}`；重复撤销幂等，不存在返回 404 |
| `POST /api/auth/users` | 原管理员直建账号路径保留，重名不覆盖已有账号 |

| 生成字段 | 默认值 | 接受范围 |
| --- | --- | --- |
| `batch_count` | 1 | 整数 1–100 |
| `max_uses` | 1 | 整数 1–1000 |
| `expires_in_days` | 7 | 整数 1–365，或 `null` 永久有效 |
| `note` | `""` | 最多 200 字符 |

公开记录包含 `id,code_hint,created_at,created_by,expires_at,max_uses,used_count,note,revoked_at,
revoked_by,status,redemptions`。后端还返回 `remaining_uses`。`status` 为 `active`、`expired`、
`exhausted` 或 `revoked`；每条 redemption 包含 `user_id,username,redeemed_at`。
时间按 UTC ISO 格式保存；`expires_at=null` 表示无期限。

注册请求沿用用户名/邮箱校验，普通用户名为 3–64 位字母、数字、下划线、点或连字符。
密码至少 8 字符、至多 72 个 UTF-8 字节，以匹配 bcrypt 限制；邀请码字段最多 64 字符。
请求中的 role/preset 不改变受邀账号身份。

| 失败 | 响应 |
| --- | --- |
| 缺失或无效邀请码（含过期、用尽、撤销） | 403，顶层 `detail="A valid invitation code is required."`，`error_code="invalid_invite"` |
| 有效邀请码下用户名重名 | 409，`error_code="username_taken"`；不扣次数 |
| 参数格式/边界错误 | 422 |
| 身份/邀请码/恢复日志存储不可用 | 503，`error_code="identity_store_unavailable"` |
| 无效邀请码触发冷却 | 429，`error_code="registration_rate_limited"`，`Retry-After` 秒数 |
| Next 注册桥无法读密钥或转发失败 | 503，`error_code="registration_proxy_unavailable"` |

管理接口依赖真实 `require_admin`：未登录返回 401，普通用户返回 403。
生成和撤销走既有 `log_admin_action`，分别记录 `invite_create` / `invite_revoke`；公开注册
没有已登录管理员身份，兑换者记录随存储事务写入邀请码历史，不伪装成管理员审计事件。

## 数据与故障恢复

`deeptutor/multi_user/auth_store.py` 为身份与邀请码提供共用可重入锁、私有 JSON 原子替换和
恢复日志。相关文件都在同一 runtime 的 `data/system/auth/` 下：

| 文件 | 用途 |
| --- | --- |
| `users.json` | 原身份库，bcrypt 密码哈希、角色、preset、token 版本等 |
| `invites.json` | 邀请码摘要、次数、有效期、撤销信息和兑换历史 |
| `registration-journal.json` | 仅未收尾事务存在；含用户及邀请码完整快照、事务 ID、校验摘要 |
| `registration_proxy_secret` | Next→API 地址证明的独立 0600 密钥，不是 JWT 密钥 |
| `auth_secret` | 现有 JWT 签名密钥，Next 注册桥不读取 |

公开注册先预检邀请码，再在线程池计算 bcrypt，最后在锁内重新检查准入状态、码状态与用户名。
这允许撤销/并发注册在最终事务中按同一顺序裁决，而不依赖过时的预检结果。
事务先写并同步 journal，再替换两个数据文件，最后删除 journal。

- journal 提交前失败：原用户库及邀请码计数保持不变。
- journal 已提交后失败：恢复时完成两份快照替换；恢复完成前其他身份写入被阻止。
- HTTP 超时或响应丢失：账号可能已经提交，先尝试登录或让管理员检查兑换记录；不手工减次数。
  重试不会重复扣费，尚有效的码遇到同名返回 409，已耗尽的码仍返回统一 403。
- journal 损坏或校验不符：拒绝继续恢复与相关写入，不按“没有事务”处理。

停服务后整体备份 `data/`，使用户、邀请码和可能存在的 journal 保持同一时点；恢复时保留
journal 供系统自动完成提交，不单独删除或拼接两份库。文件替换使用 0600，auth 目录使用 0700。

## 限流、注册桥与运行配置

一个 IP 在 15 分钟窗口中第 10 次无效邀请码尝试返回 429，并从该次失败起冷却 900 秒。
成功注册清除这个 IP 的桶；过期桶被清理；最多 10000 桶，满表时新 IP 收到 60 秒重试。
这是单进程内存状态，重启后清空。状态查询、合法用户名冲突不算无效邀请码次数。

默认同机/同容器路径：浏览器 → Next Pages `/api/auth/register` → API。
`web/pages/api/auth/register.ts` 从真实 socket 取 IP；只有这个 socket 对端是 auth 配置中的
精确可信代理 IP 时，才由 Next 从右向左解释 XFF。Next 用独立密钥签名 IP、时间、方法、路径
和请求体 SHA-256；后端仅接受 60 秒内匹配当前请求体的 HMAC。

Python 后端永不信任未签名的 XFF，即使该地址出现在代理配置中也不采用；无证明时只按真实
socket peer 限流。这防止编码路径通过通用 rewrite 绕开专用桥后注入地址。默认 Next 桥能
保留各客户端地址，不把所有浏览器都算到后端看到的 Next 地址上。

`data/user/settings/system.json`（合并到已有配置）：

```json
{ "version": 1, "backend_workers": 1 }
```

`data/user/settings/auth.json`（合并到已有配置；默认无代理信任）：

```json
{ "version": 1, "enabled": true, "registration_trusted_proxies": [] }
```

真实反向代理部署把它的精确 IP 加入
`registration_trusted_proxies`；不接受 CIDR/任意来源，最多 32 项。Next 会读取该配置和
独立密钥；其 runtime 路径为 `DEEPTUTOR_HOME`，未设置时按 web 进程工作目录的上一级解析。
API 使用同一 runtime 数据树。先启动 API 生成密钥，再启动 Next；标准启动器/容器沿用此布局。
前端读不到密钥就返回 503，不降低地址验证要求。

Next 桥只接受 POST 和 JSON，请求体上限 8 KiB，向后端转发等待最多 30 秒，禁用缓存并保留
后端 `Retry-After`。超时不保证账号未创建，应按上面的提交点规则检查登录或兑换记录。

分离部署应仅向 Next 共享对应的桥接密钥与 auth 配置，并让 Next 指向正确 runtime；无需
共享 JWT 密钥。若反向代理绕过 Next 直接访问 API，请求按代理 socket 地址合用一个桶，
应在前置代理配置每 IP 限流，或回到标准 Next 注册路径。实际代理链需按下节验收。

## 验证记录

2026-09-23 最终代码验证结果如下。后端数字来自同一条命令，不再累加先前重叠的分批结果。

| 范围 | 已确认结果 | 说明 |
| --- | --- | --- |
| 后端目标组合 | **401 passed，3 warnings，38.73 秒** | `tests/multi_user`、邀请码 HTTP、auth context/logout、runtime settings；包含新增 11 项启动/信任配置回归 |
| 前端 Node 测试 | **1189 passed** | 完整 `npm run test:node` |
| 前端 Vitest | **83 文件、323 passed，0 unhandled errors** | 完整 `npm run test:unit`，包含 5 项可空导航回归 |
| 类型、契约、架构 | **通过** | typecheck、contracts check；Python 3 条边界契约、前端 847 个模块的架构检查 |
| i18n 与 lint | **通过** | lint 0 error，49 条既有 warning；i18n 检查通过 |
| Python 静态检查 | **通过** | Ruff 覆盖本轮 13 个文件；差异空白检查通过 |
| 生产构建 | **成功** | 独立输出目录 `.next-invite-qa` |
| Docker、真实外部反向代理、既有 WS 撤权验收 | **仍待验收** | 保留测试矩阵 G1/G3 等既有缺口；真实外部代理链未据单元测试宣称通过 |
| 真实 Chrome + Next + 完整 FastAPI 注册验收 | **通过** | 独立临时 runtime、生产构建、桌面/手机视口；发码、注册、登录、撤销、兑换记录、权限与限流，浏览器无 page error |
| API 重启持久化 | **通过** | 重启后 4 个测试账号、5 条邀请码、使用次数与撤销状态保留，账号可登录，bootstrap 仍关闭，无残留事务日志 |

上述后端组合的复现命令（仓库根目录，先激活已有开发环境）：

```bash
source .venv/bin/activate
pytest -q tests/multi_user tests/api/test_invite_registration.py tests/api/test_auth_contextvar.py tests/api/test_auth_logout_cookie.py tests/services/config/test_runtime_settings.py
python scripts/check_architecture.py
git diff --check
```

前端验证（`web/` 目录）：

```bash
npm run typecheck
npm run contracts:check
npm run architecture:check
npm run test:node
npm run test:unit
npm run i18n:check
npm run lint
DEEPTUTOR_NEXT_DIST_DIR=.next-invite-qa npm run build
```

本轮没有运行全仓库 `pytest -q tests deeptutor/learning/tests` 或完整 `npm run check`；后者还
包含构建后的性能预算步骤。上表只说明实际执行过的范围，不能据此补记未执行检查为通过。

本机浏览器验收使用独立 Chrome 上下文、完整 FastAPI 和 Next 生产构建，数据位于
`/tmp/deeptutor-invite-qa-24m55mot`，未使用真实账号数据。已确认空库首次创建管理员；
管理员批量生成并查看完整码；关闭结果后明文消失；手机宽度注册普通 Standard 账号并登录；
普通用户访问邀请码管理返回 403；使用次数与兑换者准确；撤销后无法注册。过期、并发和
中途故障由核心及 HTTP 自动化测试覆盖。

默认不信任代理时，连续变换 XFF 仍累计到同一限流桶，第 10 次失败返回 429。
随后只在临时 auth 配置中把本机设为可信反代，模拟两个来源 IP：A 被限流时 B 仍能通过
真实 Next 签名转发成功注册；编码路径及伪造签名不能换桶。此项验证实际 Next→API 链路，
不等于已经验证外部 Nginx/Docker 代理配置。重启 API 后账号、兑换计数、撤销状态与登录
均正常。验收完成后两个临时服务已停止。

已有 Docker A/B 隔离、外部反代和真实 WS 撤权验收缺口继续见
[测试矩阵](user-isolation-test-matrix.md)。
