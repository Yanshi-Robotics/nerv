# 架构与控制

[English](../../guides/architecture.md) · [NERV](README.md)

NERV 0.6.3 将对话、任务监督与本地执行分开。System 1 表示 Runtime 内的执行层，不是额外一个 Python 环境。

标准 SO-101 worker 持有唯一的 LeRobot 设备实例。ACT 推理和有限关节控制都向同一个调度器提交候选目标；主进程不导入 LeRobot，也不加载策略张量。

## 架构
NERV Agent 将机器人目标交给 System 2；System 1 执行受限动作和策略，Robot Runtime 独占设备与传感器。World Control 管理仿真世界干预。宿主协调控制权、实例生命周期与事件记录。机器人驱动与策略推理运行在独立的 worker 环境中。

<div align="center"><img src="../../assets/readme/images/layers.svg" alt="NERV Agent 对话、System 2 任务监督、System 1 执行与仿真世界控制的分层关系" width="960"></div>

原有节点拓扑继续作为传输基础：

<div align="center"><img src="../../assets/readme/images/structure.svg" alt="NERV 连接 Agent harness、独立机器人运行后端、世界与工具服务，并区分观测和操作员控制路径" width="860"></div>

Agent 与 System 2 分别使用独立的 harness、上下文、摘要、供应商续接状态和用量记录。两者共用追加式事件日志，通过任务视图区分各自记录。运行后端声明传感器与技能，界面读取这些能力。仿真与现实各有独立的环境绑定。操作员的场景控制和仿真真值不进入 Agent 观测。模块边界由[接口类型](../../src/nerv/nerve/)定义。

## 对话与世界控制
右侧聊天区属于 NERV Agent，机器人执行和手动控制期间仍可交流。**停止回复**仅取消当前对话决定；**停止任务**等待本地执行确认；**急停**锁定动作，操作员解除后回到未授权待命。

中间相机外框内显示 System 2 的公开决策摘要、执行反馈和宿主事件。动态气泡带有来源和时间，不进入相机原始帧或策略输入。独立事件流支持重连与去重，历史会话不显示实时画面。

业务能力分为五类：`agent_tools` 提供计算和会话历史；`robot_actions` 提供机器人动作和技能；`robot_tasks` 管理任务；`world_control` 修改仿真配置与状态；`instance_control` 管理实例生命周期。供应商的函数调用和 MCP 只负责传输，不合并这些能力的权限。

在**新建会话**中选择 SO-101、仿真和一个桌面场景，再配置该场景支持的物品槽位。物品类型、标称尺寸和质量来自世界目录，柔性材料参数尚未经过实物标定。每个实例独立生成 MJCF、配置与来源哈希。

**工具箱 → 仿真世界**与 Agent 的世界命令使用同一服务。重置保留所选物品和世界进程，结束任务并撤销动作授权。改变物品拓扑时使用**结束并重建实例**；旧会话保留历史，不能控制新实例。天气、家具、风格等能力只有在世界声明并实现后才会出现；当前桌面场景提供物品替换与重置。现实环境拒绝修改世界属性。

创建会话要求已登记模型密钥。创建、查看和手动操作仿真不会发起模型请求。语言控制需要配置 VLM，并产生调用费用。当前桌面仿真没有部署 ACT 或哈诺塔执行策略。长期自主目标命名为 **ultimate**；本版仍是由 Agent 协调操作的系统。

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

可选诊断 API 检查图像输入、工具调用和多轮续接。诊断结果不限制创建 VLM 会话或使用模型；这两项只要求已登记非空密钥。仅操作员会话不需要密钥。已有密钥无需重新登记。密钥后来失效时仍可创建会话，调用时显示明确错误。失败回执保留用户输入，并提供重试和模型设置入口；重试不会重放旧机器人动作。

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
