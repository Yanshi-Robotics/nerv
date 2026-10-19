# NERV

[English](../../../README.md) · [架构](../../architecture.md) · [Runtime SDK](../../runtime-sdk.md) · [视觉模型配置](../../vlm.md)

NERV 将视觉语言模型与机器人、传感器和技能连接起来。Agent 决定任务步骤与工具调用，本地运行后端负责设备连接和动作下发。

## 概览

一个 NERV 实例绑定一台机器人和一个运行环境，仅一条当前对话拥有控制权。仿真与现实是启动前的并列选择，各自记录准备、校准和验收状态。更换运行环境需要结束当前实例；本版尚未实现运行中切换。

Agent 可以启动技能，在执行中继续观察、接收新指令，并决定何时取消。操作员负责准备设备、启用控制和急停。解除急停后，设备回到未使能的待命状态，之前的任务作废。

## 当前状态

当前源码版本为 **0.6.0**。本地 `v0.5.0` 标签保留审计基线。1.0.0 的发布条件见[实机验收指南](../../acceptance.md)。

| 接入对象 | 当前范围 |
|---|---|
| SO-101／现实 | LeRobot 0.6.1 适配器、俯视与腕部相机、原生 ACT 加载、持续执行和本地监督。尚待实机验收。 |
| G1／仿真 | 保留现有 MuJoCo 世界与步态控制器，CPU llvmpipe 渲染与控制回归已通过。 |
| SO-101／仿真；G1／现实 | 保留旧配置，当前绑定已禁用。 |
| 视觉模型 | 预置可编辑的 DeepSeek V4.1 Flash、GPT-6.1 Sol 和 GPT-6 Astra 注册项。接口与实机状态均从未验证开始。 |

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

进入“视觉模型”，配置供应商密钥，再主动运行接口检测。检测会产生模型调用费用，保存密钥不会触发调用。选择机器人及运行环境后创建会话，也可建立不连接机器人的对话。创建现实会话不会自动准备设备。

环境配置可参考 [.env.example](../../../.env.example)。模型设置和密钥保存在仓库外的 `NERV_HOME` 中，默认位置为 `~/.nerv`。

## 机器人接入

G1 仿真继续使用现有世界与策略子模块，并需要仿真依赖：

```bash
git submodule update --init --recursive
.venv/bin/python -m pip install -e '.[sim]'
```

在界面选择 G1、仿真和兼容世界。原有世界生命周期、场景工具和操作员视图继续保留。场景真值与第三人称相机不进入 Agent 的观测。

SO-101 使用独立的适配器环境，其中只安装 SDK 与 LeRobot 适配器：

```bash
python3 -m venv .venv-lerobot
.venv-lerobot/bin/python -m pip install -e ./packages/nerv-runtime-sdk -e ./packages/nerv-runtime-lerobot
.venv-lerobot/bin/nerv-runtime discover
.venv-lerobot/bin/nerv-runtime check --provider lerobot0.6.1
```

将 `NERV_LEROBOT_PYTHON` 指向该解释器，配置 `SO101_PORT`、校准时使用的 `SO101_ID`、原生相机 JSON 路径 `SO101_CAMERA_CONFIG`，以及冻结检查点目录 `SO101_ACT_CHECKPOINT`。相机、校准、场景和初始姿态须与训练条件匹配。现实配置继续标为 `verified: false`；`NERV_ALLOW_UNVERIFIED=1` 仅允许进入受监督的验收会话。

由操作员选择现实环境、创建会话，点击“准备设备”，审阅 worker 的工具清单，加载策略后启用控制。工具清单审阅属于初始设置，Agent 的每次工具调用不需要人工批准。准备设备会建立连接，并可能启用电机力矩；加载模型不会发送动作。技能结束后设备保持连接。软件急停保持姿态，不等同于断电，也不能代替硬件急停。

## 视觉模型

独立注册面板提供模型 ID、API 地址、协议、图像能力依据、工具支持、请求限制和推理强度。聊天区读取同一注册表。纯文字模型和 Mock 不进入正式运行目录，也不作为自动回退。

Sol 和 Astra 带工具时使用 Responses API，DeepSeek 使用独立的 Responses 适配器。界面按型号展示合法推理强度；未指定时省略参数，使用供应商默认值。系统分别记住上次模型和每个型号的强度。当前回合或机器人执行尚未结束时，修改会被拒绝。

接口检测通过不操作机器人的测试工具，分别检查图像输入、工具调用和多轮续接。地址、模型配置、强度或凭据改变后，原有检测不再适用。注册和接口检测通过均不代表实机验收。详见[视觉模型配置](../../vlm.md)。

## Agent harness

默认的 `AgentHarness` 通过可替换接口获得宿主 Link、模型供应商、上下文管理器和摘要器，不持有机器人对象，也不加载 ACT。

每个会话独立保存追加式事件日志、图片资产、任务和笔记。旧 JSON 会话执行一次性迁移，并保留 `.legacy` 源快照。JSON 快照可从日志重建；历史检索和分页阅读仅能访问当前会话。

上下文预算覆盖指令、笔记、摘要、完整工具调用组、工具定义、当前图片、供应商续接项和输出预留。Token 与图片预算属于估算；供应商返回实际用量时另行记录。摘要使用不带工具、图片及主对话推理续接的独立请求。摘要失败时保留原文；无法满足请求预算则暂停。

进程重启后，未结束的回合被记为中断、结果未知。系统恢复历史，不重放动作。新指令会使旧模型决策失效；已启动的技能仍由本地机制监督。

## 运行插件

`nerv-runtime-sdk` 可独立安装，不依赖 NERV 核心、厂商 SDK、PyTorch 或 MuJoCo。Python worker 在自己的环境内发现 `RuntimeProvider` 入口。非 Python 服务可实现相同 HTTP／MCP 协议，并登记服务 URL。

模块导入和 `describe()` 不得操作设备或 GPU。准备、加载策略、使能、执行、取消、急停、解除和断连分别处理。机器人描述、环境绑定和策略资产各有独立契约。宿主不会向 worker 注入整仓源码或模型密钥。

注册示例、协议接口和只读一致性检查见[适配器指南](../../runtime-sdk.md)。

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

发布顺序为 0.5.0 审计基线、0.6.x 开发，再于冻结配置的 10 次接入验收及故障测试通过后发布 1.0.0。单元测试通过或模型进入注册表不会自动触发发布。版本记录见 [CHANGELOG.md](../../../CHANGELOG.md)。

## 开发

[AGENTS.md](../../../AGENTS.md) 说明仓库分层。接口文档：[Brain](../../nerv-brain.md)、[Body](../../nerv-body.md)、[World](../../nerv-world.md)、[Operator](../../nerv-operator.md)、[Tool](../../nerv-tool.md)。场景和策略继续作为独立子模块维护。

NERV 使用 [MIT 许可证](../../../LICENSE)。厂商 SDK、模型权重、训练数据及外部资产遵守各自的许可证。
