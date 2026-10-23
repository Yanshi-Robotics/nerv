# NERV

[English](../../README.md) · [快速安装与启动](#快速安装与启动) · [SO-101 指南](standard-so101.md) · [架构](architecture.md) · [Runtime SDK](../../packages/nerv-runtime-sdk/README.md)

NERV 将视觉语言模型、机器人观测与本地技能执行连接起来。你可以运行已经训练好的策略，通过有限动作工具控制机器人，或让视觉模型监督任务，同时继续与 Agent 对话。

0.6.3 是开发候选版，标准 SO-101 与 LeRobot 是首个真机产品，实机验收尚未完成。NERV 接入外部训练好的策略和已有标定，不承担训练。

## 快速安装与启动

先安装 [Node.js 22 及以上版本](https://nodejs.org/en/download)（包含 npm）和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。启动器负责管理 Python 3.12，以及相互独立的主程序和设备环境。课程训练环境保持独立。

下载或克隆包含启动器的源码后，在仓库根目录安装一次。以下命令可用于 POSIX shell 或 Windows PowerShell；硬件支持范围见后文。

```bash
npm install --global --install-links .
```

安装过程会构建并包含网页。`--install-links` 会安装程序副本，避免命令依赖原源码目录。之后可以在任意目录启动：

```bash
nerv
```

服务就绪后，NERV 自动在默认浏览器中打开网页，默认地址为 [http://127.0.0.1:8000](http://127.0.0.1:8000)。首次启动安装主程序的 Python 依赖，后续复用该环境。同一个进程提供网页和 API，无需另起前端服务。按 Ctrl+C 结束服务。

通过 SSH 或在无桌面环境中使用时，执行 `nerv serve --no-browser` 关闭自动打开功能。也可以手动打开终端显示的地址。

```bash
nerv doctor
```

诊断命令显示版本、数据目录和配置，不连接硬件。用户数据统一默认保存在 `~/.nerv`，日常使用无需设置 NERV_HOME。如需指定其他端口：

```bash
nerv serve --port 8100
```

通过已发布的 GitHub 源码归档直接安装，或使用 npx 启动，详见 [GitHub 安装指南](github-install.md)。这两种方式均不需要在 npm 注册表发布包，但必须使用已包含启动器的远端提交；尚未推送的本地候选版无法从 GitHub 下载。指南也包含本地安装包、更新与 wheel 安装方式。

## 接入标准 SO-101

首版产品固定为单台 SO-101 follower、LeRobot 0.6.1，以及名为 top、wrist 的两个相机，预设为 640 × 480、30 FPS。五个臂关节使用角度，夹爪使用 LeRobot 的 0–100 位置表示。推理不需要 leader。学生填写连接信息并导入已有产物，无需配置关节映射。

设备环境单独安装一次：

```bash
nerv runtime install lerobot0.6.1
```

```bash
nerv runtime list
```

安装器会配置 LeRobot 与 Feetech，不连接机械臂。张量计算依赖的下载量大于主程序。随后按 [SO-101 配置与策略指南](standard-so101.md)完成接入：

1. 打开“设备与策略接入”。只有一个已安装 Runtime 时，界面自动填写适配器和 Python 路径。读取配置选项，再填写串口、机器人 ID、已有标定目录、相机配置和推理设备。
2. 使用 ACT 时，导入包含原始权重、原生处理器和统计量的本地导出目录。NERV 检查固定设备契约并记录文件指纹。模型缺少关键语义时，需要确认标准课程约定；张量形状不能证明单位和关节顺序。
3. 连接前完成 inspect 和 check。check 只加载模型，不连接硬件；连接后的“无动作策略检查”才会在未使能状态下使用真实观测试推理。
4. 创建真机会话，检查接入权限，由操作者准备设备。准备过程可能启用电机扭矩。核对两个相机画面后，再单独允许运动。

候选版真机连接仍标记为未验收，需要按指南显式启用监督验收设置。默认拒绝启动该连接是预期行为。安装 Runtime 和打开网页都不会自动连接或使能机器人。

| 控制方式 | 实际执行内容 | 用户提供的内容 |
|---|---|---|
| 人工运行 ACT | worker 中执行 ACT 原生预处理、推理与后处理 | 自己训练的标准 ACT 产物；选择“仅操作员”，无需 VLM 密钥 |
| VLM 直接控制 | 视觉模型选择有限的关节与夹爪工具，本地完成插值和反馈 | 已配置的视觉模型；无需 ACT 检查点 |
| VLM 监督 ACT | System 2 观察并监督 worker 内的策略执行 | 已配置的视觉模型，以及训练好的 ACT 产物 |

三种方式共用设备连接和动作调度器，切换控制器前须停止当前执行。动作结束不代表抓取成功。请求目标、LeRobot 返回的实际下发目标和实测位置分别记录。

## 架构

每个实例绑定一台机器人和一种环境，由一个活动会话持有控制权。NERV Agent 负责对话，并将目标交给 System 2；System 2 独立管理模型上下文、任务状态和取消信号。普通聊天不会取消任务。

<div align="center"><img src="../../assets/readme/images/layers.svg" alt="Agent 对话与 System 2 任务监督使用独立上下文，本地执行和设备管理与仿真世界控制分开" width="960"></div>

主进程负责会话、控制授权、Runtime 生命周期与事件日志。独立 worker 持有机器人、相机和本地执行器。System 1 表示这一执行层，不是第三个 Python 环境。

标准 SO-101 适配器只创建一个 LeRobot 设备实例。ACT 通过原生处理链生成候选位置，VLM 直接工具生成有限的位置目标；两者都经过时效、授权与动作检查，最终调用同一个 `robot.send_action()`。主进程不导入 LeRobot，也不加载策略张量。

[架构与控制指南](architecture.md)进一步说明 Agent 与 System 2 的分工、观测流、模型续接、会话记忆、仿真控制和 Runtime SDK。核心负责接口协调，机器人与模型的具体实现放在适配器和策略后端中。SmolVLA、GR00T 属于未来接入方向，当前尚未交付。

## 操作与模型

右侧栏可切换 Agent 对话和操作工具箱。“停止回复”取消当前对话响应，“停止任务”取消本地执行，“急停”撤销运动权限并尝试保持实测姿态。解除急停后回到未使能状态，不恢复旧任务。

连接、加载策略、允许运动和断开是不同操作。普通断开保留扭矩，显式卸力需要操作者支撑机械臂。软件保持不能代替断电；连接丢失时，系统无法确认保持命令已经执行。

使用视觉控制时，打开“模型与密钥”。NERV 通过注册表提供 OpenAI、DeepSeek、Gemini、xAI 和 Anthropic 的视觉模型。提交新密钥或替换密钥时，保存前会发起一次付费识图检查；打开设置和创建会话不会请求模型。对话模型和任务模型分别在对应控件中选择。已有登记密钥无需通过可选诊断才能使用。

“仅操作员”会话可以在没有 API 凭据的情况下人工运行 ACT；VLM 会话需要登记密钥。机器人相机画面进入模型观测，仿真真值和操作员专用全景不会进入。[完整模型目录与控制说明](architecture.md#视觉模型)另列于指南。

## 数据与安装目录

安装版和源码版均使用 `~/.nerv` 保存会话、日志、策略清单、设备配置和模型设置。启动器把主程序与设备环境放在 envs 下，把安装记录放在 runtimes 下。隔离测试可以显式设置 NERV_HOME。

环境配置写入 `~/.nerv/.env`，格式见 [.env.example](../../.env.example)。API 密钥来自私有凭据存储，不读取项目或 shell 的 API 密钥变量。POSIX 系统使用私有凭据文件，Windows 使用系统凭据管理器。训练权重和标定文件保留原位置；npm 管理启动器代码和自身下载缓存。

更新按源码指纹选择环境，保留已有数据和旧环境，不关闭正在运行的服务。旧版保存在仓库内的会话不会自动合并到用户目录，具体规则见[安装与更新](github-install.md#数据目录与更新)。

## 支持范围与验证

| 连接 | 当前范围 |
|---|---|
| SO-101 真机 | 标准 LeRobot 0.6.1 产品、原生 ACT、有限直接控制与共享观测；Linux 实机验收待完成 |
| G1 仿真 | 保留 MuJoCo 世界与步态控制器，以及 CPU 渲染的控制回归 |
| SO-101 仿真 | 三套桌面场景、刚性与柔性物品配置、关节与夹爪工具；未部署 ACT 或汉诺塔任务策略 |
| G1 真机 | 旧连接配置保持禁用，不提供当前执行支持 |
| 视觉模型 | 五家供应商的预置模型；密钥检查与机器人任务验收分别进行 |

Linux 是主要硬件验收目标。Windows 原生、WSL2、Apple Silicon macOS 和 Intel macOS 都需要分别验证依赖与硬件，目前仍为实验性支持。软件 CI 不能证明 USB 串口、相机或加速设备兼容。

仿真资产属于可选内容，学生启动器安装不包含这些资产。源码开发者可以初始化世界与策略子模块，再安装主程序的仿真依赖，步骤见[开发说明](../../CONTRIBUTING.md)和 [SO-101 工作台指南](../../worlds/scenes/so101_workbench/README.md)。切换真实与仿真环境需要结束当前实例。

原始 100,000 步 snack_into_box ACT 检查点已有 CPU／CUDA 张量前向证据，尚未完成实机验收，权重不随项目分发。软件替身测试不能证明抓取成功。[发布验收指南](../../guides/release-acceptance.md)分别规定 ACT 监督、VLM 直接控制的试验和故障检查。1.0.0 仍以操作者实机验收为发布条件，ultimate 表示未来自主运行目标。

## 演示与界面

下方 G1 录像来自早期仿真版本，展示头部相机观测与步态技能，不作为 SO-101 真机证据。指令为“Walk to the living room.”。完整回合未剪辑，以 2.5 倍速播放，另有[原速 MP4](../../assets/readme/media/walk-to-the-living-room.mp4)。

<div align="center"><img src="../../assets/readme/images/walk-to-the-living-room.gif" alt="早期 G1 仿真：根据相机观测调用步态技能走向客厅" width="960"></div>

<details>
<summary>早期界面与世界视图</summary>

0.6 版已经调整模型、任务和设备接入控件。保留的截图展示早期导航、观测与操作员视图；追拍相机和 World Explore 仅供操作员使用。

<div align="center"><img src="../../assets/readme/images/web-session.png" alt="早期会话界面" width="960"></div>
<div align="center"><img src="../../assets/readme/images/web-teleop.png" alt="早期操作员控件" width="960"></div>
<div align="center"><img src="../../assets/readme/images/web-dashboard.png" alt="早期注册表与会话控制面板" width="960"></div>
<div align="center"><img src="../../assets/readme/images/world-explore/apt-scene-test.png" alt="公寓中的操作员场景测试" width="960"></div>
<div align="center"><img src="../../assets/readme/images/world-explore/apt-cutaway.png" alt="操作员查看的公寓布局" width="960"></div>
<div align="center"><img src="../../assets/readme/images/world-explore/house-west_pool-night.png" alt="仿真住宅泳池露台夜景" width="960"></div>

[世界库工具](../../worlds/docs/explore/README.md)介绍场景测试与浏览器视图。G1 的平地步态不提供抓取、家具操作、自主坐下或上下楼梯能力；场景检查不能证明自主任务成功。

</details>

## 扩展与贡献

独立安装的 [Runtime SDK](../../packages/nerv-runtime-sdk/README.md)定义插件发现，以及协议 2 的生命周期、动作与观测接口。[只读示例](../../examples/inspection-runtime/)展示插件边界。设备 SDK 留在 worker 中，策略后端只生成标准目标，不打开硬件。

源码环境和测试见 [CONTRIBUTING.md](../../CONTRIBUTING.md)，模块边界见 [AGENTS.md](../../AGENTS.md)，版本历史见 [CHANGELOG.md](../../CHANGELOG.md)。报告问题时提供版本和脱敏后的复现步骤，不附带密钥、未经检查的相机画面或私有配置归档。

NERV 使用 [MIT 许可证](../../LICENSE)。厂商 SDK、模型权重、训练数据和外部资产保留各自的许可证。
