# NERV

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)](../../pyproject.toml) [![Status](https://img.shields.io/badge/0.9.1-in_development-c59b42)](../../CHANGELOG.md) [![License](https://img.shields.io/badge/License-MIT-blue)](../../LICENSE)

[English](../../README.md) · [简体中文](README.md)

**NERV 是供外部 Agent 使用的机器人平台，连接本地执行、真实设备与虚拟世界。** 你可以继续使用熟悉的 Agent，由 NERV 提供观测、有限动作、策略执行、人工控制和运行记录。

**Brain Core 随 NERV 安装，不包含 AI Agent、模型 Provider 或对话循环。** 外部 Agent 管理解、规划、模型调用和上下文；Brain Core 管身份、观测、授权、任务与回执；Runtime 管驱动、ACT／步态推理、控制循环与本地停止。World、Robot 和规范 Brain 持续存在，与 Run 和 Agent 连接分别管理生命周期。

**当前成果：同一本地候选 25 通过三轮全新双 Agent G1 客厅会合；每轮两台 G1 均在同一个共享 apt World 中运行。** 冻结的 Linux CPU 配置上，6 次机器人任务均通过固定评分与独立只读复核，正式导航中无虚报成功、跌倒或机器人间碰撞。该候选还通过 975 项 CPU 检查、两项退役 Provider 跳过，以及隔离离线安装／升级／回滚。这是限定组合的仿真资格，不代表普遍可靠性或真机验收。

**0.9.1 尚未公开发布，整体完成仍有两项 UI 检查未闭合：** End run 原生确认执行与共享 World 停止按钮。A 的 Stop task 和紧急停止目标已另行实际验证；B 保留控制身份，在单任务停止之后还完成了一次实测动作。UI 补验资源已通过操作者接口清理，这不等于未验证的按钮已通过。沿用 0.9.1 版本号，但这是架构与兼容性升级，详见[当前验收与限制](brain-core-091.md#当前本地验收)。

<div align="center"><img src="../../assets/readme/images/dual-agent-091.jpg" alt="候选25正式CPU仿真验收的原始头部相机选帧，两台G1在客厅中分别看到对方" width="960"></div>

正式 CPU 仿真验收的原始相机选帧，经排版用于展示。两路视图分别采集，不是同步双目；全身到达和稳定停止由独立物理证据核验，单张画面不等于评分。

[接入 Agent](mcp.md) · [架构](architecture.md) · [SO-101 设置](so101-setup.md) · [资产](../../guides/assets.md) · [路线图](ROADMAP.md) · [开发历史](brain-core-091.md#开发历史)

下方命令安装最新**公开版本**，不会取得这些尚未发布的候选修改。SO-101 真机验收仍未完成；Go2 缺少完整、可核验的部署契约，暂不能开放运动。

<div align="center"><img src="../../assets/readme/diagrams/zh/platform-091.svg" alt="外部Agent通过NERV Brain Core管理身份和授权，由Runtime控制设备与共享仿真" width="960"></div>

## 安装与启动

GitHub 安装器管理 Python、uv 和预构建界面。普通用户无需安装 Node.js、克隆源码或另开 LeRobot 终端；机器人与仿真组件按需安装。

Linux／macOS：

```bash
curl -fsSL https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.sh | sh
```

Windows PowerShell（实验性支持）：

```powershell
irm https://github.com/Yanshi-Robotics/nerv/releases/latest/download/install.ps1 | iex
```

打开新终端，运行 `nerv`。正式渠道默认打开 [http://127.0.0.1:8170](http://127.0.0.1:8170)。启动不会连接电机、授权运动或恢复中断的任务。没有桌面的终端可使用 `nerv serve --no-browser`；Ctrl+C 请求正常退出。

显式安装的预览版通过 `nerv preview` 启动，使用独立渠道数据。候选安装与源码开发见[贡献说明](../../CONTRIBUTING.md)。预览版不是另一份源码仓库，推送代码也不会自动切换本地安装。

## 创建项目并连接 Agent

1. 打开控制台，安装仿真引擎和 World，或从“机器人”注册标准 SO-101。
2. 新建 Sim 或 Real Project，选择机器人、世界和执行结构。创建项目无需 NERV 聊天密钥；项目保存在 `.nerv` 当前渠道下。
3. 打开“设置 → Agent 连接”，填写客户端名称、选择允许访问的项目，并指定仅观测或允许任务／动作请求。
4. 在 MCP 客户端中设置桥接命令，填入配对后的客户端 ID。预览候选版使用：

   ```bash
   nerv mcp --channel preview --client CLIENT_ID
   ```

5. 仿真先创建并明确启动空 World，再向 Agent 授予兼容模板与实例配额。未绑定桥接器发现授权并申请机器人，之后附着 Brain、启动 Run。检查选定的控制方案，再单独授权动作，或使用操作者明确授予的仿真控制预授权。

桥接器连接已经运行的 Host，不另起一套机器人服务，也不自动准备硬件。[MCP 指南](mcp.md) 介绍配置、观测确认、请求 ID 和撤销方式。Codex、DeepSeek Harness、Claude Code 是计划接入的客户端；列出名称不代表所有客户端及版本均已通过验收。

没有 Agent 或模型密钥，操作者仍可安装资源、配置设备、查看观测，以及使用允许的固定策略和人工控制。NERV 不再运行内部视觉决策服务。

## 在世界中工作

控制台集中展示**项目、机器人、世界、策略、资产和设置**。机器人详情保留 SO-101 档案及本地三维预览。关联项目分别标注真实或仿真工作区；可用的机器人资产不会被当成已注册真机。

进入项目后，主相机或仿真场景占据主要展示区域。左侧窄工具条提供观测、执行、设备和人工控制。监视框可切换为主画面、调整大小、停靠或收起，布局随项目保存。界面的相机显示方式不改变交给 Agent 的原始图像。

活动气泡显示带来源的 Agent 说明、任务状态和执行反馈，不导入完整外部聊天，也不把 Agent 的文字当作已确认的设备结果。“活动”保留完整记录。控制台以覆盖层打开，进入设置不会结束当前 Run；停止任务和紧急停止仍是独立操作。

<div align="center"><img src="../../assets/readme/project-world-090-zh.jpg" alt="SO-101 仿真工作台，包含总览画面、顶部与腕部监视框、停止控件及带来源的 MCP 观测说明" width="1100"></div>

已安装候选版中的 SO-101 仿真 Run，未授权运动。仅有观测权限的 MCP SDK 客户端收到两幅相机图像后，发布了画面中的说明。截图展示工作台和事件传递，不包含模型推理、机器人动作或真实硬件测试。

## 项目、运行与任务历史

Project 保存机器人、环境、执行结构、布局和修订。Run 记录针对已有 Robot、Brain、World 或设备连接的一段执行；这些资源拥有独立生命周期。Task 是锁定配置与授权的目标任务，Execution 是任务中的一次策略或有限动作执行。

<div align="center"><img src="../../assets/readme/diagrams/zh/lifecycle-091.svg" alt="Project 包含当前与历史 Run，Run 中的 Task 可以包含多个 Execution" width="960"></div>

再次打开项目会恢复配置和历史，并可重新附着到身份有效的活动实例。Host 重启后旧任务中断，运动授权清除，不重放动作。新项目修订用于后续运行与任务，当前任务继续使用锁定的引用。官方权重与资产通过引用复用，不为每个项目复制一份。

已登记设备需先由操作者连接，再启动 Run。结束 Run 保留设备连接，断开设备是独立操作。没有活动 Run 时仍可读取资源观测，查看图像不授予运动控制权。设备生命周期目前仅通过无硬件动作的模拟适配器检查，真机验证尚未进行。

仿真 Run 只附着已经启动的 World 和就绪的机器人 Runtime，不能创建、重启或重建物理仿真。结束或更换 Run 保留实体及其规范 Brain；移除机器人、停止共享 World 分别属于独立资源操作。旧独立仿真 Run 需要先明确分配资源，才能恢复执行。

0.9 迁移将机器人会话保存为项目与历史运行，纯聊天保留为只读归档，不伪造机器人项目。迁移快照和可重复执行的记录用于恢复；使用旧版程序回滚数据前，先按候选版的恢复说明操作。

## 监督与执行

外部 Agent 可以请求有限视觉动作或兼容的运动技能。ACT 与运动策略继续作为无内部模型决策的执行路线。驱动和推理留在 Runtime 环境，模型输出不能直接写电机寄存器。

<div align="center"><img src="../../assets/readme/diagrams/zh/supervision-091.svg" alt="观测交付、明确确认、授权执行和监督过期后的本地停止" width="960"></div>

G1 观测区分点射线与全身制动通行空间，提供本机关节／IMU 有效性和采集时的相机标定。声明的向下相机与同帧自身保守碰撞包络，帮助 Agent 检查近身及地面视图；它们不识别房间、不认证接触或到达，也不提供 World 位置、地图或目标坐标，见 [MCP 传感器契约](mcp.md)。

观测交付、观测确认和任务成功是不同事实。图像附带实例与帧信息，客户端在请求运动前需要确认收到的观测，有限动作结束后重新观察。轮询任务状态或保持连接不会续期监督。监督过期时，本地机制请求停止，并报告停止是否已确认。

停止确认依据机器人当前测量。仿真采样间隔超过所需安静窗口时，确认会失效；仅有墙钟时效不能证明持续静止。有限动作结束后，机器人局部站立反馈继续运行。

人工接管先撤销外部运动控制，再等待停止确认。Agent 仍可观察；恢复 Agent 控制需要操作者再次确认。停止接收请求、停止任务、紧急停止、断开和卸力用途不同，详见[架构与控制](architecture.md)。

## 机器人与当前限制

| 路线 | 候选版范围 | 验收边界 |
|---|---|---|
| SO-101 Real | 分步引导、已有标定检查、双相机、ACT 与有限动作 | 真实连接和动作由操作者完成；未认证抓取可靠性 |
| SO-101 Sim | 三个工作台场景、相机观测与有限控制器 | 不假定真机 ACT 导出可以直接用于仿真 |
| G1 Sim | 受管理场景与运动策略路径 | 候选 25 经真实 MCP 完成三轮 apt 客厅会合，6 次机器人任务成功；位移精度仍有限，资格仅适用于冻结的 Linux CPU 配置 |
| Go2 Sim | 适配器与能力声明开发 | 完整部署契约缺失，运动被阻止；不宣称已具备公开运动能力 |
| Sim2Real／Real2Sim 等组合 | 预留能力类型 | 本版不开放运行 |
| Codex／DeepSeek Harness／Claude Code | 面向这些客户端提供本地 stdio MCP 桥接 | 具体客户端及版本的完整流程验收仍待完成 |

标准 SO-101 引导依次检查电脑、安装运行组件、识别 follower、核对已有电机与标定条件、确认相机并保存配置。电机配置和新标定继续在外部完成。缺少标定文件表示没有找到文件，不意味着设备从未标定。连接可能给电机上力，请按 [SO-101 指南](standard-so101.md) 操作。

Linux、Windows、WSL 与 macOS 使用各自的依赖方案。Windows／WSL 硬件透传和 macOS 相机支持仍属实验性；当前锁定的 macOS Intel Host 组合没有兼容安装包。[安装指南](github-install.md) 与[验收说明](../../guides/release-acceptance.md) 分别说明平台包、仿真、策略推理和真机证据。

## 资产、策略与托管环境

NERV 的 Python 环境、工具、缓存和已安装资源统一保存在 `NERV_HOME`，默认是 `~/.nerv`。Host、LeRobot、仿真和构建工具按依赖兼容性隔离，正式与预览渠道分别保存数据。安装和更新不会修改课程环境或系统 Python；USB 设备与 GPU 仍是共享硬件，需要明确占用关系。详见[环境管理](../../guides/environments.md)。

仿真引擎与场景包可以分别更新。资产库支持官方版本、纯数据 MJCF 导入、个人副本、检查、保存版本和导出；官方更新不会覆盖个人修改。公寓、住宅和机器人资产继续属于 NERV 的仿真方向，其源码由 [nerv-assets](https://github.com/Yanshi-Robotics/nerv-assets) 管理。操作见[资产流程](../../guides/assets.md)。

策略可来自本地导出或固定的 Hugging Face 修订。ACT 保留原生处理器与统计量，ONNX 运动策略需要明确的部署契约。某个模型在组织中公开，不代表它自动兼容或受推荐。来源不明的历史权重不纳入新的官方分发。详见[运动策略](../../guides/motion-policies.md)和[发布规则](../../guides/policy-publishing.md)。

下载使用文件校验、持久记录和有上限的重试／续传，环境经暂存安装和检查后才启用。[可恢复安装](recoverable-installation.md) 介绍进度与限制。使用 `nerv update --check` 检查公开更新；执行 `nerv update` 或 `nerv rollback` 前先停止任务并退出 NERV。预览渠道有独立的更新和回滚命令。

## 部署方向

0.9.1 面向同机外部 Agent 接入。未来可由独立本地 Agent 项目管理模型 API、上下文与规划，再连接 NERV Brain Core 和 Runtime；Brain Core 仍属于 NERV。未来云端 Agent 还需要带认证的远程传输和设备注册；本候选版不包含这两个产品。

<div align="center"><img src="../../assets/readme/diagrams/zh/deployments-091.svg" alt="实线为同机 Agent 与 NERV，虚线为未来本地 Agent 与远程传输部署" width="960"></div>

[路线图](ROADMAP.md) 保留 Harness 扩展、插件管理、Yanshifu 集成和真实／仿真迁移研究，列出下一步与完成判据，不承诺日期。

## 世界与早期演示

下列录像和截图来自早期版本，展示仿真资产与历史界面，不代表 0.9.0 客户端验收完成。G1 录像使用头部相机和步态技能执行“走到客厅”，以 2.5 倍速播放；可查看[原速 MP4](../../assets/readme/media/walk-to-the-living-room.mp4)。

<div align="center"><img src="../../assets/readme/images/walk-to-the-living-room.gif" alt="早期 G1 公寓仿真，使用头部相机观测与步态技能" width="960"></div>
<div align="center"><img src="../../assets/readme/images/world-explore/house-west_pool-night.png" alt="已有住宅仿真资产的夜间泳池露台" width="960"></div>

<details>
<summary>早期界面与资产视图</summary>
<div align="center"><img src="../../assets/readme/images/new-session-0.8.5.png" alt="归档的 0.8.5 新会话界面" width="960"></div>
<div align="center"><img src="../../assets/readme/images/components-0.8.5.png" alt="归档的 0.8.5 已安装组件列表" width="960"></div>
<div align="center"><img src="../../assets/readme/images/assets-0.8.5.png" alt="归档的 0.8.5 个人资产修订" width="960"></div>
<div align="center"><img src="../../assets/readme/images/control-scheme-0.7.png" alt="归档的 0.7 SO-101 工作区" width="960"></div>
<div align="center"><img src="../../assets/readme/images/web-session.png" alt="早期会话界面" width="960"></div>
<div align="center"><img src="../../assets/readme/images/web-teleop.png" alt="早期人工控制" width="960"></div>
<div align="center"><img src="../../assets/readme/images/web-dashboard.png" alt="早期注册表面板" width="960"></div>
<div align="center"><img src="../../assets/readme/images/world-explore/apt-scene-test.png" alt="公寓场景测试" width="960"></div>
<div align="center"><img src="../../assets/readme/images/world-explore/apt-cutaway.png" alt="公寓剖视图" width="960"></div>
</details>

[世界库工具](../../nerv-assets/docs/explore/README.md) 介绍场景查看方式。平地步态不包含抓取、自主坐下或上下楼能力。下图采集自本地 0.9.0 候选版，展示已保存但尚未启动 Run 的项目，不作为机器人运动证据。

![NERV 0.9.0 浅色 Project 控制台](../../assets/readme/project-console-090-zh.jpg)

## 参与开发

NERV 由 Yanshi Robotics 维护，**DeepSeek Harness** 与 **Codex** 参与 AI 辅助开发，维护者负责审核、合并和发布。

开发入口包括[贡献说明](../../CONTRIBUTING.md)、[Runtime SDK](../../packages/nerv-runtime-sdk/README.md) 和[仓库规则](../../AGENTS.md)。欢迎通过 [GitHub Issues](https://github.com/Yanshi-Robotics/nerv/issues) 提交可复现问题和使用场景；报告中不要包含凭据、个人信息和私有录像。

应用代码使用 [MIT 许可证](../../LICENSE)，机器人资产、第三方包和策略权重保留各自许可。上述架构图附有[可编辑源文件](../../assets/readme/diagrams/README.md)。
