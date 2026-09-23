# ADR-0005：邀请码准入注册

## 状态

Accepted。2026-09-22 确定邀请码准入原则；2026-09-23 实现阶段补全以下具体选择。
代码落在 `feature/user-isolation`，以 `7ed6c990` 为本轮修改基线；本轮没有将最新
`main` 合入功能分支，避免将上游同步与准入变更混在一起。

详细操作、接口和验证记录见 [邀请码注册实现记录](../invite-registration-implementation.md)。
“已实现”与“已完成部署验收”分别记录，测试结果不替代真实部署验收。

## 背景

原内置认证只允许首次公开注册，后续账号由管理员逐个创建。邀请码让管理员控制准入，
受邀者自行填写账号密码，同时保留管理员直接建号与 learner 等预设配置。
不增加第二套用户系统，也不增加开放注册模式；身份继续存于 `data/system/auth/users.json`。

## 决定及理由

### 1. 准入与账号权限分开

- 只有用户库可正常读取、确实没有账号，且 `auth.json` 没有同时配置有效的
  `username` / `password_hash` bootstrap 管理员时，才能无码注册为首个 admin。
  有普通账号但没有管理员仍不属于首次注册；损坏或无法读取身份库返回不可用，不能视为空库。
- 其余公开注册必须提供有效邀请码。新账号固定 `role=user`、`preset=standard`，
  立即启用，不自动创建 grant，不接受请求中的 role/preset 提权；个人模型配置或管理员授权
  是注册后的独立操作。这样邀请码只决定“能否加入”，不隐含资源权限。
- 管理员 `POST /api/auth/users` 继续可用。公开注册和管理员创建使用只创建不覆盖的路径，
  共用唯一性锁；原 `save_user` 的更新语义保留，避免破坏已有账号管理调用。
- 认证关闭或 PocketBase 模式不提供本功能。不存在额外的 `registration_mode` 开关。

### 2. 邀请码格式、生命周期与展示

| 属性 | 决定 |
| --- | --- |
| 格式 | 12 位 Crockford Base32，约 60 位随机性，显示为 `XXXX-XXXX-XXXX` |
| 输入归一化 | 去除首尾空白、忽略连字符和大小写；`O→0`、`I/L→1`，减少手工输入误差 |
| 使用次数 | `max_uses` 默认 1，整数范围 1–1000 |
| 有效期 | `expires_in_days` 默认 7，整数范围 1–365；`null` 永久有效 |
| 批量与备注 | `batch_count` 默认 1，整数范围 1–100；`note` 默认空字符串，最多 200 字符 |
| 存储 | 只保存归一化码的 SHA-256 摘要和末四位 `code_hint`；完整码仅生成响应返回一次 |
| 管理视图 | `active` / `expired` / `exhausted` / `revoked`，以及兑换者 ID、用户名与时间 |

列表、撤销响应和审计不返回完整码或摘要。前端只将生成结果保存在当前组件内存中，关闭
结果、切换 tab 或离开页面后无法找回；丢失时撤销旧码并重发。撤销只阻止未来注册，
不禁用已经创建的账号；删除账号不返还次数，原兑换记录继续保留。

### 3. 兑换与身份创建共同提交

仅分别原子替换两个 JSON 文件不能保证中途退出后的用户库和邀请码库一致。因此增加
`auth_store.py`，全部身份及邀请码写入共享进程内可重入锁，并采用先写日志的恢复协议：

1. 在锁内重新检查 bootstrap、邀请码状态和用户名唯一性；bcrypt 前的检查只是预检。
2. 先持久化含两份完整快照及校验摘要的 `registration-journal.json`；日志持久化即提交点。
3. 原子替换 `users.json` 和 `invites.json`，完成后删除日志；后续访问先恢复尚未完成的日志。

提交点前失败不创建账号、不扣次数。提交点后即使响应丢失或第二个文件写入失败，也只能
向前完成提交（roll forward），不能删除已提交账号或返还次数。恢复失败继续拒绝访问，
不允许其他身份写入跨过未完成的事务。文档不再承诺“所有失败一律回滚”。

### 4. 错误与限流

- 缺失、格式错误、未知、过期、耗尽和撤销的码统一返回
  `403 {"detail":"A valid invitation code is required.","error_code":"invalid_invite"}`。
  先检查有效码，再返回用户名冲突 `409 username_taken`；冲突不扣次数。
- 存储错误返回 `503 identity_store_unavailable`。参数格式和边界错误返回 422。
- 单个 IP 在 15 分钟窗口内累计 10 次无效邀请码尝试，第 10 次即返回
  `429 registration_rate_limited`，冷却 900 秒，并返回 `Retry-After`。成功注册清除此 IP 的桶。
- 内存最多保留 10000 个 IP 桶，清理过期桶；表满时新 IP 返回 60 秒重试，不能靠挤掉已锁定
  的 IP 绕过限制。进程重启会清空内存限流状态。

### 5. 真实客户端地址与部署边界

默认前端由 Next 转发请求，直接用后端 socket 地址会把所有浏览器合并到一个限流桶；直接
信任浏览器的 `X-Forwarded-For` 又可绕过限流。因此为 `POST /api/auth/register` 增加 Next Pages
API 桥，从 `req.socket.remoteAddress` 取地址，并用独立密钥对 IP、时间、方法、路径和 body 摘要
做 HMAC。后端只接受 60 秒内的有效证明。

- 独立密钥为 `data/system/auth/registration_proxy_secret`，由后端启动时创建，权限 0600。
  Next 不读取 JWT 签名密钥，也不把桥接密钥交给浏览器。
- `auth.json.registration_trusted_proxies` 默认 `[]`，仅接受最多 32 个精确代理 IP。
  仅 Next 桥读取这个列表：只有 socket 对端受信时才从右向左解析 `X-Forwarded-For`。
  Python 后端永不信任未签名的 XFF，避免编码路径经通用 rewrite 绕过桥后伪造 IP。
- 无有效签名时，后端只用自己的真实 socket 对端。前端无法读取共享 runtime 中的桥接密钥
  时返回 503，不改用客户端头、不静默退回共用桶。
- 默认同机/同容器部署由 Next 与 API 读取同一 runtime 数据目录；分离部署需向前端提供相同
  独立密钥和 auth 信任配置。绕过 Next、由反向代理直达 API 的请求按代理 peer 共用限流桶；
  应由前置代理补充每 IP 限流，或使用标准 Next 注册路径。

首版强制 `backend_workers=1`：内置认证启动检查和注册/邀请码接口均拒绝多 worker。
进程内锁、JSON 恢复协议与限流桶不提供跨进程一致性，因此不以“每个 worker 独立计数”
冒充多 worker 支持。

### 6. 用户界面与范围

`/admin/users` 只有“用户”和“邀请码”两个 tab，本次不补设备凭证 UI。邀请码 tab 支持批量
生成、一次性复制、分页查看状态/兑换历史、确认撤销。设备登录与邀请码注册保持独立。
注册页读取专用 `/api/auth/registration-status`，根据实际状态显示邀请码输入，状态读取失败
时禁止提交并允许重试。注册完成后跳转登录，不自动登录；受邀用户看到个人模型/管理员授权提示。

### 7. Pages API 引入后的导航兼容

获取真实 socket peer 需要 Pages API；它与 App Router 并存后，Next 会将 `usePathname`、
`useSearchParams`、`useParams` 的类型扩展为可空。本轮因此修复 36 处 UI 导航调用，并保留
`web/types/navigation-compat.d.ts`，使日常 typecheck 与生产构建使用同样的可空类型。
Pages 目录同时纳入类型检查和架构扫描，不仅在构建时才发现遗漏。

Knowledge、Reading、Watching、登录跳转和学习资源创建等流程等待所需路由参数就绪后再
初始化；不能把“尚未就绪”当成“没有深链参数”而提前覆盖 URL 或消费回调。新增 5 项可空导航
回归验证这类状态切换。范围扩大来自真实 socket 桥的兼容要求，不改变页面既有业务权限。

## 结果与后续

不生成邀请码的部署仍无法新增公开注册账号。旧客户端仅在真正 bootstrap 时可以无码注册，
后续得到统一邀请码错误。原账号、管理员直建账号、原 `save_user` 更新路径保留。

后续若需要多 worker，应先替换进程内锁和限流状态，并为事务存储提供跨进程保证；这不属于
当前实现范围。当前验收缺口、执行命令和最终测试数字集中记录在
[实现记录](../invite-registration-implementation.md#验证记录)，由最终验证结果更新。
