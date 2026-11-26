# sdz-ptah

开源的动漫头像生成流水线：**参考图驱动生成 → 裁切补偿 → 质量闸门评分 → 多尺寸交付**。
生成后端可以替换：本地 ComfyUI（开放权重模型），或宿主智能体自带的生图工具。

> **项目状态：开发中（alpha）。**
> 宿主图生图路线已在真实环境跑通；本地 ComfyUI 路线的代码已就绪，并用模拟服务测试过，**尚未在真实 GPU 上实测**。
> 公开发布前还有几项必做工作（移出第三方角色素材、确定代码许可证等），见[路线图](#路线图)。

## 名字由来

`sdz-ptah` 取自埃及工匠之神 **Ptah**——以心思构想、以言语造物，对应这个项目的核心：
**用一句话生成一个形象**。

## 它做什么

一份「需求单」（角色描述 + 参考图 + 气质 + 目标平台 + 数量）经过以下步骤，交付可直接用作头像的方图：

1. **确认门**：参数确认前不调用任何生成工具。
2. **生成**：按配置的后端出图，可单段，也可两段式（先按参考图锁身份，再用动漫底模低强度重绘统一画风）。
3. **预算台账**：按调用次数计费，超预算停下询问，不静默超额；中断后可续跑，不重复计费。
4. **裁切补偿**：按人脸 / 内容框自动取景，修正生成图构图偏差。
5. **质量闸门**：原图质量分（面部、构图、风格、角度）与成品校验（截断、留白、画布、缩略图可读性）分开评分。
6. **交付**：1024 方图，可选 2K（2048）/ 4K（**4096**）/ JPG，附联系表与 `manifest.json`（记录后端、seed、是否可复现、许可快照）。

设计依据与阈值来源见[方案文档](design/outputs/AI动漫头像生成方案.md)。

## 能力边界（写实话）

- **一致性**：参考图驱动可以做到「与参考图是同一角色」；**不承诺同一角色跨批次不变脸**（角色 LoRA 尚未实现）。
- **可复现**：本地后端记录 seed、模型版本与环境版本，三者齐全时标记为可复现（待真实 GPU 验证）；宿主后端没有 seed，不可复现。
- **构图靠裁切补偿**：不靠提示词修构图；构图模板 + ControlNet 在路线图中。
- **2K / 4K 是插值放大**：Pillow Lanczos，不新增细节。档位口径是二进制：2K = 2048、**4K = 4096**（不是视频标准的 3840，详见 `docs/CONVENTIONS.md` §8）。
- **部分检查尚不可用**：OCR、NSFW、情绪、风格 embedding、解剖、动漫超分没有接入，对应闸门记为 `null`，**`null` 不算通过**。
- **已知问题**：风格闸门在纯白底头像上会误判（主体区计算在路线图 M2-1）；黑色瞳孔可能检测不到，导致正脸被误判为侧脸（M2-3）。

## 生成后端

后端登记在 [`references/backends.v2.json`](skills/sdz-ptah-avatar/references/backends.v2.json)。

| 后端 id | 类型 | 状态 | 模型许可 / 服务条款 |
|-|-|-|-|
| `agnes-aigc-img2img` | 宿主工具（Agnes 图生图，1 张参考图） | 已实测（维护者路线） | 宿主条款，未确认；参考图会发往远端 |
| `trae-generateimage` | 宿主工具（TRAE 文生图） | 可用，仅作对照 | 宿主条款，未确认 |
| `comfyui-animagine-t2i` | 本地 ComfyUI · Animagine XL 4.0 文生图 | 代码就绪，待实测 | CreativeML Open RAIL++-M（可商用，有用途限制） |
| `comfyui-animagine-i2i` | 本地 ComfyUI · Animagine XL 4.0 图生图（两段式画风步） | 代码就绪，待实测 | 同上 |
| `comfyui-qwen-edit-2511` | 本地 ComfyUI · Qwen-Image-Edit-2511 Q4_K_M（身份步） | 未接入：需导出工作流 | Apache-2.0 |
| `comfyui-neta-lumina-t2i` | 本地 ComfyUI · Neta Lumina（中文提示词） | 未接入：需导出工作流 | 主模型 Apache-2.0；文本编码器受 Gemma 使用条款约束 |
| `modelscope-qwen-edit` / `modelscope-qwen-edit-2511` | ModelScope 在线推理（API，免网页手工操作） | 代码就绪，待用真实 token 验证 | 免费推理服务条款非商用；参考图会发往远端 |

**不接入**：Midjourney / Niji（无官方 API，自动化调用违反服务条款）；InstantID / IP-Adapter FaceID 不作默认（动漫脸常检测失败，InsightFace 模型仅限非商用研究）。

## 硬件与依赖

| 用途 | 要求 |
|-|-|
| 确认、记账、裁切与评分 | Python 3（开发环境 3.12）+ Pillow；CPU 即可，不需要 numpy / OpenCV |
| 本地生成（ComfyUI 路线） | NVIDIA 显卡：SDXL 8 GB 起；Qwen-Image-Edit-2511 Q4_K_M 约 13 GB，建议 16 GB；系统内存建议 32 GB（未实测） |
| RTX 50 系显卡 | PyTorch 必须为 **CUDA 12.8 或更新**的构建，旧版会报 `sm_120` 不兼容 |
| Qwen-Image-Edit 量化版 | ComfyUI 需装 ComfyUI-GGUF 插件 |

## 安装

本项目以「技能（skill）」形式运行在宿主智能体里，在仓库根目录执行：

```bash
python skills/install.py            # 安装到 TRAE 全局技能目录（默认）
py -3 skills/install.py             # Windows 下 python 报 9009 时用这条
python skills/install.py --detect   # 只扫描本机已有的宿主技能目录，不安装
python skills/install.py --target ~/.codex/skills   # 装到其他宿主
```

默认优先创建符号链接，改源码立即生效；没有创建符号链接的权限时会自动改为复制，此时改完源码要重新运行一次安装脚本。

## 快速上手

### 1. 不需要 GPU：自检

```powershell
cd skills\sdz-ptah-avatar
py -3 -m backends selftest   # 用 mock 后端跑一遍单段与两段式
py -3 -m backends list       # 查看已登记的后端、状态与许可
```

### 2. 在宿主智能体里使用

安装后，在宿主的新会话里直接说需求即可，智能体会按 [`SKILL.md`](skills/sdz-ptah-avatar/SKILL.md) 执行全流程，例如：

> 帮我生成一个银白长发、蓝瞳、清冷感的头像，做社交平台圆头像。

默认输出 1024 PNG；可要求 2K / 4K 或 JPG。每单默认预算 10 次生成调用，首轮 2 张。

### 3. 本地 ComfyUI 路线

1. 启动 ComfyUI（默认 `http://127.0.0.1:8188`；其它地址用环境变量 `COMFYUI_URL` 指定）。
2. 阅读许可后下载 Animagine XL 4.0，放进 ComfyUI 的 `models/checkpoints/`。
3. 把 [`workflows/sdxl_t2i.api.json`](skills/sdz-ptah-avatar/workflows/sdxl_t2i.api.json) 和 `sdxl_i2i.api.json` 里的 `ckpt_name` 改成你的实际文件名，并在 `backends.v2.json` 填写模型 sha256。
4. 检查连通：`py -3 -m backends probe comfyui-animagine-t2i`。

目前能直接跑通的本地组合：

- **文生图**（无参考图，身份不保证）：确认时加 `--mode text2img --text2img-consent --backend comfyui-animagine-t2i`。
- **两段式**：宿主 Agnes 做身份步，本地 Animagine 做画风步：`--stages "identity:agnes-aigc-img2img,style:comfyui-animagine-i2i@denoise=0.35" --egress-consent`。
- **全本地两段式**（Qwen-Image-Edit 身份步 → Animagine 画风步）：等 Qwen 工作流接入后可用（路线图 M1-2）。

### 5. ModelScope 在线路线（不需要显卡）

1. 在 [modelscope.cn/my/myaccesstoken](https://modelscope.cn/my/myaccesstoken) 申请 token（需实名认证并绑定阿里云）。
2. 设置环境变量（只在本机终端设置，**不要写进任何文件或提交到仓库**）：`$env:MODELSCOPE_TOKEN = "<你的 token>"`。
3. 检查：`py -3 -m backends probe modelscope-qwen-edit`（不消耗额度）。
4. 确认时选 `--stages "single:modelscope-qwen-edit@steps=30" --egress-consent`，之后照常运行 `generate.py`。

免费推理服务条款为**非商用**，有动态限流；结果有 seed 但模型版本不固定，标记为不可复现。

### 4. 一单的完整命令

```powershell
$skill = "skills\sdz-ptah-avatar"

# ① 新建 / 切换订单：目录固定为 <订单根>\<角色 slug>，输出里有 order_id
py -3 "$skill\scripts\rounds.py" --new-order --root "D:\orders" --character mychar

# ② 写 brief.json（宿主智能体会自动写；最小示例）
#    {"order_id": "<上一步的 order_id>", "character_id": "mychar", "gender": "female",
#     "count": 2, "budget": 10, "style_id": "tv-anime-cel"}

# ③ 确认（未确认一律不生成）
py -3 "$skill\scripts\confirm_order.py" --brief "D:\orders\mychar\brief.json" `
  --confirm "确认生成" --reference "D:\refs\mychar.png" `
  --stages "identity:agnes-aigc-img2img,style:comfyui-animagine-i2i@denoise=0.35" `
  --seed 100 --egress-consent

# ④ 生成：先预检，再出图
py -3 "$skill\scripts\generate.py" --order "D:\orders\mychar" `
  --prompt-zh "<中文段>" --prompt-en "<英文段 / Danbooru tag，含 safe>" --dry-run
py -3 "$skill\scripts\generate.py" --order "D:\orders\mychar" `
  --prompt-zh "<中文段>" --prompt-en "<英文段>"

# ⑤ 裁切、评分、导出
py -3 "$skill\scripts\avatar_pipeline.py" --raw "D:\orders\mychar" `
  --brief "D:\orders\mychar\brief.json" --formats 1024 `
  --style "$skill\references\styles\tv-anime-cel.json" --budget 10

# ⑥ 交付后回填满意度
py -3 "$skill\scripts\rounds.py" --order "D:\orders\mychar" `
  --set-satisfaction --round 1 --satisfaction satisfied
```

`generate.py` 的退出码：

| 码 | 含义 | 下一步 |
|-|-|-|
| 0 | 原图已落盘并记账 | 运行第 ⑤ 步 |
| 2 | 输入错误，或有未完成的生成 | 按提示修正；用 `--resume` 继续或 `--discard-pending` 放弃 |
| 4 | 未确认 / 后端能力不满足 / 未同意外发 / 配置错误 | 改 brief 后重新确认；不会自动换后端或退回文生图 |
| 5 | 预算不足 | 询问是否追加，同意后加 `--allow-over-budget` |
| 8 | 后端故障（如 ComfyUI 未启动） | 修复后 `--resume`，已花的调用不重复计费 |
| 9 | 宿主侧后端：已写出 `<前缀>-call-<N>.json` | 宿主按调用单生成并把图放回订单目录，再 `--resume` |

## 订单目录与产物

订单目录默认 `<工作区>/orders/<角色 slug>/`，**一个角色一个目录**，图片全部平铺、不建子目录，运行期产物不入库：

| 文件 | 说明 |
|-|-|
| `<前缀>-ref-<N>.png` | 归档的参考图（sha256 记在 brief） |
| `<前缀>-avatar-<N>-src.<ext>` | 生成原图 |
| `<前缀>-avatar-<N>-s1.<ext>` | 两段式的身份步证据图（不参与评分） |
| `<前缀>-avatar-<N>.png` | 1024 成品；可选 `-2k.png` / `-4k.png` / `-q88.jpg` |
| `<前缀>-avatar-<起>-<止>-sheet.jpg` | 本单联系表 |
| `brief.json` / `prompts.json` / `status.json` | 需求单、提示词、状态 |
| `rounds.json` | 轮次、预算与满意度台账 |
| `generation.json` | 每次生成的分段、seed、后端结果与许可快照 |
| `manifest.json` | 评分与交付台账 |
| `<前缀>-call-<N>.json` / `generation-pending.json` | 等待宿主执行的调用单 / 未完成的生成（完成后删除） |

编号 `N` 跨订单、跨轮次连续递增，不覆盖、不重置；预算只统计当前订单。历史订单的旧结构（`<时间戳>-<slug>/raw/` + `out/`）原样保留，`avatar_pipeline.py --layout legacy` 用于复跑。

## 目录结构

命名与目录规则见 [CONVENTIONS.md](docs/CONVENTIONS.md)，2026-11-26 整理的变更记录见 [CHANGELOG-命名整理.md](docs/CHANGELOG-命名整理.md)。

```text
sdz-ptah/
├── README.md
├── docs/                              # 项目级文档：规范与变更记录
│   ├── CONVENTIONS.md                 # 命名与目录规范（长期规则）
│   └── CHANGELOG-命名整理.md          # 命名整理变更说明
├── skills/
│   ├── install.py                     # 安装脚本：链接 / 复制到宿主技能目录
│   └── sdz-ptah-avatar/               # 技能本体（自包含，可安装）
│       ├── SKILL.md                   # 执行入口：流程、硬约束、步骤与命令
│       ├── scripts/                   # 脚本：confirm / generate / avatar_pipeline /
│       │                              #   rounds / check_prompt / compile_prompt /
│       │                              #   list_presets / path_names / ref_palette /
│       │                              #   skin_check / probe_gradio_studio
│       ├── backends/                  # 后端适配器：ComfyUI 本地 / 宿主侧 / mock / ModelScope
│       ├── workflows/                 # ComfyUI API 格式工作流
│       └── references/
│           ├── protocol.md            # 字段、阈值、原因码、manifest
│           ├── prompt-writing-spec.md # 提示词编写规范
│           ├── backends.v2.json       # 后端注册表（默认读取）
│           ├── backends.json          # 1.0 注册表（兼容保留）
│           ├── angles.json            # 角度矩阵：侧脸双向 + 正脸，3/4 预留
│           ├── generation-presets.json / feedback.json
│           ├── modelscope-runbook.md  # ModelScope 在线路线操作手册
│           ├── styles/                # 风格档案（tv-anime-cel / solidbg / realistic-armor）
│           ├── characters/            # 角色卡（外形特征 + 人工核对清单）
│           └── prompt-examples/       # 提示词样例
├── app/                               # 本地网页控制台（服务 + 前端 + 测试）
│   ├── server.py / simple_input.py / image_prep.py
│   ├── orderlock.py / api_errors.py   # 订单互斥锁、错误码映射
│   ├── config.json / start.bat        # 配置（端口 8766）/ Windows 双击启动
│   ├── web/                           # 前端：index.html / app.js / style.css
│   ├── tests/                         # 单元测试（unittest，119 项）
│   ├── uploads/                       # 运行时上传缓存（gitignore）
│   └── custom/                        # 运行时生成的用户角色/风格档案（gitignore）
├── design/                            # 知识资产：方案 / 评审 / 验证 / 校准基准
│   ├── outputs/                       # 方案正文与设计依据
│   ├── review/                        # 评审清单、核查结果、实测记录、魔搭模型清单
│   │   └── diagnostics/               # 诊断脚本（diag_NN_）与输出（.out.txt）
│   ├── probe/                         # 生成端能力探针（公开前清理，见路线图 M0-1）
│   ├── baselines/                     # 阈值校准基准（gitignore，含正样本/风格参考）
│   └── verify/                        # 验证样本；输出目录统一 out-<变体>（gitignore）
└── orders/                            # 运行时订单数据（gitignore）
```

> 标注 gitignore 的目录为运行时产物，不入库；`.workbuddy/`（工作日志）与 `.trae/`（宿主配置）同样不入库，未在树中列出。

## 许可与合规

> 以下为工程说明，**不构成法律意见**。涉及许可、标识义务与角色版权的判断，请自行核实或咨询专业意见。

- **代码许可：尚未确定**（路线图 M0-3）。在仓库加入 `LICENSE` 文件之前，代码默认不授予开源许可。
- **代码许可 ≠ 模型许可 ≠ 数据许可**：三者分别标注。模型权重**不随仓库分发**，请从官方来源下载并遵守各自许可；完整的逐组件许可表见[方案文档 9.1](design/outputs/AI动漫头像生成方案.md)。
- **默认路线只选允许商用的组件**（Animagine XL 4.0、Neta Lumina 主模型、Qwen-Image-Edit-2511）。NoobAI-XL 的许可禁止任何商用（含生成物），只作可选、默认关闭。
- **生成内容**：使用者对自己的输入与产出负责。国内发布时请关注《人工智能生成合成内容标识办法》（2025-09-01 施行）的标识要求，自动标识功能在路线图 M5-2。
- **角色版权**：请使用原创或已获授权的角色。仓库内的示例角色卡 `satoshi` 是第三方作品角色，仅作内部阈值校准，公开发布前会替换为原创角色（路线图 M0-1 / M0-2）。
- **商业 API**：只作可选适配器，需用户自带 key，仓库不内置任何共享 key；请遵守各平台条款（如 Gemini、NovelAI 要求年满 18 岁）。
- 本项目**不提供托管服务**，只支持本地自部署。

## 内容安全

- 只做动漫风格头像，**不接收真人照片作参考图**。
- Animagine 类模型的提示词强制带 `safe`，负向词包含 `nsfw, explicit`。
- NSFW 自动检测尚未接入（路线图 M2-2），未接入时该项记 `null`，不判通过。
- 涉及未成年形象的角色，禁止生成任何性化或不当内容。

## 路线图

完整计划、依赖和完成标准见[方案文档第 10 节](design/outputs/AI动漫头像生成方案.md)。摘要：

| 阶段 | 内容 |
|-|-|
| **M0 · 公开发布前** | 移出第三方角色素材（含 git 历史）、原创示例角色、代码许可证与第三方声明、补全 `.gitignore`、同步 `protocol.md` |
| **M1 · 默认路线落地** | RTX 50 系环境、接入 Qwen-Image-Edit-2511 与 Neta Lumina 工作流、实测并定稿默认后端、提示词编译脚本、角色 LoRA 可行性 |
| **M2 · 质量闸门** | 风格闸门改算主体区（ToonOut）、WD tagger 身份核对与 NSFW 初筛、动漫眼睛检测、裁切联合求解眼位、复评与基线 |
| **M3 · 生成能力** | 构图模板 + ControlNet、角色 LoRA 流程、3/4 侧脸、动漫超分、透明 PNG |
| **M4 · 扩展后端** | ModelScope、阿里云百炼、OpenAI / Gemini / NovelAI 适配器，云 GPU 镜像 |
| **M5 · 工程与合规** | 模型下载脚本（逐组件确认许可）、AIGC 标识、单元测试与 CI、公开评测集 |

## 文档

- [方案文档](design/outputs/AI动漫头像生成方案.md)：路线选型、质量保障、端到端链路、合规边界、路线图
- [`SKILL.md`](skills/sdz-ptah-avatar/SKILL.md)：智能体执行手册
- [`protocol.md`](skills/sdz-ptah-avatar/references/protocol.md)：字段、阈值与原因码
- [后端适配器接口设计](design/review/AI动漫头像-后端适配器接口设计.md)

## 致谢

本项目的默认路线依赖以下开源项目与模型（各自许可以官方发布为准）：
[ComfyUI](https://github.com/comfyanonymous/ComfyUI)、
[Animagine XL 4.0](https://huggingface.co/cagliostrolab/animagine-xl-4.0)、
[Neta Lumina](https://huggingface.co/neta-art/Neta-Lumina)（基于 Lumina-Image-2.0）、
Qwen-Image-Edit、[Pillow](https://python-pillow.org/)。
