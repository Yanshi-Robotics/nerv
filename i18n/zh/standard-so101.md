# 用 NERV 运行标准 SO-101

[English](../../guides/standard-so101.md)

NERV 0.6.3 是开发候选版，真机验收尚未完成。本版接入一台使用 LeRobot 0.6.1 的标准 SO-101 从臂，两台 OpenCV 相机分别命名为 top 和 wrist，采用 640 × 480、30 FPS。五个臂关节使用角度，夹爪使用 LeRobot 原生的 0–100 表示。软件测试和模型加载不能证明真实抓取的可靠性。

先在 LeRobot 中完成训练与标定，保留训练产物、标定文件和课程相机配置。推理时不需要连接主臂。使用 NERV 前退出占用同一串口或相机的遥操作程序。

## 安装候选版

也可使用 [GitHub 启动器](github-install.md)，安装后执行 `nerv`，或直接通过 npx 启动。启动器管理两个 Python 环境并发现已安装的设备运行时。下面的 wheel 方式作为无需 Node.js 的安装选项保留。

采用 Python 3.12，主程序与设备运行时分别安装。三个 wheel 文件必须来自同一次构建；维护者通过 `python scripts/build_release.py` 在 `temp/release/wheels` 生成候选包，该命令不会发布文件。将这个目录复制到学生电脑，命名为 wheels。

Linux 和 macOS：

```bash
python3.12 -m venv .nerv-host
python3.12 -m venv .nerv-device
.nerv-host/bin/python -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl 'wheels/nerv-0.6.3-py3-none-any.whl[platform]'
.nerv-device/bin/python -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl wheels/nerv_runtime_lerobot-0.6.3-py3-none-any.whl
.nerv-host/bin/nerv serve
```

Windows PowerShell：

```powershell
py -3.12 -m venv .nerv-host
py -3.12 -m venv .nerv-device
.\.nerv-host\Scripts\python.exe -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl 'wheels/nerv-0.6.3-py3-none-any.whl[platform]'
.\.nerv-device\Scripts\python.exe -m pip install wheels/nerv_runtime_sdk-0.6.3-py3-none-any.whl wheels/nerv_runtime_lerobot-0.6.3-py3-none-any.whl
.\.nerv-host\Scripts\nerv.exe serve
```

设备包会安装 `lerobot[feetech]==0.6.1`。根据自己的系统和推理设备，按 PyTorch 官方说明安装相应版本。不要为了安装 NERV 修改原有训练环境。主程序包已包含网页，学生不需要安装 Node.js 或下载其他机器人的仿真资产。

打开启动命令显示的地址，默认是 `http://127.0.0.1:8000`。候选版真机通路仍标记为未验收；开展有人监督的验收时，需要在启动主程序前设置 `NERV_ALLOW_UNVERIFIED=1`。Linux、macOS 使用 export，PowerShell 使用 `$env:NERV_ALLOW_UNVERIFIED='1'`。该设置只允许使用实验通路，不会连接机械臂或启用动作。

## 配置设备

打开“设备与策略接入”，填写设备环境中 Python 可执行文件的绝对路径，适配器填 `lerobot0.6.1`，再点击“读取配置选项”。在“配置设备”中填写串口、机器人 ID、已有标定目录、课程 cameras.json 文件和推理设备。推理设备选择 cpu、cuda 或 mps，并确认当前后端支持所选设备。

标定目录中必须包含与机器人 ID 同名的 JSON 文件。相机配置需要指定两个不同设备，名称固定为 top 和 wrist，分辨率和帧率符合标准。保留课程相机工具记录的实际旋转参数。设备名称无法证明相机安装位置正确，启用动作前仍需查看两个画面，确认顶视与腕部视角没有接反。

配置保存在 NERV_HOME 指定的目录，默认是 ~/.nerv。保存配置不会连接机械臂。修改设备配置前先关闭当前机器人实例；导入策略不会覆盖原始训练产物。

## 导入 ACT 策略

在“导入策略”中填写新的策略 ID、pretrained_model 目录，以及训练任务和场景条件。只有训练数据使用了标准课程约定，才能勾选对应声明。模型未记录的单位和通道顺序会作为“用户声明”保存，不会被标记为独立验证结果。

导入需要 config.json、model.safetensors、预处理与后处理 JSON 文件，以及处理器引用的全部张量文件。系统核对六个标准位置通道和 top、wrist 图像输入，再计算文件指纹。不提供自定义关节映射或自动单位转换。模型改变后需要重新检查；新版本使用新的策略 ID。

“检查产物”核对契约和文件指纹。“加载检查”在设备环境加载模型与原生处理器，不连接硬件，也不执行推理。要用真实观测试推理，请按下一节操作。

命令行使用同一后端：

```bash
.nerv-host/bin/nerv policy import --python /absolute/path/to/.nerv-device/bin/python --provider lerobot0.6.1 --id pick-v1 --path /absolute/path/to/pretrained_model --conditions 'Task and camera setup used during training' --standard-declared
.nerv-host/bin/nerv policy inspect --python /absolute/path/to/.nerv-device/bin/python --provider lerobot0.6.1 --id pick-v1
.nerv-host/bin/nerv policy check --python /absolute/path/to/.nerv-device/bin/python --provider lerobot0.6.1 --id pick-v1 --device cpu
```

将示例路径替换为自己的绝对路径；Windows 使用 Scripts 目录中的可执行文件。路径包含空格时加引号。这些命令不会下载权重。

## 连接与运行

新建 SO-101 真机会话。无需 API 密钥时选择“仅人工操作”；需要 Agent 控制时选择已经配置的视觉模型。在操作工具箱的设备与访问区域阅读能力清单，再允许访问。

“准备设备”会连接 LeRobot，并可能启用电机扭矩。由操作者检查工作区域后点击。NERV 使用所选标定，发现不一致时拒绝继续，不自动重新标定。连接后查看两个画面，确认安装位置、方向与场景。

加载策略，保持动作未启用，点击“检查策略，不下发动作”。这一步用当前观测运行 ACT 处理器与模型，返回候选位置，不向电机下发目标。检查完成后，再启用控制并从操作工具箱运行策略。人工模式依赖可见操作页面持续取得观测；关闭或隐藏页面后，监督超时会停止下发动作。操作者仍需持续观察真实场景。

VLM 直接控制使用有限的关节与夹爪工具。每次决策关联近期观测和当前设备实例，动作插值在本地完成。执行后重新观察，不能把命令完成当作抓取成功。本版不提供笛卡尔坐标控制、其他机器人结构或非标准单位接入。

切换 ACT 与直接控制前先停止当前执行。“紧急停止”撤销控制权并尝试保持实测位置，解除后不会自动重新启用或恢复旧动作。“断开连接，保持扭矩”关闭通信但保留扭矩；“卸力并断开连接”是另一项人工操作，需要先支撑机械臂。软件停止不是物理断电；串口已经失联时，系统不能确认保持命令执行成功。

## 排查问题

| 现象 | 检查内容 |
|---|---|
| 找不到运行时适配器 | 是否选择了安装 SDK 与适配器的设备环境 Python |
| 标定缺失或不一致 | 机器人 ID、目录，以及课程中的 LeRobot 标定流程 |
| 串口不可用 | 系统权限和其他程序占用；NERV 不会关闭外部程序 |
| 相机配置被拒绝 | 名称、分辨率、帧率，以及是否指向两个不同设备 |
| 策略不兼容 | 标准 ACT 导出、原始处理器、维度、顺序和单位声明 |
| 决策过期 | 重新观察和决策，不复用旧命令 |
| 目标被调整或未到位 | 对照请求目标、LeRobot 返回的下发目标和实测位置 |

动作回执保存在 NERV_HOME 下的 runtime-events 目录。会话图像和日志可能包含私人场景及路径，分享前逐项检查并脱敏。报告问题时提供主程序、SDK、适配器版本，系统与架构，LeRobot、PyTorch 版本，推理设备、原始错误和复现步骤。不要附上 API 密钥或未经检查的配置压缩包。

## 平台状态与扩展

Linux 是主要真机验收平台。Windows 原生、WSL2 和 macOS 在社区完成硬件测试前保持实验性状态。WSL 的串口与摄像头透传需要分别验证，参考 [Microsoft USB 接入说明](https://learn.microsoft.com/en-us/windows/wsl/connect-usb)。Apple Silicon 与 Intel Mac 分别记录依赖和硬件结果。

主程序通过 Runtime SDK 调用设备，不导入 LeRobot。适配器持有唯一设备连接和动作下发权。模型插件通过 nerv.policy_backends 注册，接收经过检查的产物清单和推理设备，实现 reset、action、close，返回标准原生位置目标，不打开硬件。ACT 自行维护动作分块队列。后续模型可在插件内处理动作序列或远程推理，但仍需经过观测时效与下发检查。

SmolVLA、GR00T 是后续扩展目标，本版没有交付其运行实现。接口测试不能证明这些模型已经兼容。主程序与运行时的边界见 [SDK 说明](../../packages/nerv-runtime-sdk/README.md)。
