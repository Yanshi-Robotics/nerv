# 控制方案与执行契约

[NERV](README.md) · [架构](architecture.md) · [MCP](mcp.md) · [English](../../guides/control-schemes.md)

NERV 0.9.1 改变控制架构。Brain Core 属于 NERV，负责将外部 Agent 绑定到 Run 与机器人实例，不包含决策模型。控制方案是操作者选择的不可变修订，规定机器人型号与运行环境允许的动作、任务期限和动作预算。

| 模板 | 决策来源 | 执行方式 |
|---|---|---|
| `external_vlm` | 外部 Agent | 有限的原子动作 |
| `external_skills` | 外部 Agent | 注册的运动技能 |
| `fixed_policy` | 操作者选择一个技能 | Runtime 执行固定策略 |

历史 `policy_group`、`visual_direct` 和 managed 模型配置可读，但不能启动内部推理。必须明确选择并授权新的外部控制方案；升级不读取或搬运旧模型凭据。ACT 与步态推理属于 Runtime 的动作执行能力。

`GET /api/control-schemes?sid=SESSION` 返回固定修订与实时兼容性；`POST /api/control-schemes` 保存修订；`POST /api/sessions/SESSION/control-scheme` 只能在原任务停止后选择方案。`task_start` 固定目标、方案修订、策略指纹与实例。`task_update` 已退役，更换任务目标前应取消原任务。

操作者先分配 Brain 与 Run，再启动仿真并明确授予控制权。启动 Run 不自动启用运动。每条 MCP 连接只操作一个 Brain。共享 World 中各机器人使用独立 Runtime，型号名不能代替实例身份。

外部 Agent 必须检查实际图像、确认新观测回执，再发送有限动作。观测、任务与重试标识不能跨 Brain 使用。一次动作消耗对应观测授权，下一次动作前必须重新观察。事件轮询与 Agent 自述不续期监督。Runtime 独立执行本地停止，不以模型延迟为理由自动确认图像。

转移控制权先撤销旧代次，再确认停止。重启或重连不重放运动。停止未确认时阻止替换执行。结束单个 Run 后机器人仍保留在共享场景中；共享 World 的停止仅由操作者触发。

不同技能之间的转换默认关闭，只有适配器明确验证的技能对才可开放。0.9.1 的 G1 转换资格验证与安装候选包上的独立导航验收仍待完成；开发探针不算发布验收，也不代表 ACT 技能转换或真机验证。

动作结果分别报告实测位移、目标偏差、受阻、跌倒、超时与稳定停止。跨过请求距离阈值不代表任务成功；`target_reached=null` 表示尚无经过验证的到达容差。Agent 自述保留作者身份，与 Runtime 回执及操作者侧独立评分分开。

模块清单 `release.json` 记录依赖锁、资源与哈希。安装先创建并验证新环境，再登记 Runtime；设备依赖不进入 Host。模块安装、运动授权与共享世界变更属于操作者操作，MCP 不提供终端或安装入口。
