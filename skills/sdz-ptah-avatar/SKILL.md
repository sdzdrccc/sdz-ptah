---
name: sdz-ptah-avatar
description: Generate anime avatars from a brief via prompt compiling, batch generation, crop compensation, scoring, hard gates and PNG or JPG export at 1K/2K/4K. Use for avatar requests including Ptah 普塔 avatar. Not for scene art or real-person photos.
---

# SDZ Ptah Avatar（普塔头像）

把一份「头像需求」执行成可交付的候选图。本 skill 只承担执行链：
需求 → 提示词编译 → 批量出图 → 裁切补偿 → 裁切后评分与硬门槛 → 多格式导出。

本 skill 自包含，可独立运行：协议、阈值、脚本、原因码全部在
`references/protocol.md` 与 `scripts/avatar_pipeline.py` 内，不依赖任何项目目录。
`ai-anime-avatar/outputs/AI动漫头像生成方案.md` 只是可选外链（补充设计依据），
不存在或路径变化都不影响执行。

## 硬约束（先读，违反则结果不可信）

- 生成端只有 `GenerateImage` 一个入口：入参只有 `prompt` 与 `image_size`。
  **没有 seed、负向词、参考图、批量参数**，同一 prompt 两次结果不同。
- 原生输出 1920×1920 JPEG。构图系统性偏下（实测眼睛在 0.40–0.45），
  **不要试图用提示词修构图**，一律交给裁切补偿。
- 每个需求的调用预算 ≤ 12 次（首轮 8 次 + 二轮补量 ≤ 4 次）。
- 2K/4K 只能靠放大，放大不新增细节，必须在交付里写明。
- 缺少 OCR / NSFW / 情绪 / 风格 / 解剖 / 超分模型时，对应闸门记为 `null`，
  不得当成「通过」，也不得口头补分。
- **每单必须落盘**：订单目录里必须有 `brief.json`、`prompts.json`、`status.json`、
  `raw/`、`out/`；`%TEMP%` 只作生成中转，生成后立即搬进 `raw/`。
  中断或失败也要保留已生成图与状态；脚本不得自动删除，清理只能由用户决定。

## 订单落盘（硬性要求，先做）

每单开一个持久目录，路径固定为：

```text
<订单根目录>/<YYYYMMDD-HHMMSS>-<slug>/
├── brief.json      # 需求
├── prompts.json    # 4 条编译后的提示词与实际使用的变体
├── status.json     # running / completed / failed / interrupted
├── raw/            # 原始生成图（.jpg），不得缺失
└── out/            # 裁切、导出、contact_sheet.jpg、manifest.json、report.md
```

- 订单根目录：用户指定优先；未指定用 `<当前工作区>/avatar-orders/`。
- `%TEMP%`、`/tmp` 只能做生成中转：生成后立刻 `Move` 到 `raw/`，再跑评分。
- 中断或失败：把已生成图留在 `raw/`，`status.json` 写 `interrupted` / `failed`
  并记录 `phase`、`raw_count`、`last_error`；不得回滚或清理。
- `avatar_pipeline.py` 只写 `out/` 下的产物，不会删除订单目录或 `raw/`；
  清理订单只能由用户明确要求后执行。

`status.json` 最小字段：

```json
{
  "order_id": "20261122-153000-xiaozhi",
  "state": "running",
  "phase": "generate",
  "created_at": "2026-11-22 15:30:00",
  "updated_at": "2026-11-22 15:31:20",
  "raw_count": 0,
  "export_count": 0,
  "last_error": null
}
```

## 执行步骤

### 1. 编译需求（不调用生成工具）

把用户输入整理成 `<订单目录>/brief.json`，字段见 `references/protocol.md` 第 1 节。
必需字段：`character`、`temperament`、`usage`、`size`、`formats`；
明确侧脸时加 `"view": "profile"`，它决定第 2 步要不要带单眼条款。

### 2. 编译提示词变体（4 个）

按 `references/protocol.md` 第 2 节的公式写 4 条 prompt，分别改变：
气质措辞、取景松紧、光影、背景。每条以 `[PURPOSE]: ` 开头，
写清「头像用途 + 人物 + 表情 + 光影 + 背景 + 正面胸像或侧脸胸像 + 构图」。
禁止写「精确仰角」「精确配饰数量」这类生成端做不到的约束。
4 条 prompt 与实际使用的变体一并写入 `<订单目录>/prompts.json`。
`view=profile` 时，每条都要加第 2.4 节的单眼条款（只出现一只眼/一只耳，
鼻梁另一侧不画第二只眼、眼白或眉毛）。

### 3. 批量出图（首轮 8 次）

对 4 个变体各调 2 次 `GenerateImage`：

- `image_size`: `square`
- `path`: 绝对路径、**不带扩展名**，先落 `<临时中转目录>/v1_r1` … `v4_r2`
  （实际产物是 `.jpg`）
- 每张生成成功后**立即** `Move` 到 `<订单目录>/raw/v1_r1.jpg` …，
  不允许把 raw 留在 `%TEMP%` 就结束；每搬一张更新一次 `status.json` 的 `raw_count`

生成前先 `ToolSearch` 取 `GenerateImage` 的 schema，再用 `DeferExecuteTool` 调用。
每次调用后核对文件确实存在且可打开；打不开就记为失败，不要继续往下算分。

`view=profile` 时额外两条：

- 4 个变体里至少 2 个把「远眼不可见」写成不同措辞，不要重抽同一句；
- 出图后先放大到 800px 看鼻梁后方，出现第二只眼/眼白直接弃用，
  不进入评分（这一步在纯 Pillow 评分之前做，避免把四分之三脸当侧脸交付）。

### 4. 跑裁切与评分

```powershell
$skill = Join-Path $env:USERPROFILE ".trae-cn\skills\sdz-ptah-avatar"
py -3 "$skill\scripts\avatar_pipeline.py" `
  --raw "<订单目录>\raw" --out "<订单目录>\out" `
  --brief "<订单目录>\brief.json" --formats 1024,2k --jpg
```

`--formats` 取 `1024` / `2k` / `4k` 的逗号组合；`--jpg` 额外导出压缩版，
`--jpg-quality` 默认 88。脚本会写出：

- `out/crop/*_1024.png` 主图（默认交付格式）
- `out/native/*_native_<px>.png` 原生分辨率裁切（归档）
- `out/export/png/<tier>/*.png` 2K/4K 放大版
- `out/export/jpg/*_q<q>.jpg` 压缩版
- `out/preview/*_circle.png` 圆形预览
- `out/contact_sheet.jpg`、`out/manifest.json`、`out/report.md`

### 5. 判定与补量

- `manifest.json` 的 `verdict` 为 `PASS` 的才是候选；`reasons` 是驳回原因码。
- 若 PASS 数 < 1 且预算未用完：保留最高分方向，再出 ≤4 张（二轮补量），
  重跑脚本；总量仍 ≤12 次。同一变体连续 3 次卡在 `edge_margin` 就换变体。
- 达到预算仍无 PASS：如实报告「本轮无达标候选」，给出最高分与原因分布，
  不要放宽阈值凑数。
- 每轮评分后更新 `status.json`：`phase=score/export`、`export_count`、
  `manifest` 路径；脚本或人工中断时写 `state=interrupted`，保留所有已有文件。

### 6. 汇报

给出：候选清单（文件名、总分、`face_ratio`、`eye_center_y`、`edge_margin`、
`sharpness`、定位方式、结论、原因码）、contact sheet 路径、导出档位与文件路径、
订单目录绝对路径（`raw/`、`out/manifest.json`、`out/report.md` 必须在其中）、
以及放大与不可用能力说明。原因码释义见 `references/protocol.md` 第 6 节。
结束时把 `status.json` 写成 `state=completed`、`phase=done`。

## 输出格式速查

| 需求 | 参数 | 产物目录 |
|-|-|-|
| 默认（站点/社区头像） | `--formats 1024` | `crop/*_1024.png` |
| 高清展示 | `--formats 1024,2k`（或 `4k`） | `export/png/2k/`、`export/png/4k/` |
| 有体积限制的网页/App | 追加 `--jpg --jpg-quality 88` | `export/jpg/*_q88.jpg` |

放大手段、速度与画质代价、放大后复检项，见 `references/protocol.md` 第 7 节。

## 不可用能力（本机核实）

- 本机无 numpy / opencv / onnx / 超分模型，`pip` 无网络。
  因此人脸定位与评分均为纯 Pillow 启发式，`Real-ESRGAN` 等超分**不可用**，
  2K/4K 只能用 Pillow Lanczos 插值。
- OCR（文字）、NSFW、情绪分类、风格 embedding、解剖检查均**不可用**，
  对应闸门为 `null`，总分只在「面部质量 + 构图」两维上归一化。

## 边界

- 只做头像执行链；不写方案文档、不做写实真人、不做视频。
- 阈值、权重、原因码的权威来源是本 skill 内的 `references/protocol.md`；
  可选外链 `ai-anime-avatar/outputs/AI动漫头像生成方案.md` 仅补充设计说明。
  两者冲突且方案文档可访问时以方案文档为准，并同步修正 protocol.md。
- 源码唯一真相在仓库 `skills/sdz-ptah-avatar/`；装到 TRAE 的全局技能目录用
  `python skills/install.py`（Windows 下 `python` 报 9009 时改用 `py -3 skills/install.py`；
  默认符号链接，无权限时自动回退复制）；装到其他宿主用 `--target <其技能目录>`。
  不要直接改已安装副本。
