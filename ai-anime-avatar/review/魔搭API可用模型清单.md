# 魔搭（ModelScope）官方 API 可用模型清单

| 项目   | 内容                                                    |
| ---- | ----------------------------------------------------- |
| 核实日期 | 2026-11-24                                            |
| 核实方式 | 官方 API 端点实调 + 官方模型库筛选页无头浏览器渲染 + 模型页代码范例               |
| 用途   | 本项目 `backends/modelscope.py` 选型的外部事实依据；**不修改任何后端注册表** |
| 时效声明 | 官方明确「清单持续迭代、旧模型可能下架」。本文件是当日快照，用前按[第 6 节](#6-如何自己复核)复核 |
| 说明   | 许可、条款以官方页面为准；**不构成法律意见**                              |

---

## 0. 一句话结论

魔搭的「推理 API（API-Inference）」不是一套接口，而是**两套互不通用**的接口，各有各的模型清单：

| 接口              | 地址                                                   | 干什么          | 模型数（当日）              |
| --------------- | ---------------------------------------------------- | ------------ | -------------------- |
| 对话接口（OpenAI 兼容） | `GET https://api-inference.modelscope.cn/v1/models`  | 文字聊天、看图说话    | **38**               |
| 生图接口（异步任务）      | `POST /v1/images/generations` + `GET /v1/tasks/{id}` | 文生图、图生图、图像编辑 | 文生图 **≥3000**、图生图 **231**（含社区微调） |

**对本项目的直接影响**：生图模型**大多不在**对话接口的 38 个清单里。本项目适配器 `probe()` 用对话清单去核对生图模型，会误报「模型不在公开列表中」——这不是故障，是判据用错了清单。详见[第 5 节](#5-对本项目的意义与一处判据缺陷)。

---

## 1. 对话接口清单（38 个，官方端点实测）

> 这批模型走对话接口（`/v1/chat/completions`），**不能**直接塞进生图接口。
> 数据来源：`curl https://api-inference.modelscope.cn/v1/models`，返回 `object: list`、`owned_by: system`，共 38 条。

| 机构                     | 模型 ID                                                                                                              |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------ |
| deepseek-ai            | `DeepSeek-V4-Flash-0731`、`DeepSeek-V4-Pro`、`DeepSeek-V4-Pro-0813`、`DeepSeek-V4.1-Flash`、`DeepSeek-V4.1-Flash-1017` |
| Qwen（文本）               | `Qwen3.5-122B-A10B`、`Qwen3.5-27B`、`Qwen3.5-35B-A3B`、`Qwen3.5-397B-A17B`、`Qwen3.8-27B`、`Qwen3.8-Flash-Next`         |
| MiniMax                | `MiniMax-M1-80k`、`MiniMax-M3`                                                                                      |
| stepfun-ai             | `Step-3.5-Flash`、`Step-3.7-Flash`、`Step-5-Preview`                                                                 |
| Shanghai_AI_Laboratory | `Intern-S1`、`Intern-S1-mini`、`Intern-S2-Preview`                                                                   |
| PaddlePaddle           | `ERNIE-4.5-0.3B-PT`、`ERNIE-4.5-21B-A3B-PT`、`ERNIE-4.5-300B-A47B-PT`、`ERNIE-4.5-VL-28B-A3B-PT`                      |
| ZhipuAI                | `GLM-4.7-Flash`、`GLM-5.2`                                                                                          |
| nex-agi                | `Nex-N2.5-mini`、`Nex-N2.5-Pro`                                                                                     |
| moonshotai             | `Kimi-K3.1-Pro`                                                                                                    |
| mistralai              | `Mistral-Large-Instruct-2407`                                                                                      |
| meituan-longcat        | `LongCat-Flash-Lite`                                                                                               |
| OpenGVLab              | `InternVL3_5-241B-A28B`                                                                                            |
| MedAIBase              | `AntAngelMed`                                                                                                      |
| early-access           | `EA-29B-A4B`                                                                                                       |
| opencompass            | `CompassJudger-1-32B-Instruct`                                                                                     |
| XGenerationLab         | `XiYanSQL-QwenCoder-32B-2412`、`XiYanSQL-QwenCoder-32B-2504`                                                        |
| **图像编辑（混入了对话清单）**      | `Qwen/Qwen-Image-Edit`、`MusePublic/Qwen-Image-Edit`                                                                |

**注意最后一行**：两个图像编辑模型也出现在对话清单里。所以「在 `/v1/models` 里」既不能证明某模型能生图，也不能证明某模型不能生图——这个清单只能用于对话接口。

---

## 2. 生图接口清单（完整抓取）

> **数据来源**：官方指定的完整 API 列表入口，按任务类型筛选：
> - 文生图：`modelscope.cn/models?filter=inference_type&tabKey=task&tasks=Text-to-Image-Synthesis`
> - 图生图：`modelscope.cn/models?filter=inference_type&tabKey=task&tasks=Image-to-Image`
>
> **抓取方式**：Edge 无头浏览器逐页渲染 `--dump-dom`，提取模型链接并去重。
> 核实日期 2026-11-24。**注意**：官方声明清单持续增删，本报告是当日快照。

### 2.1 规模与口径

| 类别 | 页数 | 模型数 | 是否到底 |
|-|-|-|-|
| 文生图（Text-to-Image-Synthesis） | 抓取 1–100 页 | **≥3000**（前 35 页已去重 1050 个） | **否**：第 100 页仍满 30 个 |
| 图生图（Image-to-Image） | 1–8 页 | **231** | **是**：第 9 页为空 |

> 文生图类含有大量社区 LoRA / 微调（如 `Q491791643`、`asd547877752`、`yun0572` 等作者各几十款），
> 逐条列在正文没有意义。正文完整列出**官方 / 机构模型**，全量 1281 条（文生图 1050 + 图生图 231）
> 见同目录附件 `魔搭API生图模型全量清单.csv`（UTF-8 BOM，Excel 可直接打开）。
> 图生图类因总量可控（231），把机构 / 社区变体也一并列出。

### 2.2 本项目重点关注的生图模型

| 模型 ID | 机构 | 能力 | 许可 | 魔粒/次 |
|-|-|-|-|-|
| `Qwen/Qwen-Image-Edit-2511` | 千问 | 图像编辑，多参考图，身份一致性增强 | Apache-2.0 | **2**（模型页实测） |
| `Qwen/Qwen-Image-Edit` | 千问 | 图像编辑（项目现用后端指向它） | 以模型页为准 | 页面未标注 |
| `Qwen/Qwen-Image-2.1` | 千问 | 图生图 / 编辑，最多 16 参考图 | Qwen 研究许可 | **1**（模型页） |
| `Qwen/Qwen-Image-2.1-Turbo` | 千问 | 文生图加速版 | Qwen 研究许可 | 页面未标注 |
| `Qwen/Qwen-Image-2512` | 千问 | 文生图 | 以模型页为准 | 页面未标注 |
| `MiniMax/MiniMax-H3-Image` | MiniMax | 文生图 / 编辑，最多 16 参考图 | 社区许可协议 | **2**（模型页） |
| `black-forest-labs/FLUX.2-klein-9B` | BFL | 图生图 / 编辑 | 以模型页为准 | 页面未标注 |
| `Tongyi-MAI/Z-Image-Turbo`、`Tongyi-MAI/Z-Image` | 通义 | 文生图 | 以模型页为准 | 页面未标注 |
| `krea/Krea-2-Turbo` | Krea | 文生图 | 以模型页为准 | 页面未标注 |

> 两个后端 `modelscope-qwen-edit` / `modelscope-qwen-edit-2511` 都指向上表前两行。
> 魔搭页面同时存在 `MusePublic/Qwen-Image-Edit`（镜像）与 `Qwen/Qwen-Image-Edit`（官方），调用前确认用哪个。

### 2.3 机构 / 官方模型完整列表

#### 文生图 · 官方 / 机构模型（84 个，完整）

| 机构 | 模型（省略机构前缀） |
|-|-|
| AI-ModelScope | `Qwen-Image-Gufeng-LoRA`、`pixel_art_style_lora_z_image_turbo` |
| ChenkinNoob | `ChenkinNoob-XL-V0.1`、`ChenkinNoob-XL-V0.5` |
| DiffSynth-Studio | `ArtAug-lora-FLUX.1dev-v1`、`FLUX.1-Kontext-dev-lora-ArtAug`、`FLUX.1-Kontext-dev-lora-SuperOutpainting`、`FLUX.1-Kontext-dev-lora-highresfix`、`MOSS-Image-SFT-TurboLoRA`、`Qwen-Image-2.1-LayerExtract`、`Qwen-Image-2.1-LayerRemove`、`Qwen-Image-Edit-2511-ICEdit-LoRA`、`Qwen-Image-Edit-F2P`、`Qwen-Image-LoRA-ArtAug-v1` |
| HingXuan | `WAI-illustrious-SDXL-v17` |
| LaxharLAB | `NoobAI-XL` |
| MAILAND | `majicflus_v1` |
| MiniMax | `MiniMax-H3-Image` |
| ModelE | `Illustrious-XL` |
| MusePublic | `100_lora_SD_XL`、`14_ckpt_SD_1_5`、`14_ckpt_SD_XL`、`16_ckpt_SD_XL`、`326_ckpt_SD_XL`、`36_VAE_SD_1_5`、`37_VAE_SD_1_5`、`42_ckpt_SD_XL`、`46_ckpt_SD_XL`、`489_ckpt_FLUX_1`、`51_ckpt_SD_1_5`、`56_ckpt_SD_1_5`、`60_ckpt_SD_1_5`、`63_ckpt_SD_1_5`、`Animagin_XL_V4`、`Detail_Tweaker_LoRA_xijietiaozheng_LoRA_SD_1_5`、`DreamShaper_SD_1_5`、`FLUX.1-dev-fp8-dit`、`Juggernaut_XL_SD_XL`、`Pony_Diffusion_V6_XL_SD_XL`、`Qwen-image`、`Qwen-image-fp8`、`Realistic_Vision_V6.0_B1`、`Realistic_Vision_V6.0_B1_SD_1_5`、`majicMIX_realistic`、`majicMIX_realistic_maijuxieshi_SD_1_5`、`stable-diffusion-3-medium`、`stable-diffusion-xl-base` |
| QWQ114514123 | `WAI-illustrious-SDXL`、`WAI-illustrious-SDXL-v14`、`WAI-illustrious-SDXL-v16`、`WAI-illustrious-SDXLv17` |
| Qwen | `Qwen-Image`、`Qwen-Image-2.1-Turbo`、`Qwen-Image-2512` |
| SecretDongGe | `StarFace-Qwen-Image-2512` |
| Tongyi-MAI | `Z-Image`、`Z-Image-Turbo` |
| Viggle | `Qwen-Image-2.1-viggle-turbo` |
| Wuli-Art | `Qwen-Image-2512-Turbo-LoRA`、`Qwen-Image-2512-Turbo-LoRA-2-Steps` |
| axuanxiaov | `aligadai-moss-v1`、`onemore` |
| black-forest-labs | `FLUX.1-Krea-dev` |
| briaai | `Fibo-2.0-dev` |
| krea | `Krea-2-LoRA-darkbrush`、`Krea-2-LoRA-kidsdrawing`、`Krea-2-LoRA-neondrip`、`Krea-2-LoRA-rainywindow`、`Krea-2-LoRA-retroanime`、`Krea-2-LoRA-softwatercolor`、`Krea-2-LoRA-sunsetblur`、`Krea-2-LoRA-vintagetarot`、`Krea-2-Turbo` |
| lightx2v | `Qwen-Image-2512-Lightning`、`Qwen-Image-Lightning` |
| lodestones | `MOSS-Image-SFT-8-steps-dmd` |
| mibei0804 | `MiniMax-H3-Image-Songyu-LoRA` |
| modelsy | `Qwen-Image-2.1-bf16`、`Z-Image-Turbo-fp16` |
| muse | `Wan2.1-T2V-1.3B-FP8`、`sd35_large` |
| openmoss | `MOSS-Image-SFT` |
| sora60 | `GPT-Image-2` |
| valiantcat | `Qwen-Image-Edit-Cosplay` |

#### 图生图 · 核心底座模型（15 个，完整）

| 机构 | 模型（省略机构前缀） |
|-|-|
| FireRedTeam | `FireRed-Image-Edit-1.1` |
| MusePublic | `FLUX.1-Kontext-Dev`、`Qwen-Image-Edit` |
| Qwen | `Qwen-Image-2.1`、`Qwen-Image-Edit`、`Qwen-Image-Edit-2509`、`Qwen-Image-Edit-2511` |
| Viggle | `Viggle-Image-Swap` |
| black-forest-labs | `FLUX.2-dev`、`FLUX.2-klein-4B`、`FLUX.2-klein-9B`、`FLUX.2-klein-base-4B`、`FLUX.2-klein-base-9B` |
| briaai | `Fibo-2.0-edit-turbo` |
| lightx2v | `Qwen-Image-Edit-2511-Lightning` |

#### 图生图 · 其他机构 / 社区变体（118 个，完整）

| 机构 | 模型（省略机构前缀） |
|-|-|
| AI-ModelScope | `FLUX.2-klein-9B-manga-colorization-by-reference-LORA`、`FLUX.2-klein-AC-Style-LORA`、`Fluffy-kontext-LoRA`、`Flux-Kontext-InScene`、`Glittering-Portrait-Kontext-Dev-Lora`、`Kontext-Doll-LoRA`、`Kontext-Emoji-LoRA`、`Kontext-Labubu-LoRA`、`Kontext-Mythical-LoRA`、`Kontext-Remover-General-LoRA`、`Kontext-Sculptor-LoRA`、`Kontext-Tim-Burton-LoRA`、`Overlay-Kontext-Dev-LoRA`、`Qwen-Edit-2509-Upscale-LoRA`、`Qwen-Edit-3DChibi-LoRA`、`Qwen-Image-Edit-2509-Passionate-kiss`、`Qwen-Image-Edit-2509-Upscale2K`、`Qwen-Image-Edit-2511-Trendy_figurines`、`Qwen-Image-Edit-2511-Upscale2K`、`Qwen-Image-Edit-Giant-Figurine`、`Qwen-Image-Edit-Remover-General-LoRA`、`Qwen-Image-Edit-Skip-Sora`、`QwenEdit2509-FlatLogColor`、`Tattoo-Kontext-Dev-Lora`、`Try_On_Qwen_Edit_Lora_Alpha`、`face-swap`、`flux-2-klein-4b-spritesheet-lora`、`flux2-klein-base-9b-distill-lora`、`next-scene-qwen-image-lora-2509`、`qwen-edit-skin`、`refcontrol-FLUX.2-klein-4B-reference-depth-lora`、`refcontrol-FLUX.2-klein-9B-reference-canny-lora`、`refcontrol-FLUX.2-klein-9B-reference-depth-lora`、`refcontrol-FLUX.2-klein-9B-reference-lineart-lora`、`refcontrol-FLUX.2-klein-9B-reference-normal-lora`、`refcontrol-FLUX.2-klein-9B-reference-pose-lora`、`refcontrol-flux-kontext-reference-canny-lora`、`refcontrol-flux-kontext-reference-pose-lora` |
| fal | `Qwen-Image-Edit-2511-Multiple-Angles-LoRA`、`flux-2-klein-4B-background-remove-lora`、`flux-2-klein-4B-object-remove-lora`、`flux-2-klein-4B-outpaint-lora`、`flux-2-klein-4B-zoom-lora`、`flux-2-klein-4b-spritesheet-lora`、`virtual-tryoff-lora` |
| prithivMLmods | `FLUX.1-Kontext-Cinematic-Relighting`、`FLUX.1-Kontext-Dev-Ultra-Realistic-Portrait`、`FLUX.2-Klein-Dotted-Illustration`、`FLUX.2-Klein-Object-Remover-Bbox`、`Flux2-Klein-Cinematic-FlatLog-Control`、`Kontext-0811-exp`、`Kontext-Bottom-Up-View`、`Kontext-CAM-Left-View`、`Kontext-CAM-Right-View`、`Kontext-Top-Down-View`、`Kontext-Unblur-Upscale`、`Kontext-Watermark-Remover`、`LZO-1-Preview`、`Monochrome-Pencil`、`Natural-Exposure-Fibo-2.0-dev`、`Outfit-Cut-Specified`、`Photo-Restore-i2i`、`PhotoCleanser-i2i`、`Polaroid-Warm-i2i`、`QIE-2509-Dotted-Illustration`、`QIE-2509-Object-Mover-Bbox`、`QIE-2509-Object-Remover-Bbox`、`QIE-2509-Object-Remover-Bbox-v3`、`QIE-2511-3D-Paper-Cut-Illustration`、`QIE-2511-BW2TrueColor`、`QIE-2511-Cinematic-FlatLog-Control`、`QIE-2511-Extract-Outfit`、`QIE-2511-FlatLog2TrueColor`、`QIE-2511-Object-Remover-Bbox`、`QIE-2511-Object-Remover-v2`、`QIE-2511-Outfit-Design-Layout`、`QIE-2511-Studio-DeLight`、`QIE-2511-Zoom-Master`、`QIE-FRIE1.1-BBox-Object-Remover-v4`、`QIE-FRIE1.1-Deep-Grainy-Polaroid`、`QIE-FRIE1.1-Neutral-Exposure`、`QIE-FRIE1.1-Object-Mover`、`Qwen-Image-2.1-Amethyst-Theme`、`Qwen-Image-2.1-Clay-Sculpture-Style`、`Qwen-Image-2.1-Heatwave`、`Qwen-Image-2.1-Natural-Exposure-LoRA`、`Qwen-Image-2.1-Object-Mover-Bbox-Preview`、`Qwen-Image-2.1-Object-Mover-Bbox-turbo`、`Qwen-Image-2.1-Object-Remover-Bbox-Preview`、`Qwen-Image-2.1-Object-Remover-Bbox-turbo`、`Qwen-Image-2.1-Outrun`、`Qwen-Image-2.1-Voxel-Style`、`Qwen-Image-Edit-2511-Anime`、`Qwen-Image-Edit-2511-Hyper-Realistic-Portrait`、`Qwen-Image-Edit-2511-Midnight-Noir-Eyes-Spotlight`、`Qwen-Image-Edit-2511-Noir-Comic-Book-Panel`、`Qwen-Image-Edit-2511-Object-Adder`、`Qwen-Image-Edit-2511-Object-Remover`、`Qwen-Image-Edit-2511-Pixar-Inspired-3D`、`Qwen-Image-Edit-2511-Polaroid-Photo`、`Qwen-Image-Edit-2511-Ultra-Realistic-Portrait`、`Qwen-Image-Edit-2511-Unblur-Upscale`、`Yarn-Photo-i2i` |
| valiantcat | `CouplesOnTitanic`、`FLUX.2-klein-AC-Style-LORA`、`Kontext-Cosplay-Lora`、`Kontext-Doll-LoRA`、`Kontext-Mythical-LoRA`、`Kontext-Sculptor-LoRA`、`Kontext-Tim-Burton-LoRA`、`Qwen-Image-Edit-2509-Passionate-kiss`、`Qwen-Image-Edit-2509-Upscale2K`、`Qwen-Image-Edit-2509-photous`、`Qwen-Image-Edit-2511-Upscale2K`、`Qwen-Image-Edit-ARStandonScreen`、`Qwen-Image-Edit-Giant-Figurine`、`Qwen-Image-Edit-Remover-General-LoRA`、`Qwen-Image-Edit-Skip-Sora` |

---

## 3. 生图接口调用协议（已从官方代码范例核对）

异步任务式，两步走（本项目 `backends/modelscope.py` 已按此实现）：

1. **提交**：`POST https://api-inference.modelscope.cn/v1/images/generations`
   - 头：`Authorization: Bearer <token>`、`X-ModelScope-Async-Mode: true`
   - 体：`model`（必填，模型 ID）、`prompt`、`image_url`（参考图，可多张）、`negative_prompt`、`size`、`seed`、`steps`、`guidance`、`loras`（最多 6 个，权重和须为 1.0）
   - 返回：`{"task_id": "..."}`
2. **轮询**：`GET /v1/tasks/{task_id}`，头 `X-ModelScope-Task-Type: image_generation`
   - 返回 `task_status`（`PENDING` / `RUNNING` / `SUCCEED` / `FAILED`）与 `output_images[]`

模型页代码范例确认支持**多图编辑**：`image_url` 可传多张图片 URL，配合中文指令做融合 / 换装 / 多视角。官方示例用的图床是 `modelscope.oss-cn-beijing.aliyuncs.com`。本项目改用 base64 直接内嵌（保护参考图不外泄到第三方图床），服务是否接受仍需首次真实调用验证（见 `modelscope-runbook.md` 第 6 节）。

---

## 4. 计费与使用限制（官方规则）

### 4.1 魔粒（平台内额度点）扣减

官方 `API-Inference` 分三档，按模型算力规模计：

| 档位             | 扣减                                                                               |
| -------------- | -------------------------------------------------------------------------------- |
| 轻量模型           | 平均 0.5 魔粒/次                                                                      |
| 主流模型           | 平均 1 魔粒/次                                                                        |
| 旗舰模型           | 平均 2 魔粒/次                                                                        |
| 图像 / 视频 / 音乐生成 | **以模型页实际标注为准**（如 Qwen-Image-2.1 为 1、Qwen-Image-Edit-2511 与 MiniMax-H3-Image 为 2） |

- 系统原因导致的推理失败**全额返还**；用户主动取消的推理任务也返还（训练任务不返还）。
- 魔粒有有效期：短期 24 小时、长期 90 天，消耗时优先扣临近过期的。

### 4.2 免费额度的硬约束

- **必须** ModelScope 账号绑定阿里云账号并通过实名认证，否则 401/403。
- 免费推理 API 总量上限：每日累计约 2000 次；单模型动态限流（第三方汇总称最高 500 次），并发按平台压力动态调整，目标是保障单并发。
- 官方明示：**API-Inference 为非商业化、非盈利产品**，请勿用于需要高并发与 SLA 保障的线上任务；商用应使用各商业平台 API。
- 早期模型可能逐渐下架；清单随社区关注度（点赞、下载）持续迭代。
- 如需高并发或大额调用，可通过 API-Provider 绑定外部 API 提供方。

---

## 5. 对本项目的意义与一处判据缺陷

| 后端登记 id                     | 指向模型                        | 在对话 38 清单里？ | 在生图筛选页？                                     | 结论                       |
| --------------------------- | --------------------------- | ----------- | ------------------------------------------- | ------------------------ |
| `modelscope-qwen-edit`      | `Qwen/Qwen-Image-Edit`      | **是**       | 抓取的两页未见（页面为 `Qwen-Image-Edit-2511` / `2.1`） | 生图能否调用**不由对话清单决定**，需真实调用 |
| `modelscope-qwen-edit-2511` | `Qwen/Qwen-Image-Edit-2511` | **否**       | **是**                                       | `probe()` 会误报「模型不在公开列表中」 |
| `modelscope-qwen-image-2.1`（2026-11-24 登记） | `Qwen/Qwen-Image-2.1`  | **否**       | **是**                                       | 同上；已在方案文档 C.5 有实测记录      |
| `modelscope-minimax-h3-image`（2026-11-24 登记） | `MiniMax/MiniMax-H3-Image` | **否** | **是**                                       | 同上                       |

**判据缺陷**：`backends/modelscope.py` 的 `probe()` 用 `GET /v1/models` 的返回判断模型是否在公开列表。该接口是**对话接口**的模型清单，生图模型（除两个图像编辑模型混入外）本就不在其中。因此：

- `probe()` 对 `modelscope-qwen-edit-2511` 输出 warning「模型不在公开列表中（列表不完整，需实际调用确认）」属于**假阴性**，不能据此判定后端不可用。
- 代码注释已写明「列表不全，仅提示」，实际行为与注释一致——**这是已知限制，不是 Bug**；但执行手册的使用者容易把这条 warning 当成故障。
- 正确判据只有一条：**真实调用一次生图接口**（`generate.py` 正式出图）。`probe()` 只回答「服务可达 + token 已设置」，不回答「生图模型可用」。

**2026-11-24 已处理**：`Qwen-Image-2.1` 与 `MiniMax-H3-Image` 已登记为后端 `modelscope-qwen-image-2.1` / `modelscope-minimax-h3-image`，并在 `modelScope-runbook.md` 新增 6.1 节说明该 warning 的含义。四个 ModelScope 后端仍全部标 `verified=false`，**真实调用验证仍是 M1 / M4 的待办**。

**适配层配套改动**（同日）：`backends/modelscope.py` 新增 `steps_param` / `guidance_param` 开关，且负向词改为读 `capabilities.negative_prompt`——因为 MiniMax-H3-Image 传 `steps` / `guidance` / `negative_prompt` 会 HTTP 400。默认开关保持 true，既有后端行为不变；已用 mock 验证 MiniMax 请求体只剩 `model` / `prompt` / `seed` / `image_url`。

---

## 6. 如何自己复核

```bash
# ① 对话类 38 个清单（无需 token、不消耗额度）
curl -s -m 40 'https://api-inference.modelscope.cn/v1/models' | \
  python -c "import sys,json; d=json.load(sys.stdin); print(len(d['data'])); [print(x['id']) for x in d['data']]"
```

```bash
# ② 生图类：用 Edge 无头浏览器按任务类型逐页渲染（Git Bash 下执行）
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
for task in Text-to-Image-Synthesis Image-to-Image; do
  for p in $(seq 1 10); do
    echo "=== $task p$p ==="
    "$EDGE" --headless=new --disable-gpu --no-sandbox --virtual-time-budget=10000 \
      --dump-dom "https://modelscope.cn/models?filter=inference_type&tabKey=task&tasks=$task&page=$p" 2>/dev/null | \
      grep -oE 'href="/models/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"' | \
      sed 's/href="\/models\///;s/"//' | sort -u
  done
done
```

> 注：`tasks=` 参数是关键（不传会混进对话模型）。每页固定 30 个；**某页输出为空即到底**。
> 文生图类当日抓到第 100 页仍满，图生图第 9 页为空（231 个到底）。`--virtual-time-budget` 给 10000ms 即可。

```powershell
# ③ 单个模型的魔粒扣减与调用范例：打开模型页，看「API-Inference 调用消耗」与「查看代码范例」
# 例：https://modelscope.cn/models/Qwen/Qwen-Image-Edit-2511
```

---

## 7. 来源

- 模型库筛选页（官方指定 API 列表入口）：<https://modelscope.cn/models?filter=inference_type\&tabKey=task>
- 对话接口清单端点：<https://api-inference.modelscope.cn/v1/models>
- API-Inference 使用限制：<https://modelscope.cn/docs/model-service/API-Inference/limits>
- 魔粒体系说明：<https://modelscope.cn/docs/magicube/intro>
- 平台免费资源清单：<https://modelscope.cn/learn/1409>
- 模型页（代码范例 / 许可 / 魔粒）：`Qwen/Qwen-Image-Edit-2511`、`Qwen/Qwen-Image-2.1`、`MiniMax/MiniMax-H3-Image`
- 第三方生图 MCP 说明（交叉验证异步协议）：<https://www.modelscope.cn/mcp/servers/linsn327/modelscope-image-mcp>

> 本文件只记录外部事实与本项目代码的对照，不修改 `backends.v2.json`、`verified` 字段或方案文档。
