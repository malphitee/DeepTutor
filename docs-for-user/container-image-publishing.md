# 镜像自动发布：GHCR 与 CNB

本 fork 使用 GitHub Actions 的 `Docker Images` 流程，每种架构构建一次，同时推送到：

- `ghcr.io/malphitee/deeptutor`
- `docker.cnb.cool/johnnliu/deeptutor`

CNB 对应仓库为 <https://cnb.cool/johnnliu/deeptutor>。代码仍由 GitHub 提供，CNB 接收镜像。
测试和生产镜像均使用 Dockerfile 的 `production` 阶段，架构为 `linux/amd64` 和 `linux/arm64`。
测试环境因此验证与正式发布相同的打包方式；两者由 Git ref 和镜像标签区分。

## 触发与标签

| 事件 | 两个仓库上的相同标签 |
| --- | --- |
| 合并到 `dev`，或直接推送 `dev` | `dev`、`dev-sha-<12 位提交号>` |
| 推送稳定版本 tag，如 `v1.2.3` | `1.2.3`、`latest`、`sha-<12 位提交号>` |
| 推送预发布 tag，如 `v1.2.3-rc.1` 或 `v1.2.3rc1` | `1.2.3-rc.1` 或 `1.2.3rc1`、`sha-<12 位提交号>`，不更新 `latest` |

普通功能分支和 `main` 的 push 不构建镜像；只有 `dev` 与 `v*` tag 的非删除 push 才发布。
生产 tag 必须符合版本格式，纯 `vX.Y.Z` 才会更新 `latest`；带 `+` 的版本元数据在镜像标签中
转为 `-`。测试镜像的提交标签使用独立的 `dev-sha-` 前缀，不会覆盖生产提交标签。
GitHub Release 不再触发 Docker 发布，PyPI 的原有 Release 流程不变。

本规则需要触发提交中包含新版工作流。当前功能分支和 `dev` 已同步；生产 tag 应打在包含该
工作流且已验证的提交上。如果从 `main` 发布，应先将相关代码合入 `main`。

```bash
docker pull ghcr.io/malphitee/deeptutor:dev
docker pull docker.cnb.cool/johnnliu/deeptutor:dev
```

测试通过后，在准备发布的提交上打版本 tag 并推送。例如，以下 `v1.2.3` 仅为示例版本号：

```bash
git tag -a v1.2.3 -m "Release v1.2.3"
git push origin v1.2.3

docker pull docker.cnb.cool/johnnliu/deeptutor:1.2.3
# 或跟随最近发布的稳定版本
docker pull docker.cnb.cool/johnnliu/deeptutor:latest
```

仅在本地打 tag 不会触发 GitHub Actions，必须将 tag 推送到 `origin`。
`latest` 跟随最近一次成功发布的稳定版本，包括重跑旧 tag 的发布。严格固定部署版本可使用
发布任务给出的镜像 digest。

## 凭据与执行方式

GitHub 仓库的 Actions Secret `CNB_TOKEN` 保存 CNB 访问令牌；范围限定到上述 CNB 仓库，
具有 `registry-package` 读写权限。登录用户名固定为 `cnb`。GHCR 使用工作流自带的
`GITHUB_TOKEN` 和 `packages: write` 权限，无需额外的个人 GitHub 令牌。

两个仓库都先登录，再开始构建。缺少 CNB 令牌时明确失败，不静默省略 CNB 发布。
测试发布和生产发布各自串行执行，不同版本 tag 共用生产并发组。构建缓存使用 GitHub Actions，
并按架构分别保存；缓存仍受 Git ref 访问范围限制，首次 `dev` 或新 tag 可能需要完整构建。
流程只在 `malphitee/DeepTutor`
运行发布任务，避免其他 fork 意外向这些固定地址发布。

前端 standalone 包含 Sharp 原生依赖，因此前端构建也必须匹配目标架构；不能将 x64 构建机
产生的整个 `node_modules` 直接复制进 ARM 镜像。AMD64 使用 `ubuntu-24.04`，ARM64 使用
`ubuntu-24.04-arm` 原生运行器，避免模拟运行造成前端编译耗时过长。

每个架构先向两个仓库推送 digest，两个架构均成功后，汇总任务才更新上述标签。发布前检查
两仓库的源镜像架构，发布后核对所有标签的 AMD64/ARM64 子镜像和最终 digest 一致。

首次实测中，CNB 将构建附带的证明清单识别为 `UNKNOWN` 并拒绝上传。因此当前发布关闭
内嵌 provenance/SBOM，保留普通容器镜像和多架构清单。这是针对实测结果的兼容设置；
[CNB 官方文档](https://docs.cnb.cool/zh/artifact/supported-manifest-types.html)列出了其支持的
证明格式，后续重新启用前应实际验证兼容性。

两次仓库上传不是跨平台事务；若一个上传失败，另一个可能已经完成。判断是否发布成功应看
工作流最终状态及两个仓库的镜像清单，不能仅凭登录成功。修复权限或网络后可以重新运行任务。
仓库的公开/私有设置由平台管理，工作流不自动修改可见性。

## 配置验证

本地验证发布条件、标签规则、原生架构构建、双仓库发布及清单校验：

```bash
./.venv/bin/pytest -q tests/test_release_workflow_guards.py tests/scripts/test_docker_compose.py
git diff --check
```

实际运行结果以 GitHub Actions 的 `Docker Images` 记录为准。

参考：[Docker 多仓库推送](https://docs.docker.com/build/ci/github-actions/push-multi-registries/)、
[CNB Docker 制品库](https://docs.cnb.cool/zh/artifact/docker.html)。
