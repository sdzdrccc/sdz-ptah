# sdz-ptah

用 agent 侧生图能力批量生成精致动漫头像，覆盖「一句话需求 → 候选图交付」的完整流程：
需求 → 提示词编译 → 4 变体 × 2 次批量出图 → 裁切补偿 → 裁切后评分与硬门槛 → 联系表 / 候选清单 + PNG / JPG 导出。

## 名字由来

`sdz-ptah` 取自埃及工匠之神 **Ptah**——以心思构想、以言语造物，对应这个项目的核心：
**用一句话生成一个形象**。

## 能力边界（写实话）

- **不承诺同一角色不变脸**：生成端没有 seed、参考图与 LoRA，只能做到「同批次看起来像同一角色」。
- **生成不可复现**：同一段提示词两次结果不同，台账只记录提示词与时间。
- **构图靠裁切补偿**：生成图眼睛稳定偏低，不靠提示词修构图，由裁切把眼睛抬到上部 1/3。
- **2K / 4K 是插值放大**：用 Pillow Lanczos 从原生约 1000px 的裁切放大，不新增细节，倍率越高越软。
- **部分检查不可用**：OCR、NSFW、情绪、风格、解剖与动漫超分（Real-ESRGAN 等）当前无本地依赖，对应闸门记为 `null`。

## 安装

在仓库根目录执行：

```bash
# 主写法
python skills/install.py

# Windows 备选：python 报 9009 说明没进 PATH，用 py -3
py -3 skills/install.py
```

默认安装到 TRAE 的全局技能目录 `%USERPROFILE%\.trae-cn\skills\sdz-ptah-avatar\`。
这个路径由 TRAE 规定、不能自选；装好后本机所有 TRAE 工作区与 agent 会话都能加载。

- 默认优先创建符号链接，改源码立即生效；
- Windows 无创建符号链接权限时自动回退为复制；
- **本机当前是复制模式**，改完 `skills/sdz-ptah-avatar/` 下的源码后需要重新执行上面的脚本同步。

装到其他宿主：

```bash
# 只扫描并打印本机实际存在的宿主技能目录，不安装
python skills/install.py --detect

# 装到指定宿主的技能目录：<target>/sdz-ptah-avatar/
python skills/install.py --target ~/.codex/skills
```

本机实测检测到 `skills/` 的宿主目录：`.trae-cn`（默认）、`.agents`、`.cc-switch`、`.codex`、
`.hub`、`.qoder-cn`、`.qoderwork`、`.qwenworkcn`、`.stepfun`、`.workbuddy`、
`.workbuddy-ai`、`.config/mimocode`。默认安装只写 TRAE 的 `.trae-cn\skills\`，其他目录未写入。

## 使用

在新会话里直接说需求即可，例如：

> 帮我生成一个银白长发、蓝瞳、清冷感的头像，做社交平台圆头像。

可选参数：默认输出 PNG（1024）；需要高清可指定 2K / 4K；有体积限制的网站或 App 可要 JPG。
每个需求最多 12 次生成调用，首轮 8 张（4 变体 × 2 次），不足再补量。

## 目录

```text
sdz-ptah/
├── README.md                      # 项目说明、安装与用法
├── skills/
│   ├── install.py                 # 安装脚本：把 skill 链接/复制到宿主技能目录
│   └── sdz-ptah-avatar/           # 头像 skill 本体（自包含，可独立运行）
│       ├── SKILL.md               # 执行入口：流程、硬约束、步骤与命令
│       ├── references/            # 规则与档案；新增画风/角色只加文件
│       │   ├── protocol.md        # 协议：字段、阈值、原因码、manifest、能力矩阵
│       │   ├── angles.json        # 角度矩阵：侧脸双向 + 正脸启用，3/4 预留
│       │   ├── backends.json      # 后端能力登记：是否支持参考图、张数、强度范围
│       │   ├── feedback.json      # 交付后不满意的改进方向目录
│       │   ├── styles/            # 风格档案（各自一套阈值）
│       │   │   ├── tv-anime-cel.json      # TV 动画赛璐璐
│       │   │   └── realistic-armor.json   # 写实/皮套（无脸装甲）
│       │   └── characters/        # 角色卡（外形特征 + 人工核对清单）
│       │       ├── satoshi.json       # 小智，有人脸（faced）
│       │       └── fox-armor.json     # 极狐皮套，无脸（faceless，轮廓清单）
│       └── scripts/               # 执行脚本（纯 Pillow，无 numpy/OpenCV）
│           ├── avatar_pipeline.py # 定位 → 裁切 → 评分 → 闸门 → 导出
│           ├── confirm_order.py   # 写 brief.confirm，控制两段式确认门
│           └── rounds.py          # 轮次、预算与满意度台账
├── ai-anime-avatar/
│   ├── outputs/
│   │   └── AI动漫头像生成方案.md  # 设计依据与阈值来源（可选外链）
│   ├── probe/                     # 生成端能力探针：入参面与重复性实测
│   └── verify/
│       ├── brief.json             # 验证用需求单
│       ├── control/               # 阴性对照（无脸/合成图）
│       └── raw/                   # 8 张正脸校准原图
└── .gitignore                     # 宿主配置、缓存、基准图与生成产物
```

运行期产物不入库：订单目录默认 `<工作区>/avatar-orders/<角色>-<NN>/`（如 `satoshi-01`），
一个订单只有一个目录、图片全部平铺，不建任何子目录。命名
`<前缀>-avatar-<N>.png`（成品）、`-src.jpg`（原图）、可选 `-2k.png` / `-4k.png` /
`-q88.jpg`；`N` 在订单内跨轮连续递增，轮次只记在 `brief.round` / `rounds.json` /
`manifest.json`。订单根另有 `brief.json`、`prompts.json`、`status.json`、
`manifest.json`、`contact_sheet.jpg`，由会话与脚本生成。
历史订单的旧结构（`<YYYYMMDD-HHMMSS>-<slug>/raw/` + `out/`）原样保留、不迁移；
`avatar_pipeline.py --layout legacy` 仅用于复跑旧单。
`ai-anime-avatar/verify/out*/`、`ai-anime-avatar/基准/`、`.trae/`、`.screenshots/`
与 `__pycache__/` 已在 `.gitignore` 中忽略。验证成品图可由 `avatar_pipeline.py` 完整复现。

## 依赖

- Python 3（安装脚本只用标准库）
- Pillow（裁切与评分脚本依赖，不需要 numpy / OpenCV）
- 动漫超分需额外依赖，当前**不可用**；替代路径见方案文档附录 D。
