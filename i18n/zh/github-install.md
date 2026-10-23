# 安装、更新与恢复

[English](../../guides/github-install.md) · [NERV](README.md)

0.8.0 是供本地测试的开发版。完整发布包包含主程序、Runtime SDK、LeRobot 适配器、依赖锁文件、SO-101 仿真资源、安装器和 release.json。实机验收和学生首次使用测试尚未完成。

## 安装候选包

解压维护者提供的完整候选包，在该目录执行。Linux/macOS：

```bash
sh ./install.sh --bundle .
```

Windows PowerShell：

```powershell
./install.ps1 -Bundle .
```

安装器按需从官方来源安装固定版本的 uv，获取 Python 3.12，校验发布资产与依赖哈希，并创建独立环境。下载 Python 和依赖仍需联网。不会向训练环境安装内容；失败的环境不会成为当前主程序。

重新打开终端，使用户 PATH 生效：

```bash
nerv
```

默认地址为 http://127.0.0.1:8170。服务就绪后打开浏览器，远程或无桌面终端不强行打开。可使用 `nerv serve --no-browser`，或用 `nerv serve --port PORT` 指定端口。端口冲突时报告错误，不结束其他程序。

安装器会显示检测到的旧 nerv 命令。POSIX 使用 `command -v nerv`，PowerShell 使用 `Get-Command nerv -All` 检查路径。托管入口在 `~/.nerv/bin`；修改源码并不意味着旧 npm 安装已经更新。

## GitHub 一条命令安装

以下命令安装最新发布的开发版。已测试的平台和剩余验收项目见对应发布说明。

Linux/macOS：

```bash
curl -fsSL https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.sh | sh
```

Windows PowerShell：

```powershell
irm https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.ps1 | iex
```

下载来源是 GitHub Releases，不需要 npm 注册。Shell/PowerShell 脚本先启动安装器，随后按发布清单校验 wheel、组件与依赖。组织要求审查安装脚本时，应先下载并检查脚本。

## 安装组件

在“设置 → 组件与仿真”中安装或维护 SO-101 / LeRobot、SO-101 Simulation。界面显示安装阶段、组件包大小与可获得的下载进度。Python 依赖另外下载，LeRobot 张量依赖体积较大。浏览器刷新后保留进度，中断后可以重试；同时安装会被拒绝。

对应命令：

```bash
nerv runtime install lerobot0.6.1
```

```bash
nerv runtime install so101-sim
```

```bash
nerv runtime list
```

界面安装前需要关闭机器人实例。安装 LeRobot 组件不会连接机械臂。仿真通过 XML 加载、物理步进和双相机渲染检查后登记，这不代表任务成功。首版界面不开放任意仿真包导入。

## 数据、更新与回滚

`~/.nerv` 保存设备档案、方案及其版本、策略引用、模型配置、私有凭据、会话日志、安装任务、各版本环境和缓存。原模型与标定文件仍在用户选择的目录。设置 NERV_HOME 后，后续启动应继续使用同一个值。

```bash
nerv update --check
```

```bash
nerv update
```

```bash
nerv rollback
```

检查命令只获取发布信息。更新或回滚前先停止任务、断开设备、退出主程序。主程序仍在运行时，文件锁会阻止更新。更新先保存版本化配置快照，再为主程序和所有已安装的官方组件建立新环境，检查依赖、模块导入及命令入口，通过后统一切换。失败会恢复旧环境指针和配置。回滚恢复上一套主程序、组件与配置；密钥和事件历史保持当前状态。更新后新增的配置及策略引用会保存在命令返回的备份目录中，原始 checkpoint 不受影响。

更新会协调已安装的官方 Runtime 组件，并同步设备档案中的解释器引用。遇到未知组件或配置格式时，在切换前拒绝更新。更新中断后会阻止启动，请执行 nerv rollback 恢复。本版配置格式为 schema 1，不会尝试未经验证的格式迁移。更新时必须退出主程序。

POSIX 凭据采用私有文件权限，Windows 使用系统凭据服务。更新不把密钥搬进软件环境。包含私有数据的备份留在用户目录。

## 构建候选包

维护者需要 Python 3.12、uv、Node.js，以及已初始化的 world/policy 子模块。按 [CONTRIBUTING.md](../../CONTRIBUTING.md) 安装构建依赖后，在源码仓执行：

```bash
python scripts/build_release.py
```

构建结果位于 temp/release，包含网页、wheel、三个场景和依赖锁文件，不会自动发布。分发时应保留完整清单与安装脚本，不能只交付主程序 wheel。

## npm 兼容入口

安装了 Node.js 22+、npm 和 uv 的开发者仍可从源码安装：

```bash
npm install --global --install-links .
```

```bash
nerv
```

存在托管发布版时，npm 入口转交当前主程序。源码回退方式仍会构建网页并创建按源码指纹区分的环境，不作为学生推荐安装流程。

## 系统限制

Linux 是首要验收平台。Windows 原生、WSL2、Apple Silicon macOS 和 Intel macOS 的依赖与硬件需要分别验证，当前均为实验性。WSL2 的 USB 透传和相机需要主机配置。Linux 枚举持久相机路径，Windows 和 macOS 分别通过 DirectShow、AVFoundation 枚举设备，此过程不打开相机。系统提供唯一身份时，NERV 会保存并在连接前重新匹配；没有唯一身份的设备需要通过画面确认。枚举失败时仍可手动输入编号。检测到串口不代表标定或机械结构已经正确。

安装器采用 uv 的[非托管安装选项](https://docs.astral.sh/uv/reference/installer/)，避免由 uv 修改全局 Shell 配置，再由 NERV 注册自身入口。临时验证不希望修改 Shell 配置时，Shell 安装器可加 --no-path，随后使用打印出的绝对命令路径，并保持同一个 NERV_HOME。

从 0.7.1 起，npm 启动器支持 `nerv update`，迁移后使用当前发布版。更早的 npm 启动器不会因远端发布而自动增加命令，需要先运行一次 GitHub 安装器并重新打开终端；之后统一使用 `nerv update`。
