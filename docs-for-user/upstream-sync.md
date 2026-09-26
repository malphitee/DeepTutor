# 同步官方发布并保留用户隔离

本 fork 从官方发布标签接收功能更新。同步目标是 `dev`，每次使用独立
`sync/upstream-vX.Y.Z` 分支和 PR；用户审核前不合并、不发布或升级部署。
日常修复仍遵循 `AGENTS.md` 的交付规则。

## 必须保留的行为

- 开启内置认证后，普通账号的私有数据与自建工作区必须限制在自己的
  `data/users/<uid>` 内。部署共享目录仅供管理员使用。
- 已保存的工作区目录、迁移源、链接知识库也要重新检查归属；旧 SQLite
  catalog 中的 `metadata.root` 不构成访问外部目录的授权。
- HTTP/WS 请求缺少身份时拒绝私有访问；禁用、删除、降级和密码变更使
  旧令牌失效，已在运行的连接/任务保留现有撤销语义。
- 管理员显式授权的知识库只读共享；同一账号内的资源复用及管理员授予的
  Partner 内容域按现有授权规则运行。普通用户的任意代码执行仍受限制。
- 保留邀请码准入、存储损坏时拒绝注册，以及多用户模式的 PocketBase 禁用规则。
- 保留 fork 的附件处理、QQ 修复和镜像交付规则。已删除的模型生成首页输入
  提示不能因上游合并恢复。

## 每次同步

1. 检查 `origin/dev`、工作树和正在运行的任务。固定 fork 基线、官方标签的
   commit SHA；阅读官方发布说明，标出数据迁移和废弃功能。
2. 官方与 fork 可能使用同名版本标签。只把目标官方标签取到独立名称，
   不覆盖已有 fork 标签。例如：

   ```bash
   git fetch --no-tags origin dev
   git fetch --no-tags upstream refs/tags/v1.6.11:refs/tags/upstream-v1.6.11
   git worktree add -b sync/upstream-v1.6.11 ../DeepTutor-upstream-v1.6.11 origin/dev
   cd ../DeepTutor-upstream-v1.6.11
   git merge --no-ff --no-commit upstream-v1.6.11
   ```

3. 发生冲突时整理具体行为差异、建议及影响，让用户确认处理方向。
   按确认结果合并功能与保护措施；对无文本冲突的权限、存储和 Compose
   改动也执行复核。保留真实 merge ancestry，便于下次增量同步。
4. 运行隔离门禁、完整后端测试、前端检查与架构检查；OpenAPI/TS 合同从
   最终后端重新生成。验证数据迁移时只使用临时数据或备份副本。
5. 提交、推送同步分支，向 fork 的 `dev` 提 PR，记录官方 SHA、冲突决策、
   测试结果、尚待部署验收的内容。用户审核后再走 dev 镜像发布流程。

## 检查与发布

`.github/workflows/user-isolation.yml` 对所有目标为 `dev`/`main` 的 PR 运行，
不使用路径过滤，因此 Compose-only 改动也受检。Docker 发布复用同一工作流，
隔离检查失败时不会开始镜像构建或推送。

本地执行同一组检查：

```bash
python -m pytest -q tests/multi_user tests/services/workspace tests/app/test_turn_scope_ownership.py tests/api/test_invite_registration.py tests/test_release_workflow_guards.py
```

门禁覆盖真实认证下的双账号知识库、会话互访，RAG 授权、部署共享根、
历史目录记录和迁移源校验。完整测试与真实 Docker 双账号验收仍需按
[隔离测试矩阵](user-isolation-test-matrix.md) 执行；通过门禁不等于证明所有入口都无缺陷。

`.github/pull.yml` 明确使用 `mergeMethod: none`：如果安装了 Pull App，它只能
提出待审核的同步 PR，不能自动合并或硬重置 fork。不要通过删除该文件恢复
App 的默认行为。GitHub 的 required checks 是仓库设置，须另外确认启用状态。

## 升级前已有外部工作区目录

普通用户的旧 catalog 若记录了账号目录之外的根，访问应报错；系统不会
自动把该目录当作私有目录，也不会自动搬移、合并或删除它。
管理员应先备份用户 catalog 和原文件，核实目录所属账号，再把属于该账号的
内容复制到其私有目录并修复登记。不能把两个账号原先共享的文件直接各自宣称为私有。
