# Midas-dev

基于 MIDAS Civil NX MAPI 的桥梁自动化建模与 JTG 规范验算。

本仓库提供 `midas-bridge-modeling` 技能，用于自动建立桥梁有限元模型并完成规范验算。当前已落地桥型为变截面预应力混凝土连续箱梁（悬臂浇筑，FCM），截面、工法与验算模块按可插拔方式组织，可扩展至其他桥型。

## 演示视频

https://github.com/user-attachments/assets/2625d91e-5eaf-47a0-8f88-faeede2e3c42

视频文件另存于 [`AI建模演示视频/`](AI建模演示视频/)，验算表格见 [`PSC验算表格/`](PSC验算表格/)。

## 功能

- 根据桥型、跨径、桥面宽度、截面形式、施工方法与荷载等级，生成带规范出处的决策文档，经确认后再建模。
- 自动建立节点、材料、截面（含变截面）、单元、结构组、边界条件、施工阶段荷载、二期与温度荷载、移动荷载、收缩徐变、施工阶段、分析控制、预应力钢束与荷载组合。
- 结合自建 JTG 计算引擎与主控表，完成抗弯、抗剪、抗裂验算与配束、配筋迭代。

## 环境准备

### 1. 连接 MIDAS Civil NX API

本技能通过 MAPI（REST API）驱动 MIDAS Civil NX，使用前需先连通 API 服务：

1. 打开 MIDAS Civil NX，加载或新建目标模型。
2. 在工具栏（Tools / 工具）中找到 API 设置。
3. 填入或生成 API Key。
4. 确认连接状态显示为已链接（Connected）。

若状态异常，请检查 API 服务是否启用、端口是否被占用，以及 Key 是否填写正确。

### 2. 配置环境变量

将 API Key 与服务地址写入 Windows 用户级环境变量，供技能脚本读取。技能不会硬编码或回显 Key。

| 变量名 | 说明 | 示例 |
|---|---|---|
| `MIDAS_MAPI_KEY` | MIDAS 中设置的 API Key | 你的 Key |
| `MIDAS_MAPI_BASEURL` | MAPI 服务地址 | `https://moa-engineers.midasit.com:443/civil` |

```powershell
[System.Environment]::SetEnvironmentVariable("MIDAS_MAPI_KEY", "<你的 API Key>", "User")
[System.Environment]::SetEnvironmentVariable("MIDAS_MAPI_BASEURL", "<MAPI 服务地址>", "User")
```

设置后重新打开终端使配置生效。

## 快速开始

1. 完成上述环境准备，确认 API 状态为已链接。
2. 加载本仓库 `skills/midas-bridge-modeling` 下的技能。
3. 描述建模需求，例如：建一座 70+100+70 m 变截面连续箱梁、悬臂浇筑、公路-I 级的 MIDAS 模型。技能将从决策文档起步，逐步完成建模与验算。

工作流如下：

```text
用户基本信息
      ↓
决策文档（每项取值注明规范出处，人工确认）
      ↓
逐章建模（逐步建立并回读自检、存盘）
      ↓
分析与反算（提取内力需求，反算钢束、钢筋与箍筋，录入后重分析）
      ↓
验算微调（迭代至主控项全部满足）
```

## 目录结构

```text
skills/midas-bridge-modeling/
├── SKILL.md            # 技能入口：工作流、准则与文档路由
├── reference/          # 决策文档模板、建模模块、截面库、施工方法、规范取值表
├── scripts/            # 复用脚本：钢束计算与生成、API 提取
└── assets/示例/        # 示例桥模型（.mcb / .mct）、设计图纸与说明

AI建模演示视频/
└── midas-demo.mp4      # 建模演示录屏

模型/
├── 预应力混凝土变截面连续单箱单室梁桥.mcb  # 桥梁有限元模型（MIDAS Civil NX）
└── README.md           # 模型说明

PSC验算表格/
├── *.xlsx              # JTG 验算与配筋估算表，共 15 张
└── README.md           # 表格分类说明
```

## PSC 验算表格

[`PSC验算表格/`](PSC验算表格/) 包含 15 张 Excel 计算表，覆盖持久状况、使用阶段、施工阶段的应力、抗裂、承载力验算，以及预应力筋、普通钢筋用量估算与最小配筋率检查。分类详见该目录内 `README.md`。

## 注意事项

- 全程采用 kN-m 单位制，建模之初通过 `PUT /db/UNIT` 显式设定。
- 确定性参数以现行规范为准，取值出处见 `skills/midas-bridge-modeling/reference/`。
- 运行 Python 脚本前设置 `PYTHONIOENCODING=utf-8`；MCT / OUT 文件为 GBK 编码。
- 模型修改后需执行 `POST /doc/ANAL` 重分析，并通过 `POST /doc/SAVEAS` 存盘。

## 相关文档

- 技能说明：[`skills/midas-bridge-modeling/SKILL.md`](skills/midas-bridge-modeling/SKILL.md)
- 演示视频说明：[`AI建模演示视频/README.md`](AI建模演示视频/README.md)
- 模型说明：[`模型/README.md`](模型/README.md)
- 验算表格说明：[`PSC验算表格/README.md`](PSC验算表格/README.md)
