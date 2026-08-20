# git-worktree-env (`wte`)

[English](README.md)

为每个 Git Worktree 自动分配端口、挂载本地 Secrets，并准备独立的开发环境。

`wte` 通过一份项目配置识别专属的 main worktree。由该 main worktree 创建的
所有 linked worktree 都会获得稳定且互不冲突的端口，以及项目声明的本地配置。

## 功能

- 从全机共享端口池分配连续端口，并使用文件锁避免并发冲突。
- Worktree 路径存活期间保持端口稳定。
- 通过 main worktree 识别 Cursor、IDE 和 Agent 创建的 linked worktree。
- 将仓库外的 Secrets 以软链接挂载到 Worktree。
- 根据端口模板生成完整的 Worktree 配置文件。
- Checkout 后可在后台初始化依赖，不阻塞 Git。
- 使用全局 Hook 分发器，同时保留仓库已有 Hooks。
- 通过宿主机目录监控补偿沙箱工具创建的 Worktree。

## 环境要求

- macOS 或 Linux
- Python 3.9+
- Git

由于使用 POSIX 文件锁、软链接和 Bash Hook，目前不支持 Windows。

## 安装

推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv tool install git-worktree-env
```

从源码安装开发版本：

```bash
uv tool install --editable .
```

也可以使用：

```bash
pipx install git-worktree-env
```

## 快速开始

```bash
wte setup
cp ~/.config/wte/project.example.yaml.template ~/.config/wte/my-project.yaml
$EDITOR ~/.config/wte/my-project.yaml
wte setup  # 新增或修改 Profile 后刷新监控目录
cd /path/to/a/linked-worktree
wte sync
wte doctor
```

每个项目必须拥有独立的 main worktree：

```yaml
name: example-fullstack

match:
  main_worktree: $HOME/code/example-app

ports:
  - id: frontend
  - id: backend

secrets:
  - source: $HOME/.config/example-app/backend.env
    target: apps/backend/.env

writes:
  - path: apps/frontend/.env.development
    body: |
      VITE_PORT=${frontend}
      VITE_API_URL=http://127.0.0.1:${backend}

init:
  - command: npm install
    cwd: .
    skip_if: node_modules
```

完整配置见 [`examples/fullstack.yaml`](examples/fullstack.yaml)。

## 命令

```text
wte setup       创建配置和项目模板，并安装 Hooks 与宿主机监控
wte sync        将当前 Worktree 与对应的项目 Profile 同步
wte list        查看仍存活的 Worktree 端口
wte doctor      诊断配置、Profiles、注册表、Secrets 和 Hooks
wte uninstall   卸载 Hooks 与监控，但保留配置和运行状态
```

在需要修复或刷新的 Worktree 内执行 `wte sync`。它会分配或复用端口、重新创建
Secrets 软链接并生成配置文件，但不会执行依赖初始化命令。

只有 `post-checkout` 会执行 wte。其他 Hook 入口只负责转发安装前的全局 Hook
或仓库自己的 Hook。若已设置全局 `core.hooksPath`，`wte setup` 默认拒绝覆盖；
使用 `--force` 时会记录并继续转发原有 Hooks。内部 Hook 入口不会出现在公开 CLI
或文档中。

## 沙箱 Agent 创建的 Worktree

部分编码 Agent 创建 Worktree 时不会执行用户的全局 Git Hooks。`wte setup` 会安装
操作系统管理的目录监控作为补偿：

- macOS 使用带 `WatchPaths` 的 LaunchAgent。
- Linux 使用 systemd user path unit。

操作系统监控各项目 `.git/worktrees` 元数据目录。目录变化时只启动一次短生命周期
的隐藏 Reconciler；不会常驻 Python Daemon，也不进行定时轮询。Reconciler 会比较
`git worktree list --porcelain` 与 `ports.json`，只投影尚未注册的 Worktree，并且
不会执行依赖初始化命令。

新增或修改 Profile 后需要再次执行 `wte setup`，以刷新监控路径。在非严格沙箱中
执行 `wte sync` 也会刷新监控。`wte doctor` 会报告监控状态。

端口分配、Secrets 软链接、配置文件生成和注册表更新位于同一个短事务中。如果投影
失败，注册表不会留下记录，后续文件事件可以直接重试，不需要 Profile 指纹。

## 配置与运行数据

默认统一存放在：

```text
~/.config/wte/
├── config.yaml
├── project.example.yaml.template
├── my-project.yaml
├── another-project.yaml
├── hooks/
└── state/
    ├── ports.json
    ├── ports.lock
    ├── hooks-state.json
    └── reconciler.log
```

可以使用 `WTE_CONFIG_HOME` 修改位置；未设置时也会遵循 `XDG_CONFIG_HOME`。

根目录中除 `config.yaml` 外的 YAML 文件都会作为项目 Profile 加载。`state/`
由 wte 自动维护，不应手动编辑或同步。全机端口分配范围在 `config.yaml` 中配置：

```yaml
port_range:
  start: 20000
  end: 29999
```

默认范围为 `20000-29999`，低于常见操作系统临时端口范围和 Kubernetes NodePort
范围。Worktree 路径删除后，会在下一次手动同步或自动
Checkout 投影时回收。注册表采用原子写入，文件损坏时会明确报错，不会静默当成
空注册表。

## 安全模型

Profile 是受信任的本机配置。Secrets 内容不会写入端口注册表，源文件始终保留在
仓库外。Secrets 和生成文件的目标必须位于匹配的 Worktree 内。初始化命令会交给
Bash 执行，因此不能使用来源不可信的 Profile。

## 开发

```bash
uv sync
uv run pytest
```

## 许可证

[MIT](LICENSE)
