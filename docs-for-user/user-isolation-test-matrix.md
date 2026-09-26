# 用户隔离测试矩阵（阶段 0 交付物）

对应 `user-isolation-development-plan.md` 阶段 0 的要求：为安全不变量建立
测试矩阵，标记现有覆盖与缺口。测试本体随阶段 1–5 落地，本表是它们与计划的
对照索引。运行方式：`pytest -q tests`（CI 配置见 `.github/workflows/tests.yml`）。

官方发布同步另有无路径过滤的 `.github/workflows/user-isolation.yml` 门禁，
Docker 发布在该门禁成功后才开始。流程见 [上游同步说明](upstream-sync.md)。
新增 `tests/multi_user/test_release_sync_isolation.py` 覆盖真实认证下的双账号
知识库/会话及 RAG 授权和共享目录配置；
`test_legacy_workspace_root_isolation.py` 覆盖旧 catalog 外部根和迁移源越界。

## 1. 安全不变量 → 测试映射

| # | 不变量（计划 §2） | 覆盖测试 |
| --- | --- | --- |
| 1 | 每个请求先解析 `CurrentUser`；scope 解析失败必须报错 | `tests/multi_user/test_scope_fail_closed.py::test_active_request_without_identity_rejects_path_service`、`test_active_request_does_not_use_rag_admin_fallback`；边界在 `deeptutor/multi_user/request_scope.py`（ASGI 中间件），上下文语义在 `tests/api/test_auth_contextvar.py`（#481 回归组） |
| 2 | 路径只能由 `PathService` 产生，业务代码不得自行拼接 | `tests/multi_user/test_identity_and_paths.py::test_path_service_uses_current_user_scope`、`tests/multi_user/test_owner_path_service.py`、`tests/services/test_path_service_runtime_home.py` |
| 3 | session/turn/事件/记忆/KB/笔记/文件/workspace 全部落自己 scope | `tests/multi_user/test_resource_isolation.py`、`tests/services/workspace/test_content_workspace.py`（写入限定 outputs 等）、`tests/multi_user/test_session_cleanup.py::test_delete_session_cleans_only_current_user_artifacts`、`tests/services/session/test_pocketbase_isolation.py` |
| 4 | 拿到他人 turn ID：读取/订阅/取消/输入一律"不存在"，不触发 coordinator | `tests/app/test_turn_application_service.py::test_turn_operations_cannot_cross_session_store_ownership`、`tests/app/test_turn_scope_ownership.py`（含无凭据 store fail-closed：`test_store_without_scope_evidence_fails_closed_for_non_admin`） |
| 5 | 共享数据仅经显式 grant；默认无跨用户权限 | `tests/multi_user/test_grants_and_settings.py`、`tests/multi_user/test_kb_manifest_access.py::test_ungranted_admin_kb_yields_no_manifest`、`tests/knowledge/test_manager_list.py`、`test_manager_delete.py` |
| 6 | 禁用账号的旧 token/WS/等待交互不能再创建任务 | `tests/multi_user/test_auth_revocation.py`（token 失效、fan-out、降级取消 in-process turn）、`tests/multi_user/test_env_bootstrap_admin.py`、WS 注销清理在 `deeptutor/multi_user/revocation.py` + `request_scope.py`（集成覆盖见缺口 G3） |
| 7 | 不能经 settings/环境变量/穿越/符号链接/绝对路径越界 | `tests/multi_user/test_scope_fail_closed.py`（symlink 全家桶：workspace、memory、attachment、file_library、KB 指针、grant 路径）、`tests/services/workspace/test_content_workspace.py::test_deployment_root_locks_user_selection`、`test_workspace_id_cannot_be_reused_by_another_user` |
| 8 | 普通用户 `exec` 默认关闭 | `tests/multi_user/test_scope_fail_closed.py::test_ordinary_exec_is_rejected_before_workspace_mutation`、`test_renderer_rejects_ordinary_user_before_subprocess`、`test_ordinary_cli_app_is_rejected_before_executable_lookup`；`tests/services/sandbox/test_sandbox.py` |

身份/账号生命周期补充：`tests/multi_user/test_env_bootstrap_admin.py`（#849
bootstrap 管理员）、`test_registration_invite_only.py`（首注册提升）、
`test_device_credentials.py`、`test_login_username_contract.py`、
`tests/api/test_auth_contextvar.py`（过期 admin claim fail-closed、PB 模式
role 保留）。启动隔离模式声明：`tests/multi_user/test_isolation_mode.py`。

邀请码注册新增覆盖见 §2.1；完整选择与验收记录见
[邀请码注册实现记录](invite-registration-implementation.md) 和 [ADR-0005](adr/0005-invite-code-registration.md)。

## 2. 计划 §6 验收场景 → 现状

| 场景 | 状态 | 证据 |
| --- | --- | --- |
| A/B 同时创建 session、turn、事件，只见自己的 | ✅ | `test_turn_operations_cannot_cross_session_store_ownership`、`test_resource_isolation.py`、`test_pocketbase_isolation.py` |
| A 用 B 的 session/turn ID → 不存在且不触发 coordinator | ✅ | `tests/app/test_turn_scope_ownership.py`（coordinator mock 断言零调用） |
| A 订阅 B 的实时流 → 空流且不泄露事件 | ✅ | 同上（`subscribe_turn` 断言返回 `[]` 且 store 未被读） |
| A 读 B 的 memory、KB、notebook、output → 拒绝 | ✅ | `test_scope_fail_closed.py`（memory/attachment/file_library/KB 分支）、`test_kb_manifest_access.py`、`test_session_cleanup.py` |
| A 伪造 settings 指向 deployment root 或兄弟目录 | ✅ | `test_deployment_root_locks_user_selection`、`test_workspace_id_cannot_be_reused_by_another_user`、binding 复验在 `deeptutor/services/workspace/service.py` |
| 相对路径穿越、绝对路径、符号链接 → 拒绝 | ✅ | `test_scope_fail_closed.py::test_user_linked_path_cannot_escape_own_workspace` 等 symlink 组、`test_grant_path_rejects_traversal_and_symlinks` |
| 管理员兼容 workspace 保持可用 | ✅ | `test_env_bootstrap_admin.py`、`test_admin_can_use_direct_manim_execution`、`test_path_service_defaults_to_deeptutor_home` |
| 管理员授予只读 grant → 只能读 | ✅ | `test_grants_and_settings.py`、`test_kb_manifest_access.py`（跨模块私有调用审计见缺口 G4） |
| 账号禁用后旧 token 不能新建请求/turn | ✅ | `test_disabled_or_deleted_user_cannot_reuse_old_token`、`test_role_demotion_cancels_in_process_turn_tasks` |
| 普通用户 exec 默认拒绝 | ✅ | 不变量 8 所列 |

### 2.1 邀请码准入注册（2026-09-23）

以下为代码中已有的测试覆盖；“有测试”与“最终完整回归/真实部署验收已通过”分别记录。

| 场景 | 覆盖测试 |
| --- | --- |
| 可读空用户库且无配置管理员才允许无码首注册；并发只能产生一个首管理员 | `tests/multi_user/test_invite_store.py::test_bootstrap_is_atomic_and_code_free`、`test_configured_admin_requires_code_and_name_stays_reserved`；`tests/api/test_invite_registration.py` 首注册/bootstrap overlay HTTP 用例 |
| 损坏/不可读用户库、邀请码库、事务日志不得重新开放 bootstrap 或被静默覆盖 | `test_invite_store.py::test_damaged_users_never_reopen_bootstrap`、`test_unreadable_users_fail_closed`、`test_damaged_invites_are_not_replaced`、`test_corrupt_journal_fails_closed`；API 的 corrupt-store 503 用例 |
| 后续无码/错误/过期/耗尽/撤销统一拒绝；有效码后才检查用户名冲突，不扣次数 | `test_invite_store.py::test_expiry_revocation_and_exhaustion_have_same_error`、`test_conflict_does_not_consume_and_deletion_does_not_refund`；API 对应状态码与计次断言 |
| 用户创建与兑换不超额，并发管理员直建同名不能覆盖 | `test_invite_store.py::test_parallel_redemptions_never_exceed_capacity`、`test_admin_create_and_invite_registration_share_uniqueness_lock`、`test_create_only_cannot_overwrite_existing_user` |
| journal 提交前失败不写两库；提交后中断/清理失败/新进程启动向前恢复且只扣一次 | `test_invite_store.py::test_failure_before_journal_commit_changes_neither_store`、`test_journal_recovers_after_interruption_between_store_writes`、`test_committed_journal_recovers_in_a_fresh_process`、`test_journal_cleanup_failure_rolls_forward_only_once`、`test_journal_checksum_blocks_corrupt_snapshot_replay` |
| bcrypt 前预检不能代替最终锁内复验；撤销与兑换有唯一串行顺序 | `test_invite_store.py::test_preflight_is_advisory_and_final_redemption_rechecks`、`test_revoke_and_redeem_have_one_serial_order` |
| 仅管理员可生成/分页查看/撤销；普通注册不能指定 role/preset；auth disabled/PB 不可用 | `tests/api/test_invite_registration.py` 真实 JWT 与 `require_admin` HTTP 用例；不通过 override 绕过管理员依赖 |
| 一次性明文、列表/审计不含明文或 hash、归一化输入、生成边界与分页 | `test_invite_store.py::test_code_defaults_private_storage_and_public_views`、`test_normalization_and_permanent_invite`、`test_pagination_and_batched_creation`；API 脱敏/边界用例 |
| 第 10 次无效邀请码返回 429，冷却 900 秒，成功清桶、过期清理、满表不驱逐锁定 IP | `tests/multi_user/test_registration_limits.py`；API 冷却恢复与成功清桶用例 |
| 真实客户端地址证明、伪造/过期/修改请求体的证明无效，独立密钥权限与稳定性 | `test_registration_limits.py` 的 proof/secret 用例；`web/tests/registration-proxy.test.ts`；后端永不采用未签名 XFF |
| 多 worker 启动被拒绝、桥接密钥只在合适模式创建、可信代理仅保存有界精确 IP | `tests/multi_user/test_registration_startup.py`、`tests/services/config/test_runtime_settings.py`；新增 11 项已包含在本轮后端组合中 |
| 注册状态/邀请码输入/错误与冷却、注册后跳登录；用户/邀请码两 tab；一次性展示与撤销交互 | `web/tests/auth-registration.spec.ts`、`invite-registration.spec.tsx`、`invite-manager.spec.tsx`、`admin-invite-tabs.spec.tsx` |
| Pages API 共存时导航 hooks 暂时为 null，深链/回调应等路由就绪后处理 | `web/tests/navigation-compat.spec.tsx`、`navigation-ready-components.spec.tsx`，共新增 5 项 |

本轮同命令后端组合 **401 passed、3 warnings、38.73 秒**；完整前端 Node **1189 passed**，
完整 Vitest **83 文件/323 passed/0 unhandled errors**；typecheck、contracts、架构、i18n、
lint、Ruff 与独立目录生产构建通过。lint 有 49 条既有 warning、0 error。
实际范围与命令见 [实现记录的验证表](invite-registration-implementation.md#验证记录)，未运行
全仓库 pytest 或完整 `npm run check`。真实 Chrome/Next/FastAPI 隔离环境验收与 API 重启
持久化检查已通过；可信反代来源 IP 由本机临时配置模拟，未据此声称外部反代部署通过。

设备 heartbeat 既有回归测试已固定从当天 UTC 中午开始，避免午夜前五分钟运行时意外跨日；
保留显式日切测试。这是测试时钟稳定性修复，不是生产 heartbeat 行为变化。

## 3. 已知缺口

| # | 缺口 | 说明 |
| --- | --- | --- |
| G1 | Docker A/B 端到端冒烟（阶段 7） | 2026-09-21 以源码构建镜像人工执行一轮通过（注册提升/建号/KB 可见性/交叉 404/禁用 401/runner 加固与健康检查，见 `user-isolation-deployment.md` §2）；仍未自动化，每次同步后需按文档 §2 重跑 |
| G2 | 前端类型检查与架构检查 | `npm run typecheck` 已纳入同步后验证；Python 侧 `compileall`/`git diff --check` 已纳入流程 |
| G3 | WS 断连级集成测试 | revocation 的 token 失效与 fan-out 有单元覆盖；"禁用后既有 WebSocket 被服务端关闭"的端到端用例依赖真实 WS 握手，未单列 |
| ~~G4~~ | 跨模块私有调用公开化 | **已关闭**（commit 489688e2）：`terminate_revoked_user`、`PathService.scoped_path`、`KnowledgeManager.reload_config`、`safe_memory_child` 已公开化并迁移全部调用方 |
| G5 | 上游同步回归矩阵（阶段 7） | 2026-09-21 同步 upstream/main（33 提交）时执行过一轮：§2 全表通过；冲突解法与 ADR 恢复见 git log。每次 Sync fork 后重复 |
| G6 | 本地沙箱测试环境差异 | `test_runner_server_executes_and_truncates_output` 依赖 PATH 上的 `python` 命令：CI/激活的 venv 可通过，直接调用 `.venv/bin/python -m pytest` 会 127。属本地运行方式问题，非代码缺陷 |
| G7 | 邀请码注册外部部署验收 | 后端 401 项、完整 Node/Vitest、生产构建、真实 Chrome + Next + 完整 FastAPI 本机隔离环境和重启持久化验收均通过。Docker 与真实外部反向代理链仍待验收，不能关闭 G1/G3 既有缺口；详见 `invite-registration-implementation.md` |

## 4. 维护约定

- 新增隔离相关测试时，同时更新本表对应行；
- 上游同步（阶段 7）后按 §2 全表重跑并在 PR 描述里记录结果；
- 缺口表只增不删——关闭一项要写明关闭方式（测试/文档/决策）。
