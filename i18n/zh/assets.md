# 导入、修改和运行仿真资产

[English](../../guides/assets.md) · [NERV](README.md)

在「设置 → 我的资产」中导入 MJCF 目录或 ZIP，创建个人副本并保存版本。原目录保持不变；
官方安装包不能直接修改，需要先创建个人副本。

## 保存个人版本

1. 安装官方资源包，或导入包含 `nerv-asset.json` 的目录。
2. 点击「创建个人副本」，展开「文件与版本」查看编辑目录。
3. 用自己的编辑器修改目录中的 MJCF、纹理或清单。
4. 点击「检查并保存版本」。文件检查通过后才会保存新版本。
5. 点击「检查仿真加载」，使用已安装的仿真环境加载场景并推进物理步骤。画面效果和任务成功需要另外验证。
6. 在「新建会话」中选择保存后的场景。运行中的会话继续使用原版本，保存修改不会替换它的场景。

导出会把所选已保存版本、清单和许可文件打包成 ZIP；尚未保存的修改不会进入导出包。
可以把这个 ZIP 导入另一份 NERV 配置，不需要保留原始编辑目录。

## 声明场景

[桌面示例](../../examples/assets/tabletop/nerv-asset.json) 展示文件格式，不含机器人。
可以运行的场景还需要声明兼容的已安装仿真 Runtime。例如，包含标准 SO-101 的场景可以声明：

```json
{
  "schema": 1,
  "id": "my-so101-scene",
  "name": "我的 SO-101 场景",
  "kind": "scene",
  "engine": "mujoco",
  "entry": "scene.xml",
  "license": "MIT",
  "runtime": {
    "bodies": ["arm-lerobot-so101"],
    "spawn": {"initial_joints": {}},
    "physics": {"dt": 0.002, "render_width": 640, "render_height": 480}
  }
}
```

机器人标识需要与已安装 Runtime 的注册信息一致，许可填写文件实际采用的许可。
填写机器人名称不会自动添加机器人模型，也不会使不兼容的控制器变得可用。
资源包需要包含机器人模型、网格和纹理；关节名称与初始位置必须符合模型定义。

NERV 只接受声明式数据，不执行资源包提供的构建代码。Runtime 声明不能指定 Python 解释器、
导入模块、安装依赖或改变真机绑定。绝对路径、越界引用、缺失依赖、符号链接和可执行插件
都会被拒绝；通过文件检查后，还需要用 MuJoCo 检查模型能否加载。

## 命令行

```bash
nerv assets import --path /path/to/scene
nerv assets inspect --id <saved-id>
nerv assets clone --id <saved-id> --name "My copy"
nerv assets save --id <copy-id>
nerv assets check --id <copy-id>
nerv assets export --id <copy-id> --path /path/to/new-export.zip
```

命令行与网页使用同一个存储服务和检查流程。保存的场景通过内容指纹定位；官方更新不会
合并或覆盖个人修改。场景加载、控制器兼容和任务表现分别检查，加载通过不代表策略已经验收。

归档资产后，新会话不再显示它。已保存版本、运行中的会话和个人修改仍保留，归档不会释放
磁盘空间；可在“已归档资产”中恢复。对应命令为 `nerv assets remove --id <saved-id>`
和 `nerv assets restore --id <saved-id>`。本版不提供资产永久删除。
