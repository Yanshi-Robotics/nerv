# NERV

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![MCP](https://img.shields.io/badge/protocol-MCP-6f42c1.svg)](https://modelcontextprotocol.io)
[![MuJoCo](https://img.shields.io/badge/sim-MuJoCo-orange.svg)](https://mujoco.org)
[![LeRobot](https://img.shields.io/badge/real-LeRobot-ffcc4d.svg)](https://github.com/huggingface/lerobot)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](../../../LICENSE)

<a href="../../../README.md"><img src="https://img.shields.io/badge/Language-English-2f81f7?style=flat-square" alt="English"></a>
<a href="README.md"><img src="https://img.shields.io/badge/%E8%AF%AD%E8%A8%80-%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-e67e22?style=flat-square" alt="简体中文"></a>

机器人的神经系统：一个大模型大脑、一具机器人身体、一个世界（仿真或真实），由一个平台耦合起来，每一条信号都经它路由、过闸、留痕。

> 🤖 **如果你是 AI agent，先读 [AGENTS.md](../../../AGENTS.md)**——那是面向机器的入口：分层规则、每个事实住在哪、命令。

## 概览

NERV 把**大脑**、**身体**、**世界**耦合在一起，手边还有**工具**，但它不拥有其中任何一个。大脑是插件；身体、世界、工具各是独立进程。NERV 是中间的神经：登记它们、组装大脑看到的东西、让每条指令过安全闸、只留一份日志。可以把它理解成没有 ROS 的 roscore。

类比是人体。大脑思考、从不动手。身体执行大脑的意图，并带着自己的快反射。世界是身体摸到和看到的东西：今天是仿真公寓，明天是你的桌面。工具是大脑不想瞎猜时伸手去拿的东西——先从一个计算器开始。NERV 是它们之间的神经，也是信号在途中唯一能被拦下的地方。

NERV 是 [ANIMA Zero](../../history/anima-zero.md) 的继任者，只改了一个地址：Zero 把动作发给世界，NERV 把动作发给身体。世界只提供物理和感知。身体与世界是两个对象——身体感知世界，世界反过来推它：这堵墙走不过去，那扇门走得过去。大脑只能透过身体看世界，这正是仿真世界与真实世界对大脑等价的原因。

## 主要特性

- **两套系统、两个时钟**：大脑（System 2）每步推理一次；身体（System 1）以 30–50 Hz 跑一个学出来的策略，跑完一条指令为止，并回报实测结果。二者只在 NERV 里交汇；策略永远不等模型。
- **只看传感器，绝不看真值**：腕上的相机、桌上的相机、测距仪——每一个都是「谁装着谁发布」的传感器流。NERV 按会话声明组装大脑的观测，并拒绝任何像仿真器真值的东西。
- **会话 = 大脑 × 身体 × 世界**：注册表决定哪些三元组存在；世界没给某个身体备竞技场，就在开始前拒绝。
- **仿真与真机一套词汇**：一个身体家族的动词——`move_joints` 这样的原语、`move_forward` 这样的技能——只写一次。仿真和硬件是同一条电机总线的两个端点。
- **大脑是通信协议背后的插件**：Claude、OpenAI 兼容、Ollama 或 mock，跑最简单的看–想–过闸–动循环。NERV 看不到提示词，只看到 `Think`、`CallTool`、`Say`。
- **只有人武装了会话，硬件才会动**：每个身体动作都过安全闸；开关没拨之前身体什么都收不到，大脑会被告知原因。
- **可审计、可打断、可桥接**：每一帧、每个念头、每条指令、每条总线消息进同一份日志；技能跑到一半也能停；任何会说 NERV/Body 或 NERV/Tool 的进程都是节点——包括桥接过来的 ROS 系统。

---

## 架构

四种节点、五条接口、一个平台。节点之间互不 import，只靠注册表文件和下面的接口相遇。

<div align="center"><img src="../../images/structure.svg" alt="NERV 位于大脑、身体、世界、工具之间" width="860"></div>

每条接口分两个平面。数据平面是大脑可以看到的；控制平面只有 NERV 和操作者看得到——探活、拉起、复位、真值、视频、武装开关。没有任何东西从第二个平面流向第一个。

| 接口 | 谁和谁 | 数据平面 | 控制平面 |
|---|---|---|---|
| **NERV/Operator** | 人 ⇄ NERV | — | 注册表、会话、聊天与事件流、停止、武装 |
| **NERV/Brain** | NERV ⇄ 大脑插件 | 进：`UserMessage`、`ToolResult`；出：`Think`、`CallTool`、`SetRegister`、`Say` | 加载、能力、用量 |
| **NERV/Body** | NERV ⇄ 身体节点（MCP） | tools = 动词、`nerv://observation`、guidance、config、capabilities | `/health` `/status` `/config`（武装）`/stop` `/hold`（急停）`/release` `/stream` |
| **NERV/World** | 身体 ⇄ 世界（总线）· NERV ⇄ 世界（传感器） | 电机总线；装在世界里的传感器流 | 拉起、`/health`（epoch）、spawn、`/reset`、`/status`、`/stream` |
| **NERV/Tool** | NERV ⇄ 工具节点（MCP） | 只有 tools | `/health` |

<details>
<summary><b>NERV/Operator</b>——HTTP + SSE，由 <code>nerv serve</code> 提供</summary>

`POST /api/sessions {brain, body, world, sensors}` 先做兼容性校验再拉起节点、建会话；`POST /api/sessions/{id}/arm {armed}` 拨武装开关；`POST /api/chat/stream {session, text}` 流式推送 `start · perception · thinking · tool_call · gate · progress · tool_result · reply · done`。节点：`GET /api/nodes`、`/manifest`、`POST …/approve`。注册表：`GET /api/registry` 附兼容矩阵。全表见 [docs/nerv-operator.md](../../nerv-operator.md)。
</details>

<details>
<summary><b>NERV/Brain</b>——消息，不是提示词</summary>

大脑拿到一个 `Link`：`observe()`、`tools()`、`history()`、`registers()`、`call_tool()`、`set_register()`、`say()`、`stop_reason()`。一步的决策是一条 `Think(text, tool_calls)`；安全闸在 `call_tool` 内部运行。三道停止闸由平台侧结束一轮——人的打断、步数上限、墙钟——每一种都是可续跑的停顿。寄存器（`set_core_task`、`add_note`）是大脑的工作记忆，常驻系统提示。见 [docs/nerv-brain.md](../../nerv-brain.md)。
</details>

<details>
<summary><b>NERV/Body</b>——ANIMA Zero 的四通道，换了说话的人</summary>

MCP（`/mcp/`）：`tools/list` + `tools/call`（进度通知即生命迹象）、`resources/read nerv://observation`（状态 JSON，随后是按 `state.cameras` 命名的图像 blob）、`prompts/get guidance`、`nerv://config`、`nerv://capabilities`（`family`、工具种类 `read · primitive · skill`、传感器、武装、epoch）。HTTP：`/health`、`/status`、带节点自身武装开关的 `/config`、`/stop`、`/hold`（锁姿急停——摔倒时自动锁上）、`/release`、`/stream`。`nerv conformance <url>` 按这份契约逐条检查。见 [docs/nerv-body.md](../../nerv-body.md)。
</details>

<details>
<summary><b>NERV/World</b>——电机总线与世界的传感器</summary>

总线在仿真里是 ZMQ 上的 JSON，在硬件上就是驱动本身：`spawn(joint_names, pd_mode, kp, kd, torque_limit, default_pos)`、`read → {t, joint_pos, joint_vel, imu_quat, imu_gyro, imu_acc, odom_xy, odom_yaw, base_height}`、`write {targets, kp?, kd?}`、`reset`、`sensors`、`sensor(name)`、`rays(angles_deg, max_range_m)`、`epoch`。PD 环住在世界侧（implicit · explicit · position_actuator），理由和它住在舵机固件里一样。世界传感器是 `camera:top` 这样的流，身体的策略按全速率读、NERV 每步读一帧。控制平面：带重启即变的 epoch 的 `/health`、`/status`（真值，给人看）、`/sensors`、`/reset`、`/stream`（跟拍相机）。见 [docs/nerv-world.md](../../nerv-world.md)。
</details>

<details>
<summary><b>NERV/Tool</b>——只有工具</summary>

一个只有 `tools/list` + `tools/call` 和 `/health` 的 MCP 服务。它的函数并进大脑的工具单，与身体动词并列；重名时身体优先。不受武装开关约束，但一样过闸、一样入日志。见 [docs/nerv-tool.md](../../nerv-tool.md)。
</details>

### 一条指令，两个时钟

<div align="center"><img src="../../images/flow.svg" alt="System 2 与 System 1 的两条时间线" width="860"></div>

**看**——大脑请求观测；NERV 从身体的观测和会话声明的每一路环境传感器各取一帧组装。**想**——一次模型调用：回话，或选一个工具。**过闸**——NERV 检查会话是否武装、动词是否允许、参数是否在身体声明的范围内；拒绝以工具结果的形式回给大脑，大脑据此行动。**动**——原语写一个目标并稳住；技能在身体节点上起一个策略循环——50 Hz 的步态把「两米」翻成速度指令、读总线、写关节目标——直到完成、超时或被停，途中发进度、最后回实测结果：走了多远、被挡、摔倒、夹爪闭合。然后大脑再看一次。

### 原语与技能

| 家族 | 种类 | 动词 | 身体回报什么 |
|---|---|---|---|
| humanoid | 技能 | `move_forward(meters)` | 实测距离、刹车 / 卡住 / 摔倒、前方余量 |
| humanoid | 技能 | `turn_left(degrees)`、`turn_right(degrees)` | 实测转角、漂移 |
| arm | 读 | `read_joints()` | 关节角（°）、夹爪（%） |
| arm | 原语 | `move_joints(targets, duration_s)` | 实测角度、最大误差、被夹限的关节 |
| arm | 原语 | `nudge(joint, delta_deg)` | 实测角度 |
| arm | 原语 | `set_gripper(percent)` | 实测开度 |

原语是一个目标，插值到位并稳住。技能是一段学出来的行为，跑到一条指令结束为止；它的策略是策略发布架上的一个发布件，在 `bodies/<name>/body.yaml` 的 `skills` 里点名。训练好的模型就是这样接到平台上的——永远不经过大脑。

```text
src/nerv/nerve/       五条接口：消息类型、电机总线、传感器流、一致性检查
src/nerv/platform/    注册表、会话、观测组装、路由、安全闸、启动器、信任、HTTP、CLI
src/nerv/brain/       大脑插件：看–想–过闸–动循环、模型供应商、提示词
src/nerv/body/        身体节点：NERV/Body 服务端、技能运行器、家族（humanoid、arm）、总线端点
src/nerv/world/       世界节点：MuJoCo 服务
src/nerv/tool/        工具节点：计算器
bodies/  tools/       注册表                    worlds/  子模块 nerv-world（场景 + 世界描述）
policies/             子模块 nerv-policies      frontend/  网页
```

---

## 安装

```bash
git clone --recurse-submodules <this repository> && cd nerv
python3 -m venv .venv && .venv/bin/pip install -e ".[all]"   # 平台、大脑、工具、MuJoCo——一个 venv
cp .env.example .env                                         # API key，或本地 Ollama
.venv/bin/nerv doctor                                        # 配了什么、哪些三元组可行
```

仿真需要的一切都在 checkout 里：`worlds/` 是 [nerv-world](https://github.com/Yanshi-Robotics/nerv-world) 子模块（原名 alice-house 的场景库，连同世界描述），`policies/` 是 [nerv-policies](https://github.com/Yanshi-Robotics/nerv-policies) 子模块（已发布的步态策略：`policy.onnx` + `contract.json` + `release.yaml`）。如果 clone 时没带 `--recurse-submodules`，跑一次 `git submodule update --init --recursive`；忘了的话 `nerv doctor` 会提醒。

## 运行

```bash
.venv/bin/nerv chat --world apt2 --body humanoid-unitree-g1 --brain claude
```

NERV 拉起世界节点，再拉起身体节点和工具，逐个等 `/health`，把身体的自述给你看并请你批准，然后开始对话。输入 `arm` 武装会话（仿真里是安全的），再说「往前走两米、左转，然后告诉我 17 乘 23」。

```text
nerv chat  --world W --body B --brain X      终端里的对话
nerv run   --world W --body B --say "..."    脚本化跑一轮
nerv serve                                   :8000 上的 NERV/Operator API（构建后同时提供网页）
nerv node world|body|tool -- <args>          手动起一个节点，或在另一台机器上起
nerv registry                                大脑、身体、世界、工具、兼容矩阵
nerv conformance URL [--kind body|tool|world]  按接口检查一个节点
nerv doctor                                  配了什么、够不够得着
```

网页（`frontend/`，Next.js）是 NERV/Operator 的一个客户端：`npm install && npm run dev` 在 :8100 开发，或 `npm run build:static` 后交给 `nerv serve` 提供。它展示大脑看到的观测、只给操作者看的跟拍相机、信任面板、武装开关，以及每一条实时信号。

### 真机机械臂

对 `arm-lerobot-so101` 来说，电机总线就是 LeRobot 的 `SOFollower`。要么在同一个 venv 里 `pip install -e ".[lerobot]"`，要么让 `NERV_LEROBOT_PYTHON` 指向一个已装 LeRobot 的环境；在 `.env` 里填 `SO101_PORT`、`SO101_ID`、`SO101_CAMERAS`，照常用 `lerobot-calibrate` 校准。对硬件的会话双重未武装——NERV 里一道、身体节点里一道——你武装之前，大脑对每条指令都会收到「未武装」；武装时请把手放在电源开关旁。抓取技能是你用 LeRobot 训练后放到策略发布架上的策略；技能运行器已经在等它。

## 出厂内容

| 身体 | 家族 | 动词 | 传感器 | 总线端点 |
|---|---|---|---|---|
| `humanoid-unitree-g1` | humanoid | 技能 `move_forward`、`turn_left`、`turn_right`（已发布步态策略） | `camera:head`、8 路测距 | 仿真（MuJoCo）· 真机（LeRobot G1 桥，**未验证**） |
| `arm-lerobot-so101` | arm | `read_joints`、`move_joints`、`nudge`、`set_gripper` | `camera:wrist` + 你订阅的任何世界相机 | 仿真（MuJoCo）· 真机（LeRobot `SOFollower`） |

| 世界 | 类型 | 支持 | 世界传感器 |
|---|---|---|---|
| `apt2` | 仿真 | `humanoid-unitree-g1`——nerv-world 的曼哈顿复式顶层公寓 | 无 |

| 工具 | 函数 |
|---|---|
| `calculator` | `calc(expression)`——四则运算、乘方、开方；拒绝一切非算术的东西 |

机械臂的世界——先是一张真实的桌子，再是仿真的——是下一个要加的东西；注册表已经为它留好了形状。

## 添加你自己的

**大脑**实现 [NERV/Brain](../../nerv-brain.md)。**身体**是 `bodies/` 下的一个目录，带 `body.yaml`——家族、传感器、技能及其策略、执行器规格、总线端点——和一份给大脑读的 `guidance.md`；动词随家族而来。或者一个直接说 [NERV/Body](../../nerv-body.md) 的进程，ROS 机器人就是这样接入的。**世界**是 `worlds/` 下的一个目录，带 `world.yaml`——类型、资产、为哪些身体备了竞技场、带哪些传感器——或任何说 [NERV/World](../../nerv-world.md) 的端点。**工具**是任何说 [NERV/Tool](../../nerv-tool.md) 的 MCP 服务。**前端**说 [NERV/Operator](../../nerv-operator.md)。新家族少见一些，走 pull request。

## 致谢

场景来自 [nerv-world](https://github.com/Yanshi-Robotics/nerv-world)（原名 alice-house）。人形的步态策略在 yanshi-rl-lab 里用 Isaac Lab 训练。物理是 [MuJoCo](https://mujoco.org)；硬件接入是 [LeRobot](https://github.com/huggingface/lerobot)；SO-101 模型来自 [TheRobotStudio/SO-ARM100](https://github.com/TheRobotStudio/SO-ARM100)；G1 模型源自 [MuJoCo Menagerie](https://github.com/google-deepmind/mujoco_menagerie)。NERV 是 [ANIMA Zero](../../history/anima-zero.md) 的继任者。
