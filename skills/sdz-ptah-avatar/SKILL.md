---
name: sdz-ptah-avatar
description: Generate anime avatars from a style profile + character card + angle matrix via prompt compiling, two-stage confirmation, budgeted generation (default 2 images per round, budget 10 counted across rounds), crop compensation, scoring, style/character/canvas/angle gates and PNG/JPG export at 1K/2K/4K. After delivery, asks whether the user is satisfied and can re-generate a round from selectable improvement directions (style fidelity, composition, face features, outfit, angle, detail). Supports left/right profiles, front, and faceless/armored (helmet/visor) characters with a head-contour metric set; default facing follows gender (male left, female right, unspecified left) and is user-overridable. Reference-image generation is gated by backend capability detection (references/backends.json); unsupported backends fail with exit 4 rather than silently falling back to text2img. Square full-bleed canvas with no border. Use for avatar requests including Ptah 普塔 avatar, Satoshi 小智, and faceless armor suits. Not for scene art or real-person photos.
---

# SDZ Ptah Avatar（普塔头像）

把一份「头像需求」执行成可交付的候选图。本 skill 只承担执行链：
需求 → 三层档案（风格/角色/角度）→ 提示词编译 → 限量出图 →
生成质量评分（原图）→ 取景裁切 → 交付校验（成品）→ 多格式导出。

> **第一原则：质量靠生成，后处理只做交付。**
> 生成图不合适，裁切、放大、调色都救不回来；处理阶段只做取景裁切、几何/截断校验
> 与格式导出，不把任何后处理当作提升观感的手段。原图不合格的正确动作是换提示词/
> 换方向/追加候选重抽，不是调裁切参数或放宽阈值。详见「生成优先」一节。

**三层结构**（新增风格或角色只加文件，不改脚本）：

| 层 | 路径 | 当前内容 |
|-|-|-|
| 风格档案 | `references/styles/<style-id>.json` | `tv-anime-cel`（TV 动画赛璐璐）、`realistic-armor`（写实/皮套） |
| 角色卡 | `references/characters/<character-id>.json` | `satoshi`（小智，faced）、`fox-armor`（无脸皮套，faceless） |
| 角度矩阵 | `references/angles.json` | `profile_left` / `profile_right` / `front` 启用，3/4、仰俯 `reserved` |

本 skill 自包含，可独立运行：协议、阈值、脚本、原因码全部在
`references/protocol.md` 与 `scripts/avatar_pipeline.py` 内，不依赖任何项目目录。
`ai-anime-avatar/outputs/AI动漫头像生成方案.md` 只是可选外链（补充设计依据），
不存在或路径变化都不影响执行。

## 生成优先（决策树，优先于处理侧优化）

1. **处理只做交付**：只保留取景裁切、几何与截断校验、格式导出；不把裁切/放大/调色
   当成提升观感的手段，任何地方都不暗示后处理能改善质量。
2. **评分对象分开**：raw 原图单独出生成质量分（面部/轮廓、构图、风格、角度），
   final 只出交付校验分；两者都写进 manifest。成品过闸不得掩盖原图不合格
   （例如 final PASS 但 raw 已 `subject_cut`）。
3. **原图不达标就重抽**：换提示词、换方向、追加候选；不要调裁切参数，不要放宽阈值。
4. **预算花在命中率**：优先改提示词编译、风格档案、角色卡与候选筛选；默认每轮 2 张，
   命中率低再追加，脚本不自动加预算。
5. **裁切只做取景**：把 1920 原生图变成方形头像构图（保头顶、居中、留白合理），
   不修脸、不补画、不修表情；补偿能力上限就是取景。

## 硬约束（先读，违反则结果不可信）

- 生成端只有 `GenerateImage` 一个入口：入参只有 `prompt` 与 `image_size`。
  **没有 seed、负向词、参考图、批量参数**，同一 prompt 两次结果不同。
  参考图能否参与生成取决于宿主/后端，必须先查 `references/backends.json` 的能力声明；
  当前宿主不支持参考图入参时默认 `fallback=fail`、退出码 4，**不得静默退回文生图**。
- 原生输出 1920×1920 JPEG。构图系统性偏下（实测眼睛在 0.40–0.45），
  **不要试图用提示词修构图**，一律交给裁切补偿。
- **角度范围**：`profile_left`（鼻尖在左、耳朵在右）、`profile_right`（鼻尖在右、
  耳朵在左）与 `front`（正脸）均启用。默认朝向按性别：**男性朝左、女性朝右、
  未指定默认朝左并在确认页提示**；用户可手动覆盖。正脸判据是双眼检出 +
  eye_y 0.24–0.38（正样本校准，不套侧脸 0.32–0.46）+ 眼线倾斜/双眼平衡达标。
  3/4、仰俯仍是 `reserved`，命中即 `angle_mismatch`，不产、不测、不实现。
- **方向判不出时**（瞳孔/鼻尖侧/面罩信号缺失或冲突）→ `angle_undetermined`，
  暂停请人工确认，不拿默认朝向顶替判定结果。
- **无脸/皮套模式（`face_mode=faceless`）**：不用眼部锚点裁切，改用头部/头盔整体
  轮廓；面部类指标（眼位、双侧对称、面部占比）不参与评分，改为头部轮廓占比 +
  上下留白 + 居中偏移，manifest 标 `metric_set=head_contour`、
  `face_metrics_applied=false`。角色卡用轮廓类必填清单（耳鳍/头盔形状、面罩、
  装甲配色、胸口标识等）。写实/皮套不得套赛璐璐阈值，用 `realistic-armor`；
  无档案或声明不适用时记 `null + 原因`，**null 不算 PASS**。
- **两段式 + 确认门（硬性）**：第一段只收参考图/文字/风格，第二段列参数确认
  （默认画质 1K、张数 2、朝向按性别）；**未收到明确确认前一律不调用
  `GenerateImage`**。确认由 `scripts/confirm_order.py` 写入
  `brief.json.confirm.confirmed=true`；带 brief 却未确认时流水线直接退出码 4。
  唯一例外：交付后的满意度回访与改进方向选择不需要再走 `confirmed` 门（见步骤 8）。
- **选择项必须可降级**：能弹 `dynamic-ui` 卡片就弹（点确认后经
  `window.sendPrompt` 回传确认文字，记为 `source=confirmation_card`）；
  弹不出就用编号列表让用户回数字，两条路径语义一致。
- **画布硬约束**：画布与导出一律方形、满出血；禁止边框、圆形遮罩、装饰圈。
  prompt 必须包含 `square canvas, no border, no frame, no circular mask, edge-to-edge`；
  流水线只产出方形预览，不再生成圆形遮罩图。
- **截断判定替代留边阈值**：贴边但主体完整放行；只有贴边连续主体段过宽且向内延续
  才判 `subject_cut`。顶部/左/右为硬门槛，底部胸像裁切只记录。
- **裁切兜底（硬性）**：平涂背景下先算内容最高点，裁切上沿必须留出 6% 安全边距，
  禁止切掉头顶/耳尖/头盔顶；同时约束成品上留白 1.5–16%、下留白 ≤18%、
  主体占比 40–95%、顶部平切比 ≤22%。超限就重裁或驳回。
  内容框不可靠（非平涂底）时记 `null`，不得当成通过。
- **闸门跑在成品上**：截断/留白/占比/平切在最终 1024 成品上重跑，manifest 同时
  记录原图 `raw` 与成品 `final_gate`；输入本身是成品方图时，原图内容闸门也生效。
- **无脸不再直接判死**：`no_face` 先走头部/轮廓回退；回退也失败才记
  `locate_failed`（定位失败，不是构图不合格）。
- **低成本模式是默认**：`--budget` 默认 **10** 张。每张生成都计数并写进
  `status.json.budget` 与 `rounds.json.budget_used`；预算**按当前订单累计**，
  历史订单的图片不累加，到上限就停，必须先问用户是否追加，不许默默抽，
  也不许静默超额。
- 2K/4K 只能靠放大，放大不新增细节，必须在交付里写明。
- 缺少 OCR / NSFW / 情绪 / 风格 / 解剖 / 超分模型时，对应闸门记为 `null`，
  不得当成「通过」，也不得口头补分。
- **每单必须落盘**：订单目录里必须有 `brief.json`、`prompts.json`、`status.json`、
  `rounds.json`、`manifest.json`；`%TEMP%` 只作生成中转，生成后立即搬进订单目录。
  中断或失败也要保留已生成图与状态；脚本不得自动删除，清理只能由用户决定。

## 订单落盘（硬性要求，先做）

**一个角色一个目录**，所有订单共用同一个目录，所有图片平铺，靠文件名区分：

```text
<订单根>/satoshi/             # 目录名 = 角色 slug，不再有 -01/-02
├── brief.json                # 当前订单需求单
├── brief-20261122-100000.json  # 历史订单 brief 归档（新单不覆盖）
├── manifest.json             # 多单累积：items 按 order_id 归属
├── rounds.json               # orders[] + rounds[]，按 order_id 区分
├── status.json
├── prompts.json
├── checklist.json
├── satoshi-avatar-1-2-sheet.jpg   # 第一单联系表（按订单命名，不覆盖）
├── satoshi-avatar-3-4-sheet.jpg   # 第二单联系表
├── satoshi-avatar-1.png      # 1024 成品（无后缀 = 最终交付）
├── satoshi-avatar-1-src.jpg  # 原图
├── satoshi-avatar-1-2k.png   # 仅用户要 2K 时
├── satoshi-avatar-1-4k.png   # 仅用户要 4K 时
├── satoshi-avatar-1-q88.jpg  # 仅用户要 JPG 时
├── satoshi-avatar-2.png
├── satoshi-avatar-2-src.jpg
├── satoshi-avatar-3.png      # 下一单继续用 3、4，不重置
└── satoshi-avatar-3-src.jpg
```

- **一个角色一个目录**：目录名默认取角色 slug（`satoshi/`），同一角色后续所有订单
  都进这个目录；不再递增 `-01`/`-02`，不再新建第二层。
- **目录内完全平铺**：不建 `round01/`、`raw/`、`out/`、`crop/`、`native/`、
  `png/2k/`、`export/` 等任何子目录；规格与格式只靠文件名后缀区分。
- 命名统一 `<前缀>-avatar-<N>`：成品无后缀，原图 `-src`，规格 `-2k` / `-4k` / `-q<q>`。
- **编号跨订单连续**：新订单扫描目录内已有最大编号，从下一个继续（已有 1–4，下次从 5 开始），
  不覆盖、不重置。前缀默认取角色 slug（`satoshi`），可用 `--prefix` 自定义。
- **订单靠 `order_id` 区分**：每次下单生成 `YYYYMMDD-HHMMSS` 与 `ordered_at`，
  写进 `brief.json`、`rounds.json.orders[]` 与 `manifest.meta`；`rounds.json` 的
  `rounds[]` 每条也带 `order_id`，历史轮次不覆盖。
- **新订单不覆盖旧记录**：新单开始时旧 `brief.json` 归档为 `brief-<旧order_id>.json`；
  `manifest.json` 合并保留未在本次处理的旧 items；`rounds.json` 追加 `orders[]`。
- **预算只算当前订单**：`avatar_pipeline.py --budget` 与 `rounds.py --check` 都只统计
  当前 `order_id` 的图片/轮次，历史订单的图片不累加、不吃新单预算。
- **联系表按订单生成**：`<前缀>-avatar-<起>-<止>-sheet.jpg`（本单编号最小/最大），
  后一单不覆盖前一单；旧的通用 `contact_sheet.jpg` 只在无编号（legacy）时使用。
- **轮次只存元数据**：`brief.round`、`rounds.json` 记录每轮与每张图属于第几轮；
  文件名与目录都不带轮次。`--round` 仍是最高优先级。
- 订单目录名默认角色 slug：`rounds.py --new-order --root <根> --character satoshi`
  复用 `<根>/satoshi`，只切换订单；不再加时间戳或序号。
- **不再按角度拆订单目录**：角度只写在 `brief.json` / `manifest.json` 里。
- 订单根目录：用户指定优先；未指定用 `<当前工作区>/avatar-orders/`。
- `%TEMP%`、`/tmp` 只能做生成中转：生成后立刻 `Move` 到订单目录，再跑评分。
- 中断或失败：把已生成图留在订单目录，`status.json` 写 `interrupted` / `failed`
  并记录 `phase`、`raw_count`、`last_error`；不得回滚或清理。
- `avatar_pipeline.py` 只写订单目录与 manifest/联系表，不会删除原图；
  清理订单只能由用户明确要求后执行。
- **历史订单不迁移**：旧结构（`<时间戳>-<slug>/raw/round_XX`、`out/crop|native|export`）
  原样保留；`--layout legacy` 仅用于复跑旧订单，新订单一律用默认 `flat`。

`status.json` 最小字段：

```json
{
  "order_id": "20261122-173000",
  "ordered_at": "2026-11-22 17:30:00",
  "state": "running",
  "phase": "generate",
  "created_at": "2026-11-22 15:30:00",
  "updated_at": "2026-11-22 15:31:20",
  "raw_count": 0,
  "export_count": 0,
  "budget": {"limit": 10, "used": 0, "remaining": 10},
  "last_error": null
}
```

## 执行步骤

### 1. 第一段：收输入（不调用生成工具）

先确定订单目录：`rounds.py --new-order --root <根> --character <角色 slug>`，
固定复用 `<根>/<角色 slug>` 并切换 `order_id`；同一角色后续订单不再新建目录。

`/sdz-ptah-avatar` 或普通请求触发后，只做三件事：

1. 读会话里的参考图（复制进订单 `refs/`）与文字说明；参考图**默认只读特征**——
   能否作为生成入参取决于宿主/后端，须先查 `references/backends.json` 并检测，
   不支持时不得假装已使用参考图；
2. 选风格（默认 `tv-anime-cel`；无脸/皮套用 `realistic-armor`）与角色卡
   （有则用，无则按描述建临时卡；无脸角色卡写 `face_mode=faceless`）；
3. 解析性别与默认朝向，写 `<订单目录>/brief.draft.json`（含 `order_id`、
   `ordered_at`，取自 `rounds.py --new-order`），`status.json` 记
   `state=awaiting_input`。

字段见 `references/protocol.md` 第 1 节。默认值：`size=1024`、`count=2`
（上限为预算）、`budget=10`、`view=profile`、`face_mode=faced`。
需要参考图时在 brief 写 `generation` 块（`backend` / `mode` / `reference_images[]` /
`strength` / `fallback`），并在确认页明确「当前后端是否支持参考图、不支持时的处理」。

**朝向默认规则**：

| 性别 | 默认朝向 | 说明 |
|-|-|-|
| `male` | `profile_left` | 角色卡或用户给定 |
| `female` | `profile_right` | 角色卡或用户给定 |
| `unspecified` / 缺失 | `profile_left` | 确认页必须提示「未指定性别，已按朝左」；不从参考图推断 |
| 用户手动指定 | 用户指定 | 覆盖性别默认，记 `angle_source=user` |

### 2. 第二段：确认页（仍不生成）

列出参数，默认值已填好：画质（1K / 2K / 4K，默认 1K）、张数（默认 2，上限预算）、
朝向（按性别规则）、风格（默认唯一项）、输出（方形 PNG，可选 JPG）。

- 能弹 `dynamic-ui` 交互卡片就弹；点「确认生成」后经 `window.sendPrompt`
  回传确认文字，记为 `source=confirmation_card`。
- 弹不出就用编号列表让用户回数字/文字；语义与卡片完全一致。
- 收到明确确认（`确认` / `生成` / `confirm`）后运行：

```powershell
py -3 "$skill\scripts\confirm_order.py" --brief "<订单目录>\brief.json" `
  --confirm "确认生成" --source card
```

确认块写入前会补默认值并校验；**没有 `confirm.confirmed=true` 一律不得生成**。

### 3. 编译提示词（顺序不可换）

按 `references/protocol.md` 第 2.6 节拼接，顺序固定：

1. **风格锚点**：`references/styles/<style_id>.json` 的 `prompt_anchor` +
   线条/阴影/饱和度关键词；
2. **角色卡**：`references/characters/<character_id>.json` 的 `prompt_block`，
   覆盖全部 required 特征；
3. **角度与表情**：`references/angles.json` 中 `brief.angle` 对应条目 + 当前表情；
4. **构图画布**：头像/胸像、肩线入镜、背景平涂铺满方形画布、不画边框/圆形遮罩，
   末尾固定追加 `square canvas, no border, no frame, no circular mask, edge-to-edge`。

每条以 `[PURPOSE]: ` 开头。风格优先级高于「精致」：不要用「高清/杰作/8K」
盖过风格词，不要混第二套画风。侧脸必须带单眼条款（只出现一只眼/一只耳；
朝左：鼻尖在左、耳朵在右；朝右：鼻尖在右、耳朵在左；鼻梁另一侧不画第二只眼、
眼白或眉毛）。prompt 与实际角度写入 `prompts.json`。

### 4. 限量出图（先算预算）

- 首轮张数以 `brief.count` 为准（默认 2，上限为预算）；需要变体时最多再 1 张。
- **生成前必须确认**：`brief.json` 的 `confirm.confirmed` 为 `true` 才可调用
  `GenerateImage`；没有确认块就回第二段确认页，不许直接抽。
- 整单不超过 `--budget`（默认 10），且**按当前订单累计**。生成前先跑
  `rounds.py --check`；余额 ≤0 必须停下说明「预算用尽」并等用户决定是否追加，
  追加确认后才用 `--budget N` 调大，不许静默超额。
- 调用前在 `status.json` 记 `budget.limit`；每成功生成一张，`used += 1`、
  `remaining -= 1`，并与 `raw_count` 同步，不要事后补账。
- `image_size`: `square`；`path`: 绝对路径、**不带扩展名**，先落临时中转目录，
  文件名用 `<angle>_v1_r1` 区分角度/变体（轮次仍由元数据记录），
  生成后立即 `Move` 到 `<订单目录>/`，不允许把原图留在 `%TEMP%`；
  进目录后由流水线统一改名为 `<前缀>-avatar-<N>-src.<ext>`。
- 生成前先 `ToolSearch` 取 `GenerateImage` 的 schema，再用 `DeferExecuteTool` 调用。
  每次调用后核对文件存在且可打开；打不开记为失败，不继续算分。
- 出图后先放大到 800px 看鼻梁后方，出现第二只眼/眼白直接弃用，
  不进入评分。重抽最多 1 张，且必须换「远眼不可见」的措辞。
- 一轮生成完成后立即记账（只写 `rounds.json`，不建目录）：

```powershell
py -3 "$skill\scripts\rounds.py" --order "<订单目录>" --record `
  --round 1 --count 2 --directions "" `
  --files "satoshi-avatar-1.png,satoshi-avatar-2.png"
```

### 5. 跑裁切与评分

```powershell
$skill = Join-Path $env:USERPROFILE ".trae-cn\skills\sdz-ptah-avatar"
py -3 "$skill\scripts\avatar_pipeline.py" `
  --raw "<订单目录>" --brief "<订单目录>\brief.json" --formats 1024 `
  --style "$skill\references\styles\tv-anime-cel.json" `
  --character "$skill\references\characters\satoshi.json" `
  --angles "$skill\references\angles.json" `
  --checklist "<订单目录>\checklist.json" `
  --budget 10
```

- `--raw` 传订单根即可：脚本扫描根目录里的原图并按订单内最大编号继续编。
  默认 `--layout flat`，`--out` 可省（默认写回订单根）。
- `--prefix` 指定文件名前缀；缺省取角色卡 `character_id`、`brief.character_id`，
  再退到目录里已有前缀（都没有才用 `avatar`）。
- 无脸/皮套单改为 `--face-mode faceless --style references/styles/realistic-armor.json
  --character references/characters/fox-armor.json`；需要参考图时加 `--references` 并在
  brief 写 `generation` 块，脚本先做后端能力检测。

- `--formats` 取 `1024` / `2k` / `4k` 的逗号组合；`--jpg` 额外导出压缩版，
  `--jpg-quality` 默认 88。只有用户明确要 2K/4K/JPG 时才传对应参数，
  否则订单目录里只有 `-src` 与成品。
- `--budget` 默认 10；脚本只统计**当前订单**（`order_id`）的原图张数，
  同目录历史订单的图片不重复计数，超预算且未加 `--allow-over-budget` 时
  直接退出（码 3），不评分不导出。
- 当前订单解析顺序：`--order-id` > `brief.order_id` > `rounds.json` 当前订单；
  新单先跑 `rounds.py --new-order`，再写 brief 与出图。
- 不传 `--style` / `--character` / `--checklist` 时对应闸门为 `null`；
  风格/角色单的交付不接受 `null`。
- `--angles` 不传时默认读 `references/angles.json`（不再跳过角度闸门）。
- `--round <值>` 显式指定本批轮次，优先级最高；缺省读 `brief.round`。
  flat 结构下文件名与目录都不带轮次，**不再从文件名/父目录推断**；两者都没有时
  打印「轮次未知……已按单轮处理，可能误判重复」，manifest 记 `round_source=unknown`。
  去重仍只对「同轮 + 同目标角度」生效，跨轮不互判；同轮跨变体近似重复判 `duplicate`。
- 画布闸门始终生效（方形 + 无边框），不依赖是否传风格档案。
- 未启用角度（`reserved`）传入 `--expected-angle` 会直接 `angle_mismatch`，不是 `null`。
- 带 `--brief` 但缺少 `confirm.confirmed=true`：脚本直接退出码 4，不评分不导出。

脚本会写出（flat 结构，订单目录内平铺）：

- `<前缀>-avatar-<N>.png` 1024 成品（方形，默认交付格式）
- `<前缀>-avatar-<N>-src.<ext>` 原图（生成后立即改名归档，不删）
- `<前缀>-avatar-<N>-2k.png` / `-4k.png` 放大版（仅当 `--formats` 含该档）
- `<前缀>-avatar-<N>-q<q>.jpg` 压缩版（仅当 `--jpg`）
- 订单根 `manifest.json`（每张记 `file/order_id/round/variant/angle/format`、
  原图 `raw_quality` 与成品 `final_gate` 分列；旧订单 items 合并保留）、
  `<前缀>-avatar-<起>-<止>-sheet.jpg` 本单联系表

> raw 生成质量分与 final 交付校验分是两个对象：`raw_quality` 不达标代表这张图
> 本身就不该交付，不能因为裁切后 `final_gate` PASS 就算数。

### 6. 判定与补量

- `verdict=PASS` 且 `style` / `character` / `canvas` / `angle` 四道闸门都不为 `FAIL` 才是候选。
- **两类分分开看**：manifest 里 `raw_quality`（生成质量：面部/构图/风格/角度，
  阈值 60）与 `final_gate`（交付校验：截断/留白/占比/画布）是两个对象。
  `final_gate` PASS 不能抵消 `raw_quality` unqualified；交付前必须看 raw 分。
- **raw 不达标的决策树**（优先级高于调参）：
  1) 换提示词措辞（最直接，先动风格/单眼/构图条款）；
  2) 换方向（如 left↔right）或换角色卡细节；
  3) 追加候选（用户同意后加张数，受预算约束）。
  不要调裁切参数、不要放宽 `edge_density` / 饱和度等阈值去让图过闸。
- 风格闸门看主色数、饱和度、边缘密度、背景平涂度；角色闸门看人工清单必填项；
  画布闸门看方形与边框/装饰圈；角度闸门看侧脸双向、正脸与无脸面罩判据
  （侧脸：单眼 + 瞳孔在对应半侧 + 鼻尖侧 + 眼位；正脸：双眼 + eye_y 0.24–0.38 +
  倾斜/平衡；无脸：面罩/高光偏移 + 头盔轮廓对称；判不出方向记 `angle_undetermined`）。
- faceless 主图用头部轮廓指标集（`head_ratio` / `head_width_ratio` / `center_offset`），
  manifest 的 `metric_set` 与 `face_metrics_applied` 必须如实标注。
- 截断只在平涂背景可判定时采信；非平涂底记 `null`，不拿不可靠边圈判死。
- 截断不再按留边距离一刀切：顶部/左/右判截断才 REJECT，底部只记录。
- 构图闸门在最终成品上跑完整：截断 + 上/下留白 + 主体占比 + 顶部平切；
  不可判定的维度记 `null`，不冒充 PASS。
- 无脸图先走头部/轮廓回退；只有回退也失败才记 `locate_failed`。
- 若 PASS 数 < 1 且预算未用完：先报告，再按朝左最多 1 张补量，且必须用户同意。
- 达到预算仍无 PASS：如实报告「本轮无达标候选」，给出最高分与原因分布，
  不要放宽阈值凑数。
- 每轮评分后更新 `status.json`：`phase=score/export`、`export_count`、
  `budget`、`manifest` 路径；中断时写 `state=interrupted`，保留所有已有文件。

### 7. 汇报

给出：候选清单（文件名、角度、总分、**raw 生成质量分与状态**、`face_ratio`、
`eye_center_y`、`cut_edges`、`sharpness`、定位方式、风格/角色/画布/角度闸门结论、原因码）、
性别与实际朝向（含 `angle_source`：default / user）、确认来源
（`chat_message` / `confirmation_card`）、
**本轮实际生成张数与当前订单累计预算**（`rounds.json` 的 `budget_used` / `remaining`）、
contact sheet 路径（本单 `<前缀>-avatar-<起>-<止>-sheet.jpg`）、交付路径、订单目录绝对路径
（`<订单目录>\manifest.json`、`<订单目录>\<前缀>-avatar-<起>-<止>-sheet.jpg`、
`<订单目录>\<前缀>-avatar-<N>.png` 必须在其中）、
以及放大与不可用能力说明。原因码释义见 `references/protocol.md` 第 6 节。
把 `status.json` 重新写成 `state=completed`、`phase=done`。

### 8. 交付后满意度回访与改进方向重出

- 交付首轮后主动问一次「这版满意吗？」：能弹 `dynamic-ui` 就给「满意 / 不满意」
  两个可点选项；弹不出用编号列表 `1 满意 / 2 不满意`。这是交付后对话，
  **不需要再走 `confirmed` 门**，不触发生成。
- 满意：`rounds.py --set-satisfaction --round <N> --satisfaction satisfied` 收尾。
- 不满意：读 `references/feedback.json`（方向目录可配置，脚本不硬编码），
  按编号列出方向：画风贴合度、构图与站位、面部特征、服装与配饰、朝向角度、
  细节精致度；卡片与编号列表语义一致。
- **方向选定即视为确认本轮重出**，不再让用户二次确认；但张数或画质若变化，
  仍回第二段确认页并更新 `params_hash`。
- 重出流程：

```powershell
py -3 "$skill\scripts\rounds.py" --order "<订单目录>" --preview --directions composition,detail
py -3 "$skill\scripts\rounds.py" --order "<订单目录>" --check
py -3 "$skill\scripts\rounds.py" --order "<订单目录>" --record --round 2 `
  --count 2 --directions composition,detail
```

- 方向落回：`style_fidelity` → 风格锚点；`face_features` / `outfit` → 角色卡块；
  `composition` / `detail` → 构图尾段；`angle` → 翻转 `brief.angle`（左↔右）。
- 预算按当前订单累计：`--check` 退出码 5 表示已用尽，必须先明确告知「预算用尽」，
  由用户决定是否追加；追加确认后才用 `--budget N` 调大，不许静默超额。
- **满意度必须落账**：每轮 `satisfaction` 未回填记 `pending`（不是 `null`），
  `--check` 返回 7 并提醒；下次交互继续问，直到每轮回填，三轮全空不能算完。
- 重复轮次会退出码 6，历史轮次元数据不得覆盖。

## 输出格式速查

| 需求 | 参数 | 产物（订单目录内平铺） |
|-|-|-|
| 默认（站点/社区头像） | `--formats 1024` | `<前缀>-avatar-<N>.png` |
| 高清展示 | `--formats 1024,2k`（或 `4k`） | 追加 `-2k.png` / `-4k.png` |
| 有体积限制的网页/App | 追加 `--jpg --jpg-quality 88` | 追加 `-q88.jpg` |

放大手段、速度与画质代价、放大后复检项，见 `references/protocol.md` 第 7 节。

## 不可用能力（本机核实）

- 本机无 numpy / opencv / onnx / 超分模型，`pip` 无网络。
  因此人脸定位与评分均为纯 Pillow 启发式，`Real-ESRGAN` 等超分**不可用**，
  2K/4K 只能用 Pillow Lanczos 插值。
- OCR（文字）、NSFW、情绪分类、风格 embedding、解剖检查均**不可用**，
  对应闸门为 `null`，总分只在「面部质量 + 构图」两维上归一化。
- 风格闸门是**粗指标**（主色数/饱和度/边缘密度/背景平涂度），不是 embedding：
  只能拦明显异体系，不能证明画风精细一致；最终仍需人眼对比参考图。
- 截断判定与边框判定同样是像素启发式，不是语义分割：
  深色头发可能和深色画框混淆，细边框也可能漏检，人工确认不可省。
- 内容留白/主体占比/顶部平切闸门只在平涂背景可判定时生效；
  非平涂底记 `null` 并由人眼复核，不把「算不出」当成通过。
- 角度判定覆盖侧脸双向、正脸与无脸面罩；单眼判定可能把「远眼没检出的 3/4 脸」当成侧脸，
  正脸眼位区间又不能套侧脸，所以 800px 人工查第二只眼仍是必做步骤。
- 参考图能否参与生成取决于宿主/后端，不由本 skill 保证；`trae-generateimage` 当前
  不支持参考图入参。换宿主后必须重跑 `backends.json` 能力检测，不能沿用旧结论。
  无 embedding 模型时身份相似度只能记 `null` + 人工清单，不能拿色板相似度冒充。

## 边界

- 只做头像执行链；不写方案文档、不做写实真人、不做视频。
- 阈值、权重、原因码的权威来源是本 skill 内的 `references/protocol.md`；
  可选外链 `ai-anime-avatar/outputs/AI动漫头像生成方案.md` 仅补充设计说明。
  两者冲突且方案文档可访问时以方案文档为准，并同步修正 protocol.md。
- 源码唯一真相在仓库 `skills/sdz-ptah-avatar/`；装到 TRAE 的全局技能目录用
  `python skills/install.py`（Windows 下 `python` 报 9009 时改用 `py -3 skills/install.py`；
  默认符号链接，无权限时自动回退复制）；装到其他宿主用 `--target <其技能目录>`。
  不要直接改已安装副本。
