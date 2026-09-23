# 镜像自动发布：GHCR 与 CNB

本 fork 使用 GitHub Actions 的 `Docker Release` 流程，一次构建同时推送到：

- `ghcr.io/malphitee/deeptutor`
- `docker.cnb.cool/johnnliu/deeptutor`

CNB 对应仓库为 <https://cnb.cool/johnnliu/deeptutor>。代码仍由 GitHub 提供，CNB 接收镜像。
构建目标为 Dockerfile 的 `production` 阶段，架构为 `linux/amd64` 和 `linux/arm64`。

## 触发与标签

| 事件 | 两个仓库上的相同标签 |
| --- | --- |
| 推送 `feature/user-isolation` | `feature-user-isolation`、`sha-<12 位提交号>` |
| 推送 `main` | `main`、`sha-<12 位提交号>` |
| 手动运行，选择分支 | 对应分支标签、`sha-<12 位提交号>` |
| 发布稳定 Release，如 `v1.2.3` | `1.2.3`、`latest`、`sha-<12 位提交号>` |
| 发布预发布版本，如 `v1.2.3rc1` | `1.2.3rc1`、`sha-<12 位提交号>`，不更新 `latest` |

分支推送不会覆盖稳定版本的 `latest`。Release 必须符合已有版本校验规则；带 `+` 的版本
元数据在镜像标签中转为 `-`。拉取当前邀请码/用户隔离开发版本应使用 `feature-user-isolation`。
规则仅在包含该工作流版本的分支生效；本轮先落在 `feature/user-isolation`，`main` 合入后
才会使用同一套规则。手动运行入口也需要默认分支包含 `workflow_dispatch`。

```bash
docker pull ghcr.io/malphitee/deeptutor:feature-user-isolation
docker pull docker.cnb.cool/johnnliu/deeptutor:feature-user-isolation
```

## 凭据与执行方式

GitHub 仓库的 Actions Secret `CNB_TOKEN` 保存 CNB 访问令牌；范围限定到上述 CNB 仓库，
具有 `registry-package` 读写权限。登录用户名固定为 `cnb`。GHCR 使用工作流自带的
`GITHUB_TOKEN` 和 `packages: write` 权限，无需额外的个人 GitHub 令牌。

两个仓库都先登录，再开始构建。缺少 CNB 令牌时明确失败，不静默省略 CNB 发布。
同一 Git ref 的任务串行执行，构建缓存使用 GitHub Actions。流程只在 `malphitee/DeepTutor`
运行发布任务，避免其他 fork 意外向这些固定地址发布。

前端 standalone 包含 Sharp 原生依赖，因此前端构建也必须匹配目标架构；不能将 x64 构建机
产生的整个 `node_modules` 直接复制进 ARM 镜像。ARM 首次构建可能因模拟执行耗时更长。

两次仓库上传不是跨平台事务；若一个上传失败，另一个可能已经完成。判断是否发布成功应看
工作流最终状态及两个仓库的镜像清单，不能仅凭登录成功。修复权限或网络后可以重新运行任务。
仓库的公开/私有设置由平台管理，工作流不自动修改可见性。

## 配置验证

本地验证发布条件、标签规则、双仓库登录与单次多架构构建配置：

```bash
./.venv/bin/pytest -q tests/test_release_workflow_guards.py tests/scripts/test_docker_compose.py
git diff --check
```

首次实际运行结果另以 GitHub Actions 的 `Docker Release` 记录为准。

参考：[Docker 多仓库推送](https://docs.docker.com/build/ci/github-actions/push-multi-registries/)、
[CNB Docker 制品库](https://docs.cnb.cool/zh/artifact/docker.html)。
