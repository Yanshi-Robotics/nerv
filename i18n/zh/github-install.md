# 从 GitHub 安装与启动

[English](../../guides/github-install.md)

启动器直接通过 GitHub 分发，不要求在 npm 注册表发布包。先安装 Node.js 22 及以上版本（包含 npm）和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。uv 负责建立独立的 Python 3.12 环境，缺少 Python 时可自动下载。NERV 不向系统 Python 或课程训练环境安装依赖。

## 首次安装

把以下命令中的 COMMIT 替换为发布说明提供的完整提交号。尚未推送的本地提交无法从 GitHub 安装。0.6.3 启动器仍是开发候选版，安装成功不等于通过实机验收。

```bash
npm install -g "https://github.com/Yanshi-Robotics/nerv/archive/COMMIT.tar.gz"
nerv
```

首次启动自动安装主程序及配套 SDK，后续启动复用该环境。浏览器打开终端显示的地址，默认是 `http://127.0.0.1:8000`。需要指定端口时执行 `nerv serve --port 8100`；按 Ctrl+C 结束本次服务。

也可不做全局安装，直接运行：

```bash
npx --yes --package="https://github.com/Yanshi-Robotics/nerv/archive/COMMIT.tar.gz" nerv
```

这里使用 GitHub 源码归档地址。npm 的 Git 克隆安装会同时初始化子模块，额外下载仿真与策略仓库。源码归档包含前端源码，安装时编译一次网页；打包好的 npm 候选文件已包含网页。两种方式仍需下载正常的软件依赖，并非离线安装包。

## 按需安装设备环境

标准 SO-101 使用：

```bash
nerv runtime install lerobot0.6.1
nerv runtime list
```

这会在独立环境中安装 LeRobot 和 Feetech，下载量可能明显大于主程序。该命令不连接串口、不标定，也不使机械臂带电。PyTorch 和推理设备仍需在目标系统验证。

打开“设备与策略接入”。检测到唯一一个已安装运行时时，界面自动填写适配器和 Python 路径。再填写自己的串口、机器人 ID、已有标定与相机配置，按[标准 SO-101 指南](standard-so101.md)导入策略。

使用 npx 时，在命令末尾的 nerv 后追加 `runtime install lerobot0.6.1` 即可。之后启动主程序会从同一个用户目录发现该环境。

## 数据目录与更新

正常启动不需要设置 NERV_HOME。安装版和源码版均默认使用 `~/.nerv`；Windows 对应用户主目录下的 `.nerv`。

| 内容 | `.nerv` 下的位置 |
|---|---|
| 会话、事件和图像 | memory/sessions |
| 日志、动作回执 | logs、runtime-events |
| 设备配置、策略清单 | bodies、artifacts |
| 模型设置、能力授权 | vlm.json、trust.json |
| 主程序和设备环境 | envs |
| 已安装运行时记录 | runtimes |
| 自动下载的 Python 与缓存 | python、.cache |

Linux 和 macOS 的密钥保存在私有 credentials.json 中，Windows 继续使用系统凭据管理器。训练权重和标定文件保留原位置，由配置引用。npm 按自身规则存放程序代码和下载缓存，这些目录不承担 NERV 会话存储。

更新时安装新的已发布归档包。启动器根据源码指纹创建对应环境，保留旧环境和用户数据。先停止机器人任务，再由操作者重新启动程序；启动器不会关闭现有服务或搬动日志。

NERV_HOME 仅保留为显式的测试隔离选项。旧源码版曾把会话和日志写入仓库，这些文件仍留在原处，不会自动并入 `.nerv`。迁移前需要备份，不得覆盖已有会话或凭据。安装过程异常中断时，可能留下错误信息中指出的空 `.installing` 目录；确认安装进程结束后，再删除该目录并重试。

维护者可用 `npm pack --pack-destination temp/launcher-distribution` 生成本地候选文件，先通过 npm 或 npx 验证该 tgz，再发布源码提交。不使用 Node.js 的机器仍可选择 wheel 安装方式。
