# 《方案补充清单》第六节 · 7 项待核实结果

| 项目 | 内容 |
|-|-|
| 核查日期 | 2026-11-23 |
| 对应清单 | `review\AI动漫头像生成方案-方案补充清单.md` 第六节 |
| 硬件前提 | 由 GTX 1660 SUPER 6 GB 改为 **i5-14600KF + RTX 5060 Ti 16 GB** |
| 说明 | 以模型卡 / 官方页面为准；第三方评测数值仅作参考，需本机实测。**不构成法律意见** |

---

## 总表

| # | 核查项 | 结论 | 对方案的影响 |
|-|-|-|-|
| 1 | Illustrious / NoobAI / Neta Lumina 许可 | NoobAI **明确禁止商用（含生成物）**；Illustrious 各版本许可不一致；Neta Lumina **Apache-2.0** | NoobAI 不能作默认；Illustrious 需逐版本确认；Neta Lumina 可作默认候选 |
| 2 | Animagine XL 4.0 | CreativeML Open RAIL++-M，**允许商用**，有用途限制 | 可作 SDXL 系默认底模 |
| 3 | InsightFace 模型 | 代码 MIT；**预训练模型仅限非商用研究** | InstantID / FaceID 移出默认路线（叠加动漫检测失败，理由更充分） |
| 4 | 质检模型 | WD tagger Apache-2.0；ToonOut（动漫抠图）MIT；deepghs 动漫脸检测为自定义免责许可；Aesthetic Predictor 2.5 **AGPL-3.0** | 前两者可默认启用；美学评分需隔离或换替代 |
| 5 | 本机显存（改为 5060 Ti 16 GB） | SDXL、Neta Lumina 轻松；需 PyTorch CUDA 12.8+ | 2.5 / 0 节「6 GB 硬约束」整体作废，需重写 |
| 6 | Qwen-Image-Edit-2511 量化 | Q4_K_M GGUF 约 13.2 GB，16 GB 卡可跑 | 第一升级候选可直接本地化，不再依赖 ModelScope |
| 7 | 商业 API 条款 | 四家均把生成物权利归用户；Gemini 有年龄与免费档数据条款 | 可作可选适配器；默认不启用 |

---

## 1 · Illustrious-XL / NoobAI-XL / Neta Lumina

### NoobAI-XL（以 1.1 为准）
- 许可：`fair-ai-public-license-1.0-sd`，继承自 Illustrious v0.1，并**追加条款**。
- 追加条款要点：
  - **禁止任何形式商用**，明确包括模型、衍生模型**以及模型生成的产品**。
  - 衍生模型、合并模型、LoRA 及基于其的产品**必须开源**，并公开配方、提示词与工作流。
- 训练数据含 Danbooru 与 e621；模型卡带 `not-for-all-audiences` 标签。
- **结论**：不能作项目默认路线；如要支持，只能作「用户自选、非商用」适配器，并在下载脚本中强制展示条款。

### Illustrious-XL
- v0.1：fair-ai-public-license；不鼓励用于牟利或闭源用途；衍生模型须开源并公开数据集与合并配方；**禁止将闭源衍生模型商业化**；生成结果的上传与传播不受限制。v0.1 本身标注为研究用途底模。
- v2.0：Hugging Face 元数据标注为 `creativeml-openrail-m`，但模型卡正文未写许可细节。
- 官方同时运营在线生成平台，部分版本（如 v1.0）未开源。
- **结论**：**各版本许可不一致**，必须逐版本锁定。若采用，优先 v2.0 并在 9.1 表中写明「以 HF 元数据为准，正文未载明，需持续跟踪」；不作为第一默认。

### Neta Lumina
- 许可：**Apache-2.0**。
- 基于 Lumina-Image-2.0；文本编码器 Gemma-2B；VAE 为 16 通道 FLUX VAE。
- 当前**只支持 ComfyUI**，官方要求 ≥ 8 GB 显存。
- 支持 Danbooru tag 与中 / 英 / 日自然语言。
- **注意**：Gemma 文本编码器与 FLUX VAE 是随包分发的组件，各自的许可（Gemma 使用条款、FLUX VAE 所属许可）需单独确认是否与 Apache-2.0 兼容。
- **结论**：主模型许可友好，可作默认候选；组件许可待补查。

---

## 2 · Animagine XL 4.0

- 许可：CreativeML Open RAIL++-M（原样沿用 SDXL 许可，无追加）。
- 允许：商用、修改、分发、私用。
- 禁止：违法活动、有害内容、歧视、剥削等（RAIL 用途限制条款）。
- 要求：附许可副本、注明修改、保留声明。
- 其他事实：基于 SDXL 1.0 重新训练，约 840 万张图；**只吃 tag 提示词，自然语言效果差**；有 `safe / sensitive / nsfw / explicit` 分级标签——默认 prompt 应强制带 `safe`。
- **结论**：SDXL 系中**许可最友好**的动漫底模，建议作为 A 路线默认。RAIL 用途限制需在 README 中转述并要求下游遵守。

---

## 3 · InsightFace

- 代码：MIT，学术与商用均可。
- 训练数据及其训练出的模型：**仅限非商用研究**；手动下载与 Python 包自动下载的模型都适用此政策。
- 官方 2025-11 更新：开源人脸识别模型包（如 `buffalo_l`）商用需单独联系授权。
- **结论**：InstantID、IP-Adapter FaceID 依赖这些模型 → 对外默认路线**不使用**；仅保留为「维护者本地实验」备注。

---

## 4 · 质检模型

| 工具 | 许可 | 结论 |
|-|-|-|
| WD EVA02-Large Tagger v3（SmilingWolf） | **Apache-2.0**（HF 元数据）；训练数据为 Danbooru | 可默认启用；输出 rating（general / sensitive / questionable / explicit）可作 NSFW 初筛 |
| PixAI Tagger v0.9（备选） | 权重 Apache-2.0，基于 WD v3 EVA02 继续训练，标签更新 | 可作 tagger 备选 |
| ToonOut（BiRefNet 动漫抠图微调） | 代码 / 权重 **MIT**；数据集 CC-BY 4.0；动漫测试集像素精度 95.3% → 99.5% | 主体蒙版首选 |
| BiRefNet 原版 | MIT | 通用备选 |
| deepghs `yolo-face`（动漫脸检测） | 自定义「model-distribution-disclaimer-license」 | 不是标准开源许可，**需读原文**再决定是否默认启用 |
| Aesthetic Predictor V2.5（SigLIP） | **AGPL-3.0** | 若仓库用 MIT / Apache，直接打包会引入 AGPL 义务——建议作可选插件或换替代 |

---

## 5 · 本机硬件：RTX 5060 Ti 16 GB + i5-14600KF

### 结论
- **SDXL（Animagine / Illustrious）**：16 GB 绰绰有余（第三方指南：8 GB 已覆盖 SDXL）。
- **Neta Lumina**：官方要求 ≥ 8 GB，可跑。
- **Flux.1-dev**：第三方同机测试约 37 秒 / 张（RTX 5090 约 8.8 秒），可用但偏慢；16 GB 可流式加载 FLUX。
- **Qwen-Image-Edit-2511**：见第 6 项，量化后可跑。
- **角色 LoRA 训练（SDXL）**：16 GB 通常可行，需实测。

### 必须注意
- **PyTorch 版本**：5060 Ti 为 Blackwell 架构（sm_120），旧版 PyTorch 会报「sm_120 不兼容」——必须安装 **CUDA 12.8 或更新** 的 PyTorch 构建。ComfyUI 便携版、xformers 等依赖也要选对应版本。
- **原 1660 SUPER 的 fp16 黑图问题不再适用**，可以正常用 fp16 / bf16。
- **14600KF 没有核显**：显示输出会占用 5060 Ti 的一部分显存，跑 13 GB 级模型时余量偏紧——建议关掉不必要的 GPU 加速程序，或预留 offload。
- **内存**：Qwen-Image 系文本编码器较大，offload 时吃系统内存——建议 32 GB 以上（推断，需实测）。

### 文档需改
- 0 节第 5 条、2.5「本机基线」、2.6、3.7「环境更新」、附录 D.1 第 2 条「8 GB+ 显存或可租云」——全部按新硬件重写。
- 2.1 简易度评级：本地 SDXL / Qwen-Image-Edit 量化版门槛下降，排序需重排。

---

## 6 · Qwen-Image-Edit-2511 量化版

- 许可：Apache-2.0（量化版继承）。
- 第三方指南：
  - **Q4_K_M GGUF 约 13.2 GB → 适合 16 GB 卡**；Q4_0 约 11.9 GB → 12 GB 卡。
  - ComfyUI 有内置 `image_qwen_image_edit_2511` 模板。
  - 加约 850 MB 的 Lightning LoRA，采样步数可从 40 降到 4。
- 社区项目已在 RTX 4070 Ti SUPER 16 GB 上验证 ComfyUI + GGUF 方案，并封装了 FastAPI 接口——可参考作为后端适配器原型。
- 另有 Nunchaku 4-bit 量化版。
- **结论**：5060 Ti 16 GB 上可本地跑 2511，**第一升级候选不再依赖 ModelScope token**，也绕开了「免费推理非商用」的条款问题。量化与 Lightning LoRA 对身份保持的影响需实测。

---

## 7 · 商业 API 条款

| 平台 | 生成物权利 | 需注意 |
|-|-|-|
| OpenAI（gpt-image） | 用户拥有 Output，OpenAI 转让其权利 | 遵守使用政策 |
| Gemini | Google 不主张生成内容所有权 | 使用者须年满 18 岁；**不得用于面向未成年人的服务**；免费档提交内容与生成结果会被用于改进 Google 产品——**参考图不应走免费档**；不得绕过安全过滤 |
| NovelAI | 用户保留内容权利，条款未禁止商用 | 动漫专用；API 自动化使用的具体条款需单独确认 |
| 火山引擎 Seedream | 第三方资料显示多参考图、最高 4K；参考图按版本可能另计费 | 官方商用条款与计费以火山引擎官方页面为准（本次只查到第三方转售渠道资料） |

- **结论**：四家都可作「可选适配器」，默认不启用；Gemini 的年龄条款与免费档数据条款需写入 README。
- Midjourney 维持原结论：无官方 API，不进入适配器。

---

## 据此调整的默认路线建议

| 角色 | 推荐 | 许可 |
|-|-|-|
| 默认生成底模（tag 驱动） | **Animagine XL 4.0** | RAIL++-M，可商用 |
| 默认生成底模（中文自然语言） | **Neta Lumina** | Apache-2.0（组件待查） |
| 身份锁定 / 编辑 | **Qwen-Image-Edit-2511 Q4_K_M 本地** | Apache-2.0 |
| 属性校验 + NSFW 初筛 | WD EVA02 tagger v3 | Apache-2.0 |
| 主体抠图 | ToonOut | MIT |
| 可选、非商用 | NoobAI-XL、InstantID / FaceID | 禁止商用 / 非商用研究 |
| 需逐版本确认 | Illustrious-XL | 版本间不一致 |

## 第二轮核查（2026-11-23，补查遗留 4 项）

### 8 · Neta Lumina 组件许可

| 组件 | 许可 | 要点 |
|-|-|-|
| 主模型（Unet） | Apache-2.0 | 见第 1 项 |
| 文本编码器 Gemma-2B（`gemma_2_2b_fp16.safetensors`） | **Gemma Terms of Use**（Gemma 2 在其适用清单内） | 不是 Apache，见下 |
| VAE（16 通道 FLUX VAE，`ae.safetensors`） | Apache-2.0（FLUX.1 的 autoencoder 权重按 Apache-2.0 发布，dev 与 schnell 共用同一份；来源为第三方转述，建议再对照 BFL schnell 仓库） | 与 Flux.1-dev 主模型的非商用许可**无关** |

**Gemma Terms 对本项目的影响**：
- Google 不主张对生成内容的权利，生成物由使用者负责。
- 使用受 Gemma 禁止用途政策约束，不得违法使用。
- **再分发**（含以 API / 网页形式提供托管服务）需同时满足：把用途限制写进下游协议并告知用户、附上 Gemma Terms 副本、修改文件需显著标注、非托管分发要附带一个指定内容的 Notice 文本文件。
- **结论**：仓库不分发权重、只提供下载脚本时，义务较轻——下载脚本展示 Gemma Terms 并要求确认即可。若将来提供在线托管服务，会被认定为「分发」，需补齐上述四项。因此 Neta Lumina 的整体许可应写为「主模型 Apache-2.0 + 文本编码器 Gemma Terms + VAE Apache-2.0」，**不能简单标成 Apache-2.0**。

### 9 · deepghs `yolo-face`（动漫脸检测）许可

- 模型标注的是 deepghs 自定义的「Model Redistribution Disclaimer License（RDL）」。它**不是一份授权许可，而是转载免责声明**：
  - 知识产权仍归原作者，deepghs 只是转载方，不主张权利。
  - 使用、修改、分发**完全以原始发布时的许可为准**。
  - 使用者需保留原版权声明与许可文件，并注明原始来源。
  - deepghs 不对侵权、准确性等承担责任。
- **结论**：RDL 本身不给出可否商用的答案，必须**追溯每个检测模型的上游许可**。另外这些模型名为 YOLOv10 / YOLOv11——若基于 Ultralytics 框架训练，上游很可能是 **AGPL-3.0**（YOLOv11 来自 Ultralytics，YOLOv10 来自清华 THU-MIG，许可需分别核对，此判断为推断）。
- **建议**：默认不打包；作为可选插件。若需要默认启用的动漫脸检测，优先找明确 MIT / Apache 的替代，或用 WD tagger + ToonOut 蒙版近似定位人脸区域。

### 10 · NovelAI 条款（官方 ToS，2025-12-08 生效）

| 条款 | 内容 | 对适配器的影响 |
|-|-|-|
| 生成物权利 | 用户保留生成内容的全部权利，NovelAI 不主张所有权 | 可商用（叠加角色 IP 问题仍需自行处理） |
| 年龄 | 须年满 18 岁 | README 注明 |
| 账号 | 不得把账号托管在公开域名上、不得允许第三方远程使用账号、不得转租转售 | **只能「每个用户填自己的 key」**，不能做共享账号的在线服务 |
| 自动化 | 除非另行明确同意，不得使用无视服务限制或给服务造成过大压力的机器人 / 自动化系统 | 适配器必须内置限速与并发上限，不做批量刷图 |
| 转售 | 不得复制、托管、转授权或转售服务 | 不能把 NovelAI 封装成自己的付费服务 |
| 参考图 | 图生图的参考图、蒙版会被加密短期缓存，NovelAI 自身无法解密，到期自动删除 | 隐私风险较低，可在 README 说明 |
| 日志 | 未经同意不记录请求内容 | — |

- 官方文档有 API Reference 页面。
- **结论**：可作「用户自带 key、限速、仅本地使用」的可选适配器。

### 11 · Seedream（火山方舟）

- **已确认**：火山方舟官方图片生成教程写明 Doubao Seedream 系列支持文本、单图与多图输入，可做文生图、多图融合与图像编辑——多参考图能力有官方出处。
- **未确认**：生成内容的权利归属与商用条款。本轮在火山引擎官网公开页面检索不到对应的服务协议原文（协议通常需登录控制台、开通服务时查看）。
- **结论**：保持「待核实」。开通火山方舟时请在控制台查看《火山方舟服务协议》或模型服务专用条款，重点看：生成内容知识产权归属、输入素材（参考图）是否用于训练、内容安全与标识要求。核实前不进入默认适配器列表。

### 12 · 5060 Ti 实测

- 需新卡到手后在本机进行，无法远程替代；第三方数据见第 5、6 项。
- 建议实测矩阵（每项记录：单图耗时、峰值显存、系统内存占用、`raw_quality` / `pass@4`）：

| # | 路线 | 设置 | 关注点 |
|-|-|-|-|
| 1 | Animagine XL 4.0 | 1024², 28 步, CFG 5, fp16 | 基线速度；白底头像风格闸门 |
| 2 | Neta Lumina | 1024², 30 步, CFG 4–5.5 | 中文提示词效果；显存 |
| 3 | Qwen-Image-Edit-2511 Q4_K_M | 40 步 vs Lightning LoRA 4 步 | 身份五项保持；显存余量（无核显） |
| 4 | 两段式 C | 3 → 1，denoise 0.25 / 0.35 / 0.45 | 画风统一 vs 身份损失 |
| 5 | WD tagger v3 + ToonOut | CPU 与 GPU 各跑一次 | 质检耗时是否可接受 |
| 6 | 角色 LoRA 训练（SDXL） | 20–40 张、默认参数 | 16 GB 能否完成、耗时 |

- 环境前提：PyTorch CUDA 12.8+ 构建；ComfyUI 与 GGUF 插件为最新版；记录驱动、CUDA、PyTorch 版本写入 manifest。

---

## 全部核查后的许可总表（可直接用于第 9.1 节）

| 组件 | 许可 | 默认启用 | 随仓库分发 |
|-|-|-|-|
| Animagine XL 4.0 | CreativeML Open RAIL++-M（可商用，有用途限制） | 是 | 否（脚本下载） |
| Neta Lumina 主模型 | Apache-2.0 | 是 | 否 |
| ├ Gemma-2B 文本编码器 | Gemma Terms of Use | 随 Neta Lumina | 否（下载时展示条款） |
| └ FLUX VAE | Apache-2.0 | 随 Neta Lumina | 否 |
| Qwen-Image-Edit-2511（含量化版） | Apache-2.0 | 是 | 否 |
| WD EVA02 tagger v3 | Apache-2.0 | 是 | 否 |
| ToonOut / BiRefNet | MIT | 是 | 否 |
| Illustrious-XL | 各版本不一致 | 否（逐版本确认后可选） | 否 |
| NoobAI-XL | fair-ai + 禁止商用（含生成物） | 否（可选，非商用） | 否 |
| InsightFace 模型（InstantID / FaceID） | 非商用研究 | 否 | 否 |
| deepghs yolo-face | RDL 免责声明，上游许可待逐个追溯 | 否（可选插件） | 否 |
| Aesthetic Predictor 2.5 | AGPL-3.0 | 否（可选插件） | 否 |
| NovelAI API | 生成物归用户；账号不得共享、须限速 | 否（用户自带 key） | — |
| OpenAI / Gemini API | 生成物归用户；Gemini 有年龄与免费档数据条款 | 否（用户自带 key） | — |
| Seedream API | **条款待核实** | 否 | — |

## 仍未闭环

1. Seedream 官方服务协议（需登录火山方舟控制台查看）。
2. deepghs yolo-face 各模型的上游许可（YOLOv10 / YOLOv11）。
3. FLUX VAE 许可建议对照 BFL 官方 schnell 仓库再确认一次。
4. 5060 Ti 实测（见第 12 项矩阵）。
