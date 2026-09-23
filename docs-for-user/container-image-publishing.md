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
| 合并到 `dev`，或直接推送 `dev` | `dev`、`dev-<12 位提交号>` |
| 推送稳定版本 tag，如 `v1.2.3` | `1.2.3`、`latest` |
| 推送预发布 tag，如 `v1.2.3-rc.1` | `1.2.3-rc.1`，不更新 `latest` |

普通功能分支和 `main` 的 push 不构建镜像；只有 `dev` 与 `v*` tag 的非删除 push 才发布。
发布 tag 统一采用 SemVer 中以下格式，所有数字均禁止多余前导零：

- 稳定版：`vX.Y.Z`，例如 `v1.2.3`。
- 预发布：`vX.Y.Z-alpha.N`、`vX.Y.Z-beta.N`、`vX.Y.Z-rc.N`。

只有稳定版更新 `latest`。不接受 `v1.2.3rc1`、`.post1`、`.dev1`、`+build.1` 等其他写法；
提交号通过测试镜像标签和 OCI 的 revision label 追溯。GitHub Release 不再触发 Docker 发布；
PyPI 仍在发布 GitHub Release 时触发，但使用相同的 Git tag 格式校验。

## 应用版本一致性

发布前必须先更新并提交 `deeptutor/__version__.py`。Docker 在构建前读取这个唯一版本来源，
校验它与 Git tag 规范化后一致；不一致就失败。前端页面和 CLI 也读取此文件。

| Git tag | Python `__version__` 示例 | 镜像版本 |
| --- | --- | --- |
| `v1.2.3` | `1.2.3` | `1.2.3` |
| `v1.2.3-alpha.1` | `1.2.3a1` | `1.2.3-alpha.1` |
| `v1.2.3-beta.2` | `1.2.3b2` | `1.2.3-beta.2` |
| `v1.2.3-rc.1` | `1.2.3rc1` | `1.2.3-rc.1` |

这是同一版本在 SemVer 标签与 Python PEP 440 中的表示差异。`dev` 日常构建不要求每次修改版本号。

本规则需要触发提交中包含新版工作流。当前功能分支和 `dev` 已同步；生产 tag 应打在包含该
工作流且已验证的提交上。如果从 `main` 发布，应先将相关代码合入 `main`。

```bash
docker pull ghcr.io/malphitee/deeptutor:dev
docker pull docker.cnb.cool/johnnliu/deeptutor:dev
```

测试通过并确认应用版本已匹配后，在准备发布的提交上打版本 tag 并推送。例如，以下 `v1.2.3` 仅为示例版本号：

```bash
git tag -a v1.2.3 -m "Release v1.2.3"
git push origin v1.2.3

docker pull docker.cnb.cool/johnnliu/deeptutor:1.2.3
# 或跟随最近发布的稳定版本
docker pull docker.cnb.cool/johnnliu/deeptutor:latest
```

仅在本地打 tag 不会触发 GitHub Actions，必须将 tag 推送到 `origin`。
`dev` 和 `latest` 是可变别名。`latest` 跟随最近一次成功发布的稳定版本，包括对已有相同镜像
重跑旧 tag 的发布；严格固定部署版本可使用发布任务给出的镜像 digest。

## 已发布版本与失败恢复

CI 在写入任何生产标签前检查两个仓库的版本标签：不存在才创建；已存在时必须与本轮两种架构
的 digest 相同，否则拒绝全部写入。同一个版本标签不会被重新构建出的不同镜像覆盖。
两个仓库的版本镜像校验一致后，才更新 `latest` 等别名。

如果仅一个仓库成功，使用 GitHub Actions 的 **Re-run failed jobs** 重跑失败的汇总任务。
平台 digest artifacts 保留 7 天；重跑复用原始 digest，只补齐缺失的版本。若重跑全部构建导致
产物变化，已有版本保护会阻止覆盖，应使用新的版本发布。
这些检查约束本工作流；仓库外部的手动推送仍取决于注册表自身的权限和不可变标签设置。

## 凭据与执行方式

GitHub 仓库的 Actions Secret `CNB_TOKEN` 保存 CNB 访问令牌；范围限定到上述 CNB 仓库，
具有 `registry-package` 读写权限。登录用户名固定为 `cnb`。GHCR 使用工作流自带的
`GITHUB_TOKEN` 和 `packages: write` 权限，无需额外的个人 GitHub 令牌。

两个仓库都先登录，再开始构建。缺少 CNB 令牌时明确失败，不静默省略 CNB 发布。
测试发布和生产发布各自串行执行，不同版本 tag 共用生产并发组。构建缓存保存在 GHCR，
按发布通道和架构分开写入，并在构建时读取两个通道的同架构缓存，详见下节。
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

## 构建缓存

仅使用 `type=gha` 时，即使 `scope=deeptutor-amd64` 相同，GitHub 的 Git ref 访问规则仍然
生效：新版本 tag 无法读取 `dev` 或其他版本 tag 的缓存。发布流程又不在默认 `main` 分支
push 时构建，因此无法依靠默认分支预热来解决，首次发新版本往往重新安装全部依赖。

现在使用独立的 GHCR BuildKit 缓存标签，复用现有 `GITHUB_TOKEN`，不增加密钥：

| 构建 | 写入缓存标签 | 读取缓存标签 |
| --- | --- | --- |
| `dev` / AMD64 | `buildcache-test-amd64` | `buildcache-test-amd64`、`buildcache-production-amd64` |
| 版本 tag / AMD64 | `buildcache-production-amd64` | 同上 |
| `dev` / ARM64 | `buildcache-test-arm64` | `buildcache-test-arm64`、`buildcache-production-arm64` |
| 版本 tag / ARM64 | `buildcache-production-arm64` | 同上 |

这些标签位于 `ghcr.io/malphitee/deeptutor`，是缓存元数据，**不能作为应用镜像部署**。
缓存不写入 CNB，也不会移动 `dev`、`latest` 或版本镜像标签。通道和架构分开写入，避免
并行构建覆盖另一通道或架构的缓存；`mode=max` 保存前端和 Python 中间构建阶段。
原 GHA 缓存保留为读取回退，不再重复上传，减少缓存导出耗时和每个版本的重复存储。

Dockerfile 已将依赖层放在源码之前：`npm ci` 仅受 Node 基础镜像与前端依赖清单影响，
Python 依赖安装仅受 Python 基础镜像、构建工具和 requirements 文件影响。
因此修改业务源码或版本号时，匹配的依赖层可以直接显示 `CACHED`，前端编译或后端源码层
仍按实际改动重建。基础镜像、依赖或安装指令发生变化时，重新安装属于正常行为。

首次采用此配置仍需预热缓存；找不到缓存时正常构建，缓存上传失败只影响后续加速，不能
据此认定缓存已可复用。后续两个不同版本 tag 的构建应检查
`importing cache manifest from ghcr.io/...:buildcache-...` 及 `npm ci` / `pip install`
对应步骤的 `CACHED`，以此确认跨版本命中；没有完成实测前不承诺具体耗时改善。
手动重跑旧版本 tag 使用的是旧工作流，不会自动获得新配置。

参考：[GitHub Actions 缓存访问范围](https://docs.docker.com/build/cache/backends/gha/#scope)、
[Registry 缓存与 mode=max](https://docs.docker.com/build/cache/backends/registry/)。

## 配置验证

本地验证版本匹配、发布条件、原生架构构建、版本保护及双仓库清单校验：

```bash
./.venv/bin/pytest -q tests/test_release_workflow_guards.py tests/scripts/test_validate_image_release.py tests/scripts/test_publish_image_manifests.py tests/scripts/test_docker_compose.py
git diff --check
```

实际运行结果以 GitHub Actions 的 `Docker Images` 记录为准。

参考：[Docker 多仓库推送](https://docs.docker.com/build/ci/github-actions/push-multi-registries/)、
[CNB Docker 制品库](https://docs.cnb.cool/zh/artifact/docker.html)。
