# 用户隔离测试矩阵（阶段 0 交付物）

对应 `user-isolation-development-plan.md` 阶段 0 的要求：为安全不变量建立
测试矩阵，标记现有覆盖与缺口。测试本体随阶段 1–5 落地，本表是它们与计划的
对照索引。运行方式：`pytest -q tests`（CI 配置见 `.github/workflows/tests.yml`）。

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

## 3. 已知缺口

| # | 缺口 | 说明 |
| --- | --- | --- |
| G1 | Docker A/B 端到端冒烟（阶段 7） | 需要构建镜像 + 双账号浏览器流程，未自动化；部署侧操作手册见 `user-isolation-deployment.md` §2 |
| G2 | 前端类型检查与架构检查 | 属阶段 7；Python 侧 `compileall`/`git diff --check` 已纳入流程 |
| G3 | WS 断连级集成测试 | revocation 的 token 失效与 fan-out 有单元覆盖；"禁用后既有 WebSocket 被服务端关闭"的端到端用例依赖真实 WS 握手，未单列 |
| G4 | 跨模块私有调用待公开化 | `manager._load_config()`、`PathService._scoped_path()`、`_terminate_revoked_user` 等 5 处（见 review 记录）；行为有测试覆盖，API 归位待定 |
| G5 | 上游同步回归矩阵（阶段 7） | 每次 Sync fork 后需人工过一遍本表 §2 |

## 4. 维护约定

- 新增隔离相关测试时，同时更新本表对应行；
- 上游同步（阶段 7）后按 §2 全表重跑并在 PR 描述里记录结果；
- 缺口表只增不删——关闭一项要写明关闭方式（测试/文档/决策）。
