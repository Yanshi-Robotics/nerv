# NERV 0.9.1 架构

[English](../../guides/architecture.md) · [NERV](README.md)

Brain Core 是随 NERV 安装的有状态交互模块。外部 Agent 负责目标理解、视觉判断、规划、模型调用与上下文；Brain Core 负责身份绑定、观测、能力、授权、任务和回执；Runtime 负责驱动、ACT／步态策略、高频控制与停止。MCP 是通信协议，不承担控制状态机的职责。

![平台关系](../../assets/readme/diagrams/zh/platform-091.svg)

| 对象 | 契约 |
|---|---|
| Project | 一台机器人的可复用配置与历史 |
| Run | 固定项目修订及机器人实例的一次运行 |
| Brain instance | 固定关联一个 Run、一个机器人、一个外部 Agent 身份 |
| Robot instance | 独立关节、执行器、传感器、Runtime 与控制记录；不同于机器人型号 |
| World instance | 固定场景、机器人组成与出生点；一个物理时钟、真实共享碰撞；生命周期独立于单个 Run |

操作者先分配 Run／Brain，再由 Agent 启动已分配的运行。共享世界在启动前组合，模型关节带实例命名空间，总线端点映射回策略原始关节名。控制器、队列和任务分别保存；另一台机器人的几何仍参与碰撞与避障。组合器读取源场景生成实例，不手工修改 MJCF 或第三方机器人原件。

![实例生命周期](../../assets/readme/diagrams/zh/lifecycle-091.svg)

结束一个共享 Run 只撤销它的控制，使其 Runtime 在世界中保持，不关闭世界或另一台 Runtime。共享世界停止只属于操作者；拓扑变化必须新建世界。Host 恢复时中断原 Run，不恢复运动。原 Agent 可以读取历史，但新运动必须重新建立有效绑定并由操作者授权。

Project／Run／Task 日志继续作为事实来源。观测确认是客户端的声明；动作接受不等于执行完成；Agent 自述不等于独立任务评分。Brain 不保存外部完整对话、隐藏推理或模型密钥。

![执行监督](../../assets/readme/diagrams/zh/supervision-091.svg)

安装包排除旧 nerv.brain、nerv.harness、nerv.vlm 和内部决策模块。源码与历史保留。旧模型、密钥和聊天接口退役，旧内部决策方案必须显式重选；固定策略继续支持。这是兼容性变化，不是普通补丁。

![未来部署](../../assets/readme/diagrams/zh/deployments-091.svg)

未来部署关系是：**独立本地 Agent 项目 → NERV Brain Core → Runtime／机器人**。Brain Core 不成为独立安装的 Agent 产品。远程云接入、多机器人协作规划、新策略训练和真机验收不属于本版。共享 G1 独立验收仍是交付门槛，架构图不作为验收证据。
