# NERV

[English](../../README.md) · [架构](#架构) · [Runtime SDK](#运行插件) · [视觉模型配置](#视觉模型)

NERV 将视觉语言模型与机器人、传感器和技能连接起来。Agent 与用户交流并提交机器人任务，System 2 规划和监督执行，本地运行后端负责设备连接和动作下发。

## 概览

一个 NERV 实例绑定一台机器人和一个运行环境，仅一条当前对话拥有控制权。仿真与现实是启动前的并列选择，各自记录准备、校准和验收状态。更换运行环境需要结束当前实例；本版尚未实现运行中切换。

NERV Agent 持续与用户对话，独立的 System 2 负责观察、规划和监督机器人。普通消息不打断任务；明确的目标更新和取消通过任务接口执行。操作员负责准备设备、启用控制和急停。解除急停后，设备回到未使能的待命状态，之前的任务作废。

## 架构

NERV Agent 将机器人目标交给 System 2；System 1 执行受限动作和策略，Robot Runtime 独占设备与传感器。World Control 管理仿真世界干预。宿主协调控制权、实例生命周期与事件记录。机器人驱动与策略推理运行在独立的 worker 环境中。

<div align="center"><img src="../../assets/readme/images/layers.svg" alt="NERV Agent 对话、System 2 任务监督、System 1 执行与仿真世界控制的分层关系" width="960"></div>

原有节点拓扑继续作为传输基础：

<div align="center"><img src="../../assets/readme/images/structure.svg" alt="NERV 连接 Agent harness、独立机器人运行后端、世界与工具服务，并区分观测和操作员控制路径" width="860"></div>

Agent 与 System 2 分别使用独立的 harness、上下文、摘要、供应商续接状态和用量记录。两者共用追加式事件日志，通过任务视图区分各自记录。运行后端声明传感器与技能，界面读取这些能力。仿真与现实各有独立的环境绑定。操作员的场景控制和仿真真值不进入 Agent 观测。模块边界由[接口类型](../../src/nerv/nerve/)定义。

## 当前状态

当前源码版本为 **0.6.2**。本地 `v0.5.0` 标签保留审计基线。1.0.0 发布前须由操作员完成冻结条件下的实机试验和故障检查。

| 接入对象 | 当前范围 |
|---|---|
| SO-101／现实 | LeRobot 0.6.1 适配器、俯视与腕部相机、原生 ACT 加载、持续执行和本地监督。尚待实机验收。 |
| G1／仿真 | 保留现有 MuJoCo 世界与步态控制器，CPU llvmpipe 渲染与控制回归已通过。 |
| SO-101／仿真 | 三套 MuJoCo 桌面预设，可替换刚性与柔性物体，提供俯视／腕部相机和有限幅度的关节／夹爪控制。尚无任务策略或仿真 ACT 部署。 |
| G1／现实 | 保留旧配置，当前绑定已禁用。 |
| 视觉模型 | 预置 OpenAI、DeepSeek、Gemini、xAI 和 Anthropic 的视觉模型，按供应商管理密钥，按型号选择推理强度。接口检测与实机验收分别记录。 |

ACT 技能使用原实验 100,000 步的 `snack_into_box` 检查点。仓库只保存文件哈希与输入契约，不包含模型权重。策略是否自行返回 Rest，仍须实机检验。

## 快速开始

需要 Python 3.11 及以上版本，以及 Node.js 和 npm。在本仓库执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ./packages/nerv-runtime-sdk -e '.[platform,dev]'
npm --prefix frontend ci
npm --prefix frontend run build:static
.venv/bin/nerv serve --port 8000
```

浏览器打开 `http://localhost:8000`。静态前端与后端使用同一来源。前端开发命令为 `npm --prefix frontend run dev`，使用已分配的 8100 端口。

打开“模型与密钥”，选择供应商，输入密钥后点击“保存密钥”。NERV 先检查本地存储是否可写，再使用该供应商的一个已启用模型发送一次付费图像请求。只有回答与生成的测试图相符，才会保存密钥；界面仅显示保存结果，测试失败保留原密钥。模型和推理强度在会话中选择。创建会话要求已登记密钥，但不会请求供应商。也可建立不连接机器人的对话，现实会话不会自动准备设备。

环境配置可参考 [.env.example](../../.env.example)。模型设置和密钥保存在仓库外的 `NERV_HOME` 中，默认位置为 `~/.nerv`。密钥存放在 `credentials.json`，模型选择、强度和检测记录存放在 `vlm.json`。目录权限为 0700，文件权限为 0600。视觉模型注册表不读取 shell 或项目 `.env` 中的 API 密钥。请在面板中重新填写已有密钥；升级不会修改旧文件。

## 机器人接入

G1 仿真继续使用现有世界与策略子模块，并需要仿真依赖：

```bash
git submodule update --init --recursive
.venv/bin/python -m pip install -e '.[sim]'
```

在界面选择 G1、仿真和兼容世界。原有世界生命周期、场景工具和操作员视图继续保留。场景真值与第三人称相机不进入 Agent 的观测。

SO-101 仿真使用同一组仿真依赖，在界面选择 `so101-pick-place`、`so101-sort-snacks` 或 `so101-hanoi-five`。[桌面场景说明](../../worlds/scenes/so101_workbench/README.md)提供物品替换、弹性柔性体与布片、源配置和验证方式。重新生成三套标准场景：

```bash
.venv/bin/python worlds/tools/make_workbench.py --all
```

复位恢复指定的机械臂初始姿态和全部物体状态。场景验证覆盖物理、相机、电机指令和末端位置可达性；自主抓取与无碰撞路径仍需对应控制器和实验验证。

SO-101 现实环境使用独立的适配器环境，其中只安装 SDK 与 LeRobot 适配器：

```bash
python3 -m venv .venv-lerobot
.venv-lerobot/bin/python -m pip install -e ./packages/nerv-runtime-sdk -e ./packages/nerv-runtime-lerobot
.venv-lerobot/bin/nerv-runtime discover
.venv-lerobot/bin/nerv-runtime check --provider lerobot0.6.1
```

将 `NERV_LEROBOT_PYTHON` 指向该解释器，配置 `SO101_PORT`、校准时使用的 `SO101_ID`、原生相机 JSON 路径 `SO101_CAMERA_CONFIG`，以及冻结检查点目录 `SO101_ACT_CHECKPOINT`。相机、校准、场景和初始姿态须与训练条件匹配。现实配置继续标为 `verified: false`；`NERV_ALLOW_UNVERIFIED=1` 仅允许进入受监督的验收会话。

由操作员选择现实环境、创建会话，点击“准备设备”，审阅 worker 的工具清单，加载策略后启用控制。工具清单审阅属于初始设置，Agent 的每次工具调用不需要人工批准。准备设备会建立连接，并可能启用电机力矩；加载模型不会发送动作。技能结束后设备保持连接。软件急停保持姿态，不等同于断电，也不能代替硬件急停。

## 对话与世界控制

右侧聊天区属于 NERV Agent，机器人执行和手动控制期间仍可交流。**停止回复**仅取消当前对话决定；**停止任务**等待本地执行确认；**急停**锁定动作，操作员解除后回到未授权待命。

中间相机外框内显示 System 2 的公开决策摘要、执行反馈和宿主事件。动态气泡带有来源和时间，不进入相机原始帧或策略输入。独立事件流支持重连与去重，历史会话不显示实时画面。

业务能力分为五类：`agent_tools` 提供计算和会话历史；`robot_actions` 提供机器人动作和技能；`robot_tasks` 管理任务；`world_control` 修改仿真配置与状态；`instance_control` 管理实例生命周期。供应商的函数调用和 MCP 只负责传输，不合并这些能力的权限。

在**新建会话**中选择 SO-101、仿真和一个桌面场景，再配置该场景支持的物品槽位。物品类型、标称尺寸和质量来自世界目录，柔性材料参数尚未经过实物标定。每个实例独立生成 MJCF、配置与来源哈希。

**工具箱 → 仿真世界**与 Agent 的世界命令使用同一服务。重置保留所选物品和世界进程，结束任务并撤销动作授权。改变物品拓扑时使用**结束并重建实例**；旧会话保留历史，不能控制新实例。天气、家具、风格等能力只有在世界声明并实现后才会出现；当前桌面场景提供物品替换与重置。现实环境拒绝修改世界属性。

创建会话要求已登记模型密钥。创建、查看和手动操作仿真不会发起模型请求。语言控制需要配置 VLM，并产生调用费用。当前桌面仿真没有部署 ACT 或哈诺塔执行策略。长期自主目标命名为 **ultimate**；本版仍是由 Agent 协调操作的系统。

## 演示与界面

以下录像和截图来自早期 G1 仿真版本，展示原有的导航与操作员视图。0.6 版已调整模型设置和设备准备流程。

<div align="center"><img src="../../assets/readme/images/walk-to-the-living-room.gif" alt="早期 G1 仿真：语言模型观察头部相机，并调用步态技能走向客厅" width="960"></div>

收到“Walk to the living room.”后，Agent 根据头部相机画面调用步态技能。录像展示一轮完整指令，未剪辑，以 2.5 倍速播放，另有[原速 MP4](../../assets/readme/media/walk-to-the-living-room.mp4)。追拍相机仅供操作员查看。

<div align="center"><img src="../../assets/readme/images/web-session.png" alt="早期仿真会话界面：机器人观测、操作员追拍画面与对话" width="960"></div>

会话界面并列显示传感器画面与对话，并区分机器人观测和操作员专用视图。运行后端提供的传感器名称决定显示哪些相机。

NERV Agent 每轮决策都会接收机器人相机的新鲜快照，普通聊天也可以看图。界面中显示或隐藏画面不会改变模型输入。仿真相机可以在准备动作执行器之前读取；真实相机仍需操作员准备设备。SO-101 桌面场景的俯视和腕部画面并排显示，全景放在下一行并标注“上帝视角”。全景不进入 Agent 输入，任务监督仍由 System 2 负责。

<details>
<summary>更多界面与仿真场景</summary>

右侧栏的**工具箱**集中提供设备接入、准备、运动授权、机器人动作、世界控制、相机与 System 2 设置，以及场景测试。工具箱关闭后，顶部操作栏仍显示**停止任务**和**急停**。右侧栏在 **Agent 对话**与**工具箱**之间切换，切换时保留聊天草稿和正在返回的回复，不停止机器人任务。中央保留相机与真实机器人动态。控制面板显示注册项、节点状态与会话。

**允许接入**针对操作员已查看的能力与说明。能力变化后须重新确认；节点离线时无法允许接入。接入许可不会自动准备设备、授权运动或启动任务。

<div align="center"><img src="../../assets/readme/images/web-teleop.png" alt="早期遥控界面：急停与按工具生成的控制卡片" width="960"></div>

<div align="center"><img src="../../assets/readme/images/web-dashboard.png" alt="早期控制面板：注册表、节点、信任状态与会话" width="960"></div>

World Explore 展示公寓与住宅资产。场景测试由操作员进行，其任务状态和结果不进入 Agent 观测，也不证明机器人具有相应自主技能。G1 已发布的平地步态不提供家具操作、抓取、自主坐下或上下楼梯技能。

<div align="center"><img src="../../assets/readme/images/world-explore/apt-scene-test.png" alt="操作员场景测试：罐子放在冰箱层板上，冰箱门已关闭" width="960"></div>

<div align="center"><img src="../../assets/readme/images/world-explore/apt-cutaway.png" alt="World Explore 公寓剖面：房间与设施标记" width="960"></div>

<div align="center"><img src="../../assets/readme/images/world-explore/house-west_pool-night.png" alt="MuJoCo 住宅夜景中的泳池露台与西侧步道" width="960"></div>

场景测试、G1 路线和浏览器视图的说明见[世界库工具](../../worlds/docs/explore/README.md)。

</details>

## 视觉模型

面板按供应商管理密钥，只显示是否已保存，不向浏览器返回保存的值。面板列出支持的模型，不提供模型或推理强度选择。同一供应商的已注册模型共用密钥，实际使用权限取决于供应商账户。聊天区读取同一注册表。纯文字模型和 Mock 不进入正式运行目录，也不作为自动回退。

OpenAI、DeepSeek 和 xAI 分别使用对应的 Responses 协议参数。Gemini 使用无服务端会话存储的 Interactions API，Claude 使用原生 Messages 流式接口。带签名的思考内容和推理续接项只会传回匹配的供应商接口。界面按型号展示合法推理强度；未指定时省略参数，使用供应商默认值。系统分别记住上次模型和每个型号的强度。当前回合或机器人执行尚未结束时，修改会被拒绝。

| 供应商 | 预置视觉模型 | 原生推理强度 |
|---|---|---|
| OpenAI | GPT-6.1 Sol、GPT-6 Astra | low、medium、high、xhigh、max |
| DeepSeek | V4.1 Flash | none、low、high、max |
| Gemini | 3.8 Flash、3.5 Flash-Lite | Flash：low、medium、high；Lite 另支持 minimal |
| xAI | Grok 4.7、Grok 4.3 | 4.7：low、medium、high、xhigh；4.3：none、low、medium、high |
| Anthropic | Claude Opus 5.5、Claude Sonnet 5.5 | low、medium、high、xhigh、max |

模型能力依据于 2026-10-20 核对的供应商文档：[OpenAI](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[DeepSeek](https://api-docs.deepseek.com/api/create-response/)、[Gemini](https://ai.google.dev/gemini-api/docs/thinking)、[xAI](https://docs.x.ai/developers/model-capabilities/text/reasoning)、[Anthropic](https://platform.claude.com/docs/en/build-with-claude/effort)。只有操作员提交密钥登记表单时才发送一次识图请求。打开设置、切换选项和创建会话均不会请求供应商。

可选诊断 API 检查图像输入、工具调用和多轮续接。诊断结果不限制创建会话或使用模型；这两项只要求已登记非空密钥。已有密钥无需重新登记。密钥后来失效时仍可创建会话，调用时显示明确错误。失败回执保留用户输入，并提供重试和模型设置入口；重试不会重放旧机器人动作。

地址、型号、强度或凭据变化后，原有诊断和实机记录不再适用。一次密钥登记通过，不代表其他型号、通用视觉能力或机器人能力已通过验收。Agent 回复失败不会取消独立任务；System 2 故障仍按本地停止与监督规则处理。

## Agent harness

<div align="center"><img src="../../assets/readme/images/flow.svg" alt="持续技能执行流程：观测、保存并检查工具调用、取得执行编号、监督本地策略并报告观测到的结果" width="860"></div>

图中展示 ACT 这类持续运行的技能。启动后先返回执行编号，System 2 可以在 worker 运行本地策略时继续观察、查询执行状态。直接读取类工具和已有 G1 技能也可在工具调用中返回结果。等待模型响应时，本地停止和监督机制继续生效。

默认的 `AgentHarness` 通过可替换接口获得宿主 Link、模型供应商、上下文管理器和摘要器，不持有机器人对象，也不加载 ACT。

每个会话独立保存追加式事件日志、图片资产、任务和笔记。旧 JSON 会话执行一次性迁移，并保留 `.legacy` 源快照。JSON 快照可从日志重建；历史检索和分页阅读仅能访问当前会话。

上下文预算覆盖指令、笔记、摘要、完整工具调用组、工具定义、当前图片、供应商续接项和输出预留。Token 与图片预算属于估算；供应商返回实际用量时另行记录。摘要使用不带工具、图片及主对话推理续接的独立请求。摘要失败时保留原文；无法满足请求预算则暂停。

进程重启后，未结束的回合被记为中断、结果未知。系统恢复历史，不重放动作。新聊天消息仅使 Agent 的旧回复失效。明确更新任务时递增任务版本，停止旧执行后重新规划。任务启动时冻结 System 2 的模型配置和推理强度。

## 运行插件

`nerv-runtime-sdk` 可独立安装，不依赖 NERV 核心、厂商 SDK、PyTorch 或 MuJoCo。Python worker 在自己的环境内发现 `RuntimeProvider` 入口。非 Python 服务可实现相同 HTTP／MCP 协议，并登记服务 URL。

模块导入和 `describe()` 不得操作设备或 GPU。准备、加载策略、使能、执行、取消、急停、解除和断连分别处理。机器人描述、环境绑定和策略资产各有独立契约。宿主不会向 worker 注入整仓源码或模型密钥。

[Runtime SDK 指南](../../packages/nerv-runtime-sdk/README.md)与[只读插件示例](../../examples/inspection-runtime/)说明协议 2 的接入方式。插件须提供 `actions()`；不兼容的 worker 在准备前被拒绝。SDK 提供插件发现、协议接口和只读一致性检查。

## 验证

原始 ACT 检查点与原生处理器已通过 CPU 和排队执行的 CUDA 张量前向检查。这些检查只验证数值契约，不代表抓取表现。

软件测试明确使用模型、相机和执行器替身，不构成实机演示。CPU 生命周期测试需要适配器源码，可在不向主环境安装原生驱动的情况下安装：

```bash
.venv/bin/python -m pip install --no-deps -e ./packages/nerv-runtime-lerobot
.venv/bin/python -m ruff check src packages tests
.venv/bin/python -m pytest tests --ignore=tests/test_humanoid_family.py --ignore=tests/test_world_bus.py --ignore=tests/test_e2e_apt.py -q
npm --prefix frontend run typecheck
npm --prefix frontend run check:locales
npm --prefix frontend run build:static
```

完整仿真验证还须在可用的 MuJoCo 渲染环境中运行上述三份被排除的测试。本机 GPU 工作遵守队列规则；实机测试与付费接口检测由操作员明确触发。

## 版本管理

版本历史继续保留。核心包、CLI 和 HTTP 版本统一来自 `src/nerv/_version.py`。独立安装的 SDK 与适配器在各自包元数据中声明匹配的开发版本。

发布顺序为 0.5.0 审计基线、0.6.x 开发，再于冻结配置的 10 次接入验收及故障测试通过后发布 1.0.0。单元测试通过或模型进入注册表不会自动触发发布。版本记录见 [CHANGELOG.md](../../CHANGELOG.md)。

## 开发

[AGENTS.md](../../AGENTS.md) 说明仓库分层。服务契约由[接口类型](../../src/nerv/nerve/)和 [Runtime SDK](../../packages/nerv-runtime-sdk/src/nerv_runtime_sdk/) 定义。场景和策略继续作为独立子模块维护。

NERV 使用 [MIT 许可证](../../LICENSE)。厂商 SDK、模型权重、训练数据及外部资产遵守各自的许可证。
