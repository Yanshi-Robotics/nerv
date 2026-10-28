# 安装、更新与恢复

[English](../../guides/github-install.md) · [NERV](README.md)

这份指南对应 NERV `0.8.5`。公开安装器安装最新已发布版本，具体版本请查看[发布说明](https://github.com/Yanshi-Robotics/nerv/releases)。软件测试与真机验收、学生首次使用测试分别记录。

## 安装 NERV

Linux／macOS：

```bash
curl -fsSL https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.sh | sh
```

Windows PowerShell：

```powershell
irm https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.ps1 | iex
```

NERV 把托管的 CPython、uv 和应用包安装到用户目录中的 `.nerv`，使用标准 Python 虚拟环境及明确的解释器路径。系统 Python 和已有训练环境不会被安装程序修改。普通用户不需要 Git、Node.js、npm 或源码仓库；环境类型、存储位置及共享硬件的限制见[环境管理说明](environments.md)。

打开一个新终端，运行：

```bash
nerv
```

界面地址为 `http://127.0.0.1:8170`。不希望自动打开浏览器时使用 `nerv serve --no-browser`；指定端口使用 `nerv serve --port PORT`。端口被占用时，NERV 提示错误，不停止其他进程。远程或无桌面终端不会强行打开浏览器。

托管启动命令位于 `~/.nerv/bin`。如果系统已有其他 `nerv` 命令，在 POSIX 中用 `command -v nerv`，在 PowerShell 中用 `Get-Command nerv -All` 检查来源。旧 npm 启动器可能需要执行一次 GitHub 安装来注册托管命令；开发仓库不需要安装到 Node.js 中。

## 安装候选包

解压完整候选包，在解压目录中运行：

```bash
sh ./install.sh --bundle .
```

Windows PowerShell：

```powershell
./install.ps1 -Bundle .
```

安装器根据系统选择固定版本文件并校验完整性。候选包包含 NERV 包和清单，但不一定包含完整离线缓存，Python、uv 和第三方 wheel 仍可能需要下载。下载成果可以复用，未完成的环境不会被启用。

Shell 安装器支持 `--no-path`，用于不希望修改 shell 配置的临时安装。随后使用安装器给出的绝对命令路径，并在每次启动时保持相同的 `NERV_HOME` 设置。

## 准备组件与资产

先在“设置 → 系统与安装”查看电脑和 CPU／NVIDIA 方案，再从“组件与仿真”或 SO-101 引导中安装运行组件。已安装组件置顶；卡片可查看路径、下载进度、安装阶段、动态日志和恢复操作。安装 LeRobot 不会连接机械臂或使电机带电。

```bash
nerv runtime install lerobot0.6.1
nerv runtime install so101-sim
nerv runtime list
```

下载器按文件保存缓存，通过校验和及 HTTP 范围响应确认内容。短暂网络故障会在次数和等待上限内重试；服务器不支持续传或文件发生变化时，只重下受影响的文件。暂停和重新打开会保留可用片段。下载百分比表示文件字节进度，环境创建和检查另行显示，检查通过后才算安装完成。并发、带宽和代理在“系统与安装”中配置，详见[可恢复安装](recoverable-installation.md)。

场景资源与 Python 环境分别管理版本。“我的资产”支持安装官方场景、创建个人副本，以及导入带 NERV 清单的纯数据 MJCF 包，详见[资产管理](assets.md)。场景能加载和渲染，不代表任务已成功，也不代表任意训练策略都能在其中运行。

## 更新与恢复

先停止任务、断开设备并退出 NERV，再操作版本：

```bash
nerv update --check
nerv update
nerv rollback
```

检查更新只下载元数据。更新会选择兼容的精确清单，复用没有变化且已验证的环境，在暂存环境中安装变化部分，通过检查后再切换引用。已安装的官方组件及设备档案中的解释器引用一起处理；未知组件或配置格式会阻止启用新版本。失败时恢复原配置和引用，更新被异常中断后需运行 `nerv rollback` 再启动。

密钥和事件历史仍属于用户数据。恢复时，新配置会保留在提示的备份目录中，不会直接丢弃。外部 checkpoint 和标定文件保持原位置，不要通过删除整个 `.nerv` 来解决更新问题。

## 使用独立预览版

已有支持 `0.8.5` 命令的托管 Host 时，可把完整候选包安装到预览渠道：

```bash
nerv preview update --bundle /path/to/extracted/bundle
nerv preview
nerv preview rollback
```

预览使用端口 `8100`，会话、密钥、设备档案和可编辑资产放在 `.nerv/profiles/preview`，与正式渠道的数据分开。需要时明确复制一次模型配置，之后不持续同步，也不复制动作授权。不可变文件和兼容环境可由统一存储复用。

## 从源码构建

开发者在 Git 仓库中修改源码，然后执行仓库外构建：

```bash
nerv preview build --source /path/to/nerv
```

命令在 `.nerv` 中创建源码快照、准备固定版本工具链、执行检查并生成候选包，不自动启用候选版。用上面的预览更新命令安装它。首次开发和 CI 的引导方式见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。源码检出目录不应创建 `.venv`、`node_modules`、构建缓存或依赖符号链接。

## 平台限制

安装方案按操作系统、架构、Python ABI 和计算方案分别选择。Windows 与 WSL 不能共用虚拟环境；WSL 使用 Linux 包，但 USB、相机和 GPU 透传需要单独配置。macOS 使用 CPU 路线，不承诺 MPS。某个组件缺少兼容二进制包时，该组件在对应目标上不可安装。

软件检查不能代替 USB 权限、相机身份、电机配置、标定、扭矩行为和抓取可靠性的验收。请按 SO-101 引导操作，真机连接和动作由操作者执行。Linux 是主要真机验收目标，其他平台维持已声明的实验性硬件支持状态。
