# sdz-ptah-avatar 执行规范

本文件是可执行条款：阈值、公式、原因码、命名与产物字段，自包含、可直接执行。
可选外链（补充设计依据，缺失不影响本文件生效）：
`ai-anime-avatar/outputs/AI动漫头像生成方案.md`（第 1.3、2.6、3.2、3.4、4.x 节）。

> **第一原则（优先于所有处理侧优化）：质量靠生成，后处理只做交付。**
> 生成图不合适，裁切、放大、调色都救不回来。因此：
> 1. 处理阶段只保留三件事——取景裁切、几何与截断校验、格式导出；任何条款都不得
>    暗示后处理能提升观感。
> 2. **评分对象分开**：原图必须单独出生成质量分 `raw_quality`（面部/轮廓、构图、
>    风格、角度，阈值 60）；成品只出交付校验分 `final_gate`（截断/留白/占比/画布）。
>    两者都写进 `manifest.json`，**成品过闸不得掩盖原图不合格**。
> 3. **原图不达标就重抽**：换提示词、换方向、追加候选；不调裁切参数、不放宽阈值。
> 4. 改进预算放在提示词编译、风格档案、角色卡与候选筛选；默认每轮 2 张，
>    命中率低再追加，脚本不自动加预算。
> 5. 裁切只做取景：1920 原生 → 方形头像构图（保头顶、居中、留白合理）；
>    不修脸、不补画、不修表情，补偿能力上限就是取景。

> **当前范围（硬性）**：做**侧脸头像**，朝左（`profile_left`，鼻尖在左、耳朵在右）
> 与朝右（`profile_right`，鼻尖在右、耳朵在左）双向都启用。
> 默认朝向按性别：**男性朝左、女性朝右、未指定默认朝左并提示确认**；用户可手动覆盖。
> 正脸、3/4、仰俯仍为 `reserved`，不实现、不产出、不测试。
> 需要扩角度或加风格时，先改本节范围与对应档案，再改评分分支；不得在执行时自行扩出去。
>
> **方向判不出时（硬性）**：瞳孔位置与鼻尖侧缺失或互相冲突 → `angle_undetermined`，
> FAIL 并暂停请人工确认；不自动猜朝向，也不拿默认值顶替。
>
> **画布（硬性）**：画布与导出一律方形、满出血；禁止边框、圆形遮罩、装饰圈。
> 提示词必须包含 `square canvas, no border, no frame, no circular mask, edge-to-edge`。
>
> **低成本模式（默认）**：每单生成预算 `--budget`，默认 **10** 张；每张生成都要计数，
> 超预算必须停下并向用户确认，不能默默继续抽。量产单需用户明确同意后再调大。
> 预算**按轮累计**（`rounds.json.budget_used`）：累计到上限即停，追加必须再次确认；
> **不得静默超额**。

---

## 1. brief.json（需求单）

```json
{
  "style_id": "tv-anime-cel",
  "character_id": "satoshi",
  "character": "小智：黑色尖刺短发、红白棒球帽、蓝色外套白领、脸颊三道闪电纹",
  "temperament": "元气、倔强但友好",
  "usage": "社交平台方头像",
  "size": "1024",
  "formats": ["1024"],
  "jpg": false,
  "count": 2,
  "view": "profile",
  "face_mode": "faced",
  "gender": "male",
  "gender_source": "card",
  "angle": "profile_left",
  "angle_source": "default",
  "budget": 10,
  "generation": {
    "backend": "trae-generateimage",
    "mode": "text2img",
    "reference_images": [],
    "strength": null,
    "fallback": "fail"
  },
  "constraints": "不要文字、不要多人、不要明显多指",
  "confirm": {
    "confirmed": true,
    "at": "2026-11-22T16:00:00",
    "source": "chat_message",
    "text": "确认生成",
    "params_hash": "3f2a1c9b7d8e4a10"
  }
}
```

| 字段 | 必需 | 说明 |
|-|-|-|
| `style_id` | 是 | 风格档案 id，对应 `references/styles/<id>.json`；当前仅 `tv-anime-cel` |
| `character_id` | 是 | 角色卡 id，对应 `references/characters/<id>.json`；当前仅 `satoshi` |
| `character` | 是 | 角色卡的简要文字版，用于报告头；完整特征以角色卡为准 |
| `temperament` | 是 | 气质方向，决定措辞变体 |
| `usage` | 是 | 用途，决定是否需要缩图可读 |
| `size` | 否 | 主交付档位：`1024` / `2k` / `4k`；默认 `1024` |
| `formats` | 否 | 与 `size` 一致的导出档位列表；默认 `[size]` |
| `jpg` | 否 | 是否导出压缩版 |
| `count` | 否 | 首轮张数，默认 **2**，上限为预算；超预算会被截到预算并记 `count_capped` |
| `view` | 否 | 当前固定 `profile`；其它视角为预留，不执行 |
| `face_mode` | 否 | `faced` / `faceless`；缺省读角色卡 `face_mode`，再缺省 `faced`。无脸/皮套角色必须写 `faceless` |
| `gender` | 否 | `male` / `female` / `unspecified`；缺失时按 `unspecified` 处理 |
| `gender_source` | 否 | `user` / `card` / `unspecified`，由流水线补全 |
| `angle` | 否 | `profile_left` / `profile_right` / `front`；缺失时按性别取默认（男左、女右、未指定左） |
| `angle_source` | 否 | `default` / `user`；用户手动指定即为 `user` |
| `budget` | 否 | 本单生成预算，默认 10；与 `--budget` 取更小值 |
| `round` | 否 | 显式轮次；优先级次于 `--round`、高于文件名/目录推断。缺省且推断失败时告警并按单轮处理（见 5.3） |
| `generation` | 否 | 生成后端与参考图参数块，见 3.1；缺省按 `text2img` + `fallback=fail` |
| `constraints` | 否 | 追加的负面约束，编译进 prompt 的禁止项描述 |
| `confirm` | 是（执行前） | 显式确认块；`confirmed` 必须为 `true`，否则流水线拒绝执行 |

`constraints` 只能写成自然语言描述（生成端无负向词入参），例如
「画面保持干净，只有一个人物，没有文字与水印」。

---

## 1.5 两段式流程与确认门（硬性）

**第一段：收输入（不生成）**。入口为 `/sdz-ptah-avatar` 或普通请求，只做：

1. 读会话里的参考图（复制进订单 `refs/`，**只用于读特征，不能当生成条件**）与文字说明；
2. 选风格档案（默认 `tv-anime-cel`）与角色卡（有则用，无则按描述建临时卡）；
3. 解析性别：用户明确说明 > 角色卡 `gender` > `unspecified`；**不从参考图推断性别**；
4. 写 `brief.draft.json`，`status.json` 记 `state=awaiting_input`。

**第二段：确认页（仍不生成）**。列出参数，默认值已填：

| 项 | 默认 | 可选 |
|-|-|-|
| 画质 | 1K（`1024`） | 1K / 2K / 4K |
| 张数 | 2（上限为预算 10） | 1–10 |
| 朝向 | 按性别（男左、女右、未指定左） | 朝左 / 朝右 |
| 风格 | `tv-anime-cel` | 当前仅此一项 |
| 输出 | 方形 PNG | 可加 JPG |

呈现路径（二选一，语义一致）：

- **交互卡片**：能用 `dynamic-ui` 时弹单选卡片；点「确认生成」后通过
  `window.sendPrompt('...')` 回传一段确认文字，等价于用户发消息。
- **编号列表**：不能弹组件时列出 `1 1K / 2 1.5K…` 之类编号，用户回数字或文字。

**硬门：未确认不生成**

- 只有收到明确确认（`确认` / `生成` / `confirm`，或确认卡片回传）后，
  才运行 `scripts/confirm_order.py` 写入 `confirm` 块，并把 `status.json`
  从 `awaiting_confirmation` 改为 `confirmed`。
- 生成前检查：`brief.json` 没有 `confirm.confirmed=true` → **一律不得调用
  `GenerateImage`**；默认值、只给参数、只上传参考图，都不算确认。
- 流水线同样校验：带 `--brief` 但未确认 → 直接退出码 4，不评分、不导出。
- 确认块字段：`confirmed` / `at` / `source`（`chat_message` 或 `confirmation_card`）/
  `text` / `params_hash`。参数变了（画质、张数、朝向、性别）必须重新确认。
- **唯一例外**：交付后的满意度回访与「改进方向」选择属于交付后对话，不需要再走
  `confirmed` 门；方向选定即视为确认本轮重出（见 3.5）。但只要张数或画质变化，
  仍需重新确认并更新 `params_hash`。

```powershell
py -3 scripts/confirm_order.py --brief "<订单目录>\brief.json" `
  --confirm "确认生成" --source card
py -3 scripts/confirm_order.py --brief "<订单目录>\brief.json" --check
```

---

## 2. 提示词编译

### 2.1 结构

```
[PURPOSE]: <风格锚点>，<用途> 用动漫头像，<角色卡特征块>，<角度 + 表情>，<光影/背景>，<方形满出血构图>。
```

- 首段必须是 `[PURPOSE]: `，后面是自然语言，不用 tag 堆叠（tag 也可用，但属性不精确）。
- 单条 80–160 字，只描述「能看见的东西」，不写参数。
- 必须包含：风格锚点、角色卡特征、角度、表情、光影、背景、取景、构图。
- **顺序不可换**：风格在前，角色在中，角度与表情在后，构图留白收尾。
  风格优先级高于「精致」——宁可少写装饰词，也不要写出与风格档案冲突的质感词。
- **构图收尾固定包含英文画布条款**：`square canvas, no border, no frame, no circular mask, edge-to-edge`。

### 2.2 四个变体维度（固定）

| 变体 | 变什么 | 示例差异 |
|-|-|-|
| v1 | 气质措辞 | 「温柔含笑」 |
| v2 | 取景松紧 | 「头脸占满画面」vs「稍远一点，露出肩线」 |
| v3 | 光影 | 「柔和逆光」vs「冷调侧光」 |
| v4 | 背景 | 「纯净浅色渐变」vs「简洁单色背景」 |

### 2.3 禁止项（生成端做不到，写了也没用）

- 精确俯仰角 / 精确头部偏转角 / 精确手部动作
- 精确配饰数量（「一枚耳饰」会漂移成多枚）
- 「重生成同一张图」「保持同一个 seed」
- 分辨率、模型名、采样步数等参数词

### 2.4 侧脸（view=profile）附加条款

按 `brief.angle` 产出朝左或朝右。每条 prompt 必须同时写清：

- 只出现一只眼睛、一只耳朵；鼻梁另一侧不画第二只眼、眼白或眉毛；
- 头部侧转：朝左时鼻尖朝画面左侧、耳朵在右；朝右时鼻尖朝画面右侧、耳朵在左；
- 帽子/发饰贴着头部，帽檐不压住眼睛；
- 方形满出血：画布满出血到四边，不画圆形画框、装饰圈、边框；
- 肩线完整入镜，不要裁到胸口。

不要写「精确 90° 侧脸」「精确朝向角度」——生成端不保证角度，只保证可见元素。
实测「远眼」是最常见的漏网缺陷：正面/四分之三脸会带出第二只眼，
必须在 800px 放大下逐张检查（见第 3 节侧脸验收）。

### 2.5 三层档案（风格 / 角色 / 角度）

提示词不再从零写，而是由三份固定档案拼出来。路径固定，新增风格/角色只加文件，不改脚本：

| 层 | 路径 | 作用 |
|-|-|-|
| 风格档案 | `references/styles/<style-id>.json` | 画风锚点与粗指标门槛；当前 `tv-anime-cel`（赛璐璐）、`realistic-armor`（写实/皮套） |
| 角色卡 | `references/characters/<character-id>.json` | 可文字化的外形特征与人工核对清单；当前 `satoshi`、`fox-armor`（无脸皮套） |
| 角度矩阵 | `references/angles.json` | 角度定义、提示词表述、可见眼数约束；`profile_left` / `profile_right` / `front` 启用 |

**风格档案固定字段**：

| 字段 | 内容 |
|-|-|
| `line.weight_px_at_1024` / `line.color` | 1024 图上的线条粗细与描边色 |
| `shading.layers` / `shading.gradient` | 阴影层数、是否渐变 |
| `palette.saturation_tendency` | 饱和度倾向与主色锚点 |
| `face.eyes` / `face.brows` / `face.nose` / `face.mouth` | 眼、眉、鼻、嘴画法 |
| `face.proportions` | 脸型比例与年龄感 |
| `background.mode` / `background.allowed` | 背景处理：平涂 / 留白 / 暗调 |
| `canvas` | 方形、满出血、无边框/圆形遮罩，以及固定英文画布条款 |
| `prompt_anchor` | 编译时置顶的风格句 |
| `gate` | 风格粗指标门槛；`gate.applicable=false` 表示该维度显式不适用 |
| `gate.note` | 不适用/校准说明；声明不适用时闸门记 `null` + 原因，绝不当作通过 |
| `prompt_forbidden` | 与画风冲突、必须避免的质感词 |

**角色卡固定字段**：

| 字段 | 内容 |
|-|-|
| `face_mode` | `faced` / `faceless`；决定定位锚点、指标集与角度判据 |
| `gender` | `male` / `female` / `unspecified`；决定默认朝向（男左、女右、未指定默认左） |
| `hair.direction` / `hair.tufts` | 发型走向、发绺数量与形态 |
| `headwear.type` / `headwear.logo` | 帽型、徽标 |
| `outfit.palette` / `outfit.collar` | 服装配色、领边 |
| `face.shape` / `face.age` | 脸型与年龄感 |
| `signature` | 标志性细节（如脸颊纹） |
| `prompt_block` | 编译时置中的角色特征块 |
| `checklist` | 人工逐项核对清单，`required` 项缺一不放行 |

无脸/皮套角色的 `checklist` 换成轮廓类必填项：耳鳍/头盔形状、面罩、装甲配色、
胸口或肩部标识等；不得沿用「眼睛/眉毛/脸颊纹」这类只对有人脸成立的项目。

### 2.6 编译顺序（不可换）

1. **风格锚点**：`prompt_anchor` + 线条/阴影/饱和度关键词。风格优先级高于「精致」。
2. **角色卡**：`prompt_block` + 全部 required 特征。不得用「帅气少年」这类泛词替代具体特征。
3. **角度与表情**：取 `angles.json` 对应条目的 `prompt`，再叠加当前表情。
4. **构图画布**：头像/胸像、肩线入镜、背景平涂铺满方形画布、不画边框/圆形遮罩；
   末尾固定追加 `square canvas, no border, no frame, no circular mask, edge-to-edge`。

禁止：混入第二套画风词、用「高清/杰作/8K」压过风格词、写成 tag 堆叠。
示例（侧脸朝左）：

```
[PURPOSE]: 1990 年代日本 TV 动画赛璐璐风格，干净细线稿，平涂上色，硬边双层阴影，无渐变；
社交平台方头像用侧脸胸像；小智：黑色尖刺短发、两侧鬓发外炸、红白棒球帽、蓝色外套白领、
琥珀棕大眼、两颊三道闪电纹；接近正侧脸，鼻尖朝左、耳朵在右，只出现一只眼睛和一只耳朵，
鼻梁另一侧不画第二只眼；柔和平光；纯净浅色平涂背景铺满画布；
square canvas, no border, no frame, no circular mask, edge-to-edge，肩线完整入镜。
```

### 2.7 角度矩阵与默认朝向

| 角度 | 状态 | 可见眼数 | 瞳孔位置 | 鼻尖侧 | 眼位 |
|-|-|-|-|-|-|
| `profile_left` | 启用 | 1 | 单眼中心 ≤0.42 头宽 | 左 | 0.32–0.46 |
| `profile_right` | 启用 | 1 | 单眼中心 ≥0.58 头宽 | 右 | 0.32–0.46 |
| `front` | 启用 | 2 | 双眼粗糙位置 0.38–0.62 | — | **0.24–0.38**（不套用侧脸 0.32–0.46） |
| 3/4 / 仰俯 | `reserved`，命中即 `angle_mismatch` | — | — | — | — |

正脸还要同时满足：双眼都检出、眼线倾斜 ≤0.07、双眼水平平衡 ≤0.08。正脸区间用
`ai-anime-avatar/verify/raw` 的 5 张正样本校准（实测 eye_center_y 0.303–0.316），
不从侧脸区间外推；3/4 脸常也能检出双眼，但眼位/倾斜对不上，仍按 `reserved` 拒。

**无脸/皮套（`face_mode=faceless`）**：不用眼部锚点，角度改用面罩/高光横向偏移
（朝左 ≤−0.08、朝右 ≥+0.08、正面 |偏移| ≤0.06+0.02）与头盔左右轮廓对称性；
信号缺失或冲突 → `angle_undetermined`，不猜。

**默认朝向规则（可覆盖）**：

| 条件 | 默认 | 说明 |
|-|-|-|
| `gender=male` | `profile_left` | 角色卡或用户给定 |
| `gender=female` | `profile_right` | 角色卡或用户给定 |
| `gender=unspecified` / 缺失 | `profile_left` | 确认页必须提示「未指定性别，已按朝左」；不从参考图推断 |
| 用户手动指定朝左/朝右 | 用户指定 | 覆盖性别默认，记 `angle_source=user` |

角度判定回答「朝左 / 朝右 / 正面 / 判不出」，3/4 与仰俯不实现：

```
有人脸：
  可见眼数 == 1
  且 朝左：瞳孔横向位置 ≤0.42 且 鼻尖侧向左
     朝右：瞳孔横向位置 ≥0.58 且 鼻尖侧向右
  且 0.32 ≤ eye_center_y ≤ 0.46
  可见眼数 == 2 → 正面：eye_y 0.24–0.38 + 眼线倾斜/双眼平衡达标
无脸：
  面罩/高光横向偏移与头盔轮廓左右对称性一致 → 朝左/朝右/正面
  信号缺失或冲突 → angle_undetermined
```

- **方向别写反**：实测朝左样本 `pupil_edge=0.178`、朝右样本 `0.789`；朝左时可见眼在头框
  左半部（鼻尖一侧），朝右在右半。`nose_side` 由头部框内肤色重心辅助判断，与瞳孔交叉校验。
- **瞳孔极度偏一侧的例外**：`pupil_edge ≤0.12` 或 `≥0.88` 时方向已确定；若鼻尖侧
  不可用（肤色重心落在中线、背景嘈杂）不把明确方向判成未定；鼻尖与瞳孔冲突时仍判未定。
- **判不出方向时**（瞳孔或鼻尖侧缺失/冲突）→ `angle_undetermined`，FAIL 并暂停请人工确认；
  不用默认朝向顶替判定结果。
- 方向明确但与期望不符 → `angle_mismatch`；未启用角度直接 FAIL，不返回 `null`。
- 已知局限：单眼判定会把「远眼没被检出的 3/4 脸」误判成侧脸。因此 800px 人工查第二只眼
  仍是必做步骤；角度闸门只是筛子，不是证明。

---

## 3. 生成协议

| 项 | 规则 |
|-|-|
| 调用工具 | `GenerateImage`（经 `ToolSearch` 取 schema，`DeferExecuteTool` 执行） |
| 入参 | 仅 `prompt` 与 `image_size`；无 seed / 负向词 / 参考图 / 批量 |
| 原生输出 | 1920×1920 方形 JPEG（实测 385–510 KB）；提示词显式要求满出血、无边框 |
| 当前角度 | 按 `brief.angle`：`profile_left` 或 `profile_right`；默认按性别规则，用户可覆盖 |
| 首轮 | 默认 2 张（画质默认 1K）；必须完成确认门后才允许调用 |
| 二轮补量 | 只在预算内、且用户同意后补；同一角度最多再 1 张 |
| 单需求总预算 | `--budget`，默认 **10**；每张调用都计数，**按轮累计**，超预算停下确认 |
| 轮次记录 | 每轮写 `rounds.json`（`round/count/directions[]/budget_used`），产物落 `<订单目录>/roundNN/`（平铺）；历史轮次不覆盖 |
| 预算落盘 | `status.json` 的 `budget{limit,used,remaining}` 每次生成后 +1；manifest 记录实际张数 |
| 侧脸变体 | 需要重抽时，换「远眼不可见」的措辞（至少两种写法），不要原句连抽 |
| 侧脸验收 | 每张先放大到 800px 看鼻梁后方；出现第二只眼/眼白即弃用，不进入评分 |
| 重抽规则 | 同一角度连续 2 次不满足 `subject_cut` / `angle_mismatch` / `angle_undetermined` 即停，先报告再决定 |
| 确认门 | `brief.confirm.confirmed` 必须为 `true`；未确认不得调用 `GenerateImage`，流水线直接退出码 4 |
| 命名 | 生成中转名 `<angle>_v<variant>_r<round>.jpg`（如 `profile_left_v1_r1.jpg`）；`v` 是变体、`r` 是轮次（去重按 `r` 分组）；进轮目录后改名为 `NN_src.<ext>`，成品为 `NN_final.png` |
| 落盘 | 先写临时中转路径，生成后逐张 `Move` 到 `<订单目录>/roundNN/` 并改 `NN_src.<ext>`；不得留在 `%TEMP%` |
| 失败处理 | 文件缺失或打不开记为该次失败，不进入评分；已生成图与 `status.json` 必须保留 |

不可复现：同一 prompt 两次结果不同，manifest 只记录 prompt 与时间，不记录 seed。

### 3.1 参考图生成与后端能力（调用前必须检测）

参考图**不是本 skill 的固有能力**，取决于宿主与后端。能力登记在
`references/backends.json`，每个后端声明 `available`、`reference_images.supported`、
`max`、`roles`、`strength_range`。当前宿主 `trae-generateimage` 实测只有
`prompt` / `image_size` 两个入参，`supported=false`；换宿主或接入新后端必须重跑检测，
不得沿用上一单结论。

`brief.generation` 字段：

| 字段 | 说明 |
|-|-|
| `backend` | 后端 id，必须在 `backends.json` 登记 |
| `mode` | `reference`（要参考图）或 `text2img` |
| `reference_images[]` | 每项 `{path, role}`，`role` 取后端声明的 `style`/`identity`/`outfit` |
| `strength` | 参考图强度，必须在后端 `strength_range` 内 |
| `fallback` | 不支持参考图时怎么办：默认 `fail`；只有用户显式同意才可 `text2img` |

调用前检测（`avatar_pipeline.py` 在评分/导出前执行）：

1. 后端未登记 / `available=false` → 退出码 **4**；
2. `mode=reference` 或带参考图，但后端 `reference_images.supported=false`：
   - `fallback=fail`（默认）→ 退出码 **4**，**不得静默退回文生图**；
   - `fallback=text2img`（仅用户显式同意）→ 继续，但 manifest
     `identity_unverified=true`，报告首屏显著警告「参考图未参与生成，不能据此认为身份一致」；
3. 参考图数量超过 `max`、`strength` 越界 → 退出码 4；
4. `mode=reference` 但没有可读参考图 → 退出码 4。

参考图相似度指标 `reference_similarity`：

- **风格相似度**可算：色板直方图交集 0.50 + 饱和度差 0.25 + 边缘密度差 0.25，
  三项都是像素统计，只说明「同体系」，不是风格 embedding；
- **身份相似度**无 embedding 模型时记 `null`，同时给 `manual_identity_checklist`
  （发型/耳鳍、头盔面罩、装甲配色、胸口标识、身材比例），**不得用色板相似度冒充身份一致**。

**低成本验证优先**：能用参考图或已有产物算出的结论（风格粗指标、角度判定、阈值校准），
一律不出图。只有必须验证「生成端实际出图」时才动用预算，且先跑最小张数。
每轮报告必须写出「本轮实际生成 N 张 / 预算 M 张」，包括失败与重抽。

---

## 3.5 满意度回访与改进方向重出（交付后）

**触发时机**：首轮产物已出并交付（报告 + 预览链接已给）之后，主动问一次
「这版满意吗？」。这是**交付后对话**，不触发生成，也不需要再走 `confirmed` 门。

**回访呈现（二选一，语义一致）**：

- 交互卡片：两个可点选项「满意 / 不满意」。
- 编号列表降级：`1 满意 / 2 不满意`，用户回数字或文字即可。

**用户满意**：收尾，用 `rounds.py --set-satisfaction --round N --satisfaction satisfied`
回填本轮满意度，不再出图。**未回填的轮次会保持 `pending`**，
`rounds.py --check` 返回 7 并在下次交互继续提醒，不能默认算满意。

**用户不满意**：读 `references/feedback.json` 列出改进方向（可配置，不在脚本里硬编码），
可点卡片 + 编号列表降级。默认目录至少覆盖六类：

| id | 方向 | 落回层 |
|-|-|-|
| `style_fidelity` | 画风贴合度（更贴参考截图） | `style.prompt_anchor` |
| `composition` | 构图与站位（脸大小 / 上下位置 / 留白） | `prompt.tail` |
| `face_features` | 面部特征（脸型 / 眼睛 / 鼻型 / 年龄感） | `character.prompt_block` |
| `outfit` | 服装与配饰（帽子 / 徽标 / 领口 / 耳饰） | `character.prompt_block` |
| `angle` | 朝向角度（左 ↔ 右翻转） | `brief.angle`（`angle_flip`） |
| `detail` | 细节精致度（线条 / 发丝 / 高光） | `prompt.tail` |

**确认语义**：方向选定即视为确认本轮重出，**不再要求用户二次确认**；
但若张数或画质相对上一轮变化，仍按第二段确认门重新确认并更新 `params_hash`。

**执行顺序**：

1. `rounds.py --preview --directions <id,...>` 取 `prompt_appendix` 与 `angle_flip`；
2. 把 `prompt_clause` 追加到对应层（不改变 §2.6 编译顺序，只强化该层措辞）；
   `angle_flip=true` 时把 `brief.angle` 左↔右翻转，`angle_source=feedback`；
3. 生成前跑 `rounds.py --check`：余额 ≤0 时**必须先问用户是否追加预算**，
   确认后用 `--budget N` 调整上限；**不得静默超额**；
4. 出图后 `rounds.py --record --round N --count N --directions ...`，
   自动创建 `<订单目录>/roundNN/`，历史轮次不覆盖。

```powershell
py -3 scripts/rounds.py --order "<订单目录>" --check
py -3 scripts/rounds.py --order "<订单目录>" --preview --directions composition,detail
py -3 scripts/rounds.py --order "<订单目录>" --record --round 2 --count 2 `
  --directions composition,detail
```

**退出码**：`0` 成功；`2` 参数/未知方向；`5` 预算用尽（须先问用户）；
`6` 重复轮次（不覆盖历史）；`7` 有轮次满意度未回填（pending）。

**满意度必须落账**：`rounds.json` 每轮的 `satisfaction` 只允许
`satisfied` / `unsatisfied`。未回填一律记 `pending`（不是 `null`），
顶层 `satisfaction` 同步为 `pending`，`pending_rounds` 列出轮次；
`--check` 返回 7 并打印提醒，`--record` 也会带出提醒。三轮全 `null`
不能算完，下次交互必须继续问，直到每轮都回填。

`rounds.json` 最小结构：

```json
{
  "schema": "avatar-rounds/1.0",
  "order_id": "satoshi-01",
  "budget_limit": 10,
  "budget_used": 2,
  "remaining": 8,
  "satisfaction": "pending",
  "pending_rounds": [1],
  "rounds": [
    {
      "round": 1,
      "count": 2,
      "directions": [],
      "budget_used": 2,
      "generated_at": "2026-11-22 15:31:20",
      "satisfaction": "pending",
      "round_dir": "round01"
    }
  ]
}
```

`budget_used` 是累计值（各轮 `count` 之和），不是本轮增量。
预算耗尽时先明确告知「预算用尽」，由用户决定是否追加；追加必须再确认一次。

---

## 4. 裁切补偿

### 4.1 脸框来源

**无脸/皮套（`face_mode=faceless`）不走下列眼部/肤色锚点**，直接用非背景内容框
（`content-head`）：平涂底四角取背景色 → flood fill 得内容框 → 取内容最高点与
头宽最大行作为头部/头盔整体轮廓。`method=content-head`、`eyes=[]`、`profile=false`；
内容框不可靠时不猜头部位置，记 `locate_failed`。裁切锚点为目标头宽占画面
0.44 / 0.50 / 0.56 三档 + 全幅；头部占比目标 0.35、头宽目标 0.50、居中偏移 ≤0.10。

1. **主路径（眼睛锚点）**：找高饱和色块（`sat ≥ 60`，`mx ≥ 70`，不贴画面边），
   两两配成双眼（水平间距 5%–42% 画面宽，高度差 ≤9% 画面高），
   在全分辨率上求虹膜质心。
   脸框：`face_w = 2.00 × 眼距`，`face_h = 1.65 × 眼距`，
   顶边 = 眼线 − 0.42 × 脸高，水平居中于双眼中点。
2. **回退（皮肤块）**：`Cr−Cb ≥ 2`、`126 ≤ Cr ≤ 170`、`85 ≤ Cb ≤ 146`、`Y ≥ 100`
   的连通块中，取「面积达到最大块 45% 以上且最靠上」的一块作为脸块（肩颈常与脸
   连成更大的肤色块，只取最大块会把脸框落到肩颈上）。回退时 `conf = 0.45`，
   眼线按脸框 0.47 估算。
   入图前先用**外圈主色**估计背景，与背景色距离 ≤45（YCbCr 曼哈顿）的像素不参与
   肤色判定，避免米白/浅米底被整片当成皮肤（实测会把脸框放大到整幅）。
   旧下限 `Cb ≥ 110` 会把小麦色/暖色皮肤整张脸判丢，实测已下调到 85；
   白色（Cb≈Cr）仍被 `Cr−Cb ≥ 2` 挡住。
3. **侧脸（单眼）**：双眼配不成对时，取上述脸块上部，找「白色眼白 + 深色瞳孔」块
   （眼白 `min(R,G,B) ≥195` 且 `max−min ≤22`，排除米白背景；
   面积 12 格–5% 样点、宽为肤色宽 7%–30%、横向位于肤色框 8%–82%、
   纵向位于肤色框上部 60%、周围深色像素 ≥5%），以瞳孔/描线重心为眼线。
   头宽取眼下 10% 肤色高度处的非背景内容宽度，头顶取该宽度内的内容最高点
   （含头发/帽子），下巴取肤色宽度收窄到 55% 的位置。`conf = 0.52`，`profile = true`。
4. **侧脸回退（无眼白）**：白色/浅色皮肤的眼白与面部同色，找不到眼白锚点时，
   用脸块上部 55% 行的最大跨度定头宽，非背景内容最高点定头顶，
   `眼线 = 头顶 + 0.55 × 头高`，`头高 = 1.02 × 头宽`，`conf = 0.42`、
   `method = profile-head`，仍走侧脸闸门，不伪装成高可信度。
   双眼配对的合理性校验：`顶边 = 眼线 − 0.42 × 脸高` 若 `< −2% 画面高`
   或脸高 `> 85% 画面高`，判为错误配对（常见于把头发/耳饰高光配成一对），
   放弃双眼锚点转侧脸通道。
   侧脸只有一只眼，不参与对称性比较。

### 4.2 裁切公式

```
目标脸占比  = 0.62
裁切边长 s  = sqrt(脸框面积 ÷ (0.785 × 目标脸占比))
理想 left   = 脸框中心 x − s/2
理想 top    = 眼线 y − 眼位目标 × s   # 正脸 0.26，侧脸 0.40（箱体含头发/帽子）
约束 0      top ≤ 内容最高点 − 0.06 × s  # 头顶/耳尖安全边距（平涂底可判定时）
约束 1      top ≤ 脸框顶 − 0.05 × s     # 头顶留白
约束 2      top ≥ 脸框底 − s + 0.06 × s # 下巴留白
约束 3      left / top 夹到画面内
裁切补偿代价 cost = (|left−理想left| + |top−理想top|) ÷ s
```

**约束 0（裁切兜底，硬性）**：平涂背景下先算非背景内容框，取内容最高点；
裁切上沿必须在其上方留出 6% 安全边距。宁可加大边长或退回全幅，
也不允许把头顶/耳尖/头盔顶切平。内容框不可靠（非平涂底）时该约束记 `null`，
但截断闸门照常运行，不得把 `null` 当成通过。

同时评估 `目标脸占比 = 0.56 / 0.62 / 0.68` 三档，外加一个**全幅候选**
（边长 = 画面短边，代价按同一套理想位置计算）。已紧构图的原图不应为凑目标
脸占比被二次放大裁切。选择顺序：先取构图闸门全过的候选，再按总分最高；
若全部有构图问题，才取总分最高者并如实带出原因码。

### 4.3 满出血与截断（裁切产物）

- 不再要求「脸框离边 ≥2.5%」：方形满出血下，头发/帽顶接近边沿是正常的。
- 构建裁切时仍预留 6% 头顶安全边距（约束 0），最终由**截断 + 内容闸门**决定是否驳回：
  贴边且主体完整 → 放行；头被切平、上下留白越界、主体占比过小 → 驳回或重裁。
- 下巴到裁切边 ≥5%。
- 眼睛正脸落在上部 1/3（目标 0.26）；侧脸箱体含头发/帽子，目标 0.40，
  允许 0.32–0.46，不能套用正脸的 0.36 上限。

**成品内容闸门（硬性，平涂底可判定时生效）**：

| 指标 | 门槛 | 说明 |
|-|-|-|
| 上留白（内容顶到画布上沿） | 1.5%–16% | 下界仅在原图本身有空间时要求 |
| 下留白（内容底到画布下沿） | ≤18% | 胸像允许贴底（0%） |
| 主体占比（内容框面积 ÷ 画布面积） | 40%–95% | 极狐那类「头小、四周大留白」直接驳回 |
| 顶部平切比（内容最高行最长连续段 ÷ 内容宽） | ≤22% | 自然头顶/耳尖是窄接触；平切会形成宽横条 |

闸门在**最终成品**上重跑，manifest 同时记录原图 `raw.content` 与成品
`final_gate.content`；若输入本身就是成品方图（返修场景），原图内容闸门同样生效，
再裁一次无法还原已经切掉的头顶。

---

## 5. 评分与硬门槛

### 5.1 硬门槛（全部通过才是 PASS）

| 指标 | 门槛 | 说明 |
|-|-|-|
| `face_ratio` | 0.34 ≤ x ≤ 0.82 | 脸框面积 ÷ 圆形安全区面积；侧脸头框含头发/帽子，面积噪声大，故放宽 |
| `eye_center_y`（正脸） | 0.12 ≤ y ≤ 0.36 | 裁切图上眼睛纵向位置 |
| `eye_center_y`（侧脸） | 0.32 ≤ y ≤ 0.46 | 两张人工认可正样本实测 0.391 / 0.409，共同区间收紧 |
| `subject_cut` | 顶/左/右任一边判截断 | 见 5.8；单纯贴边不触发 |
| 内容留白 / 主体占比 / 顶部平切 | 见 4.3 | 平涂底可判定时生效；不可判定记 `null`，不当通过 |
| `sharpness` | ≥ 8 | 面部边缘 stddev |
| `thumb` | ≥ 0.35 | 96px 缩图可读性 |
| `conf` | ≥ 0.40 | 人脸定位可信度 |
| `candidates` | = 1 | 多人/多脸判定 |

**无脸/皮套（`face_mode=faceless`）不使用上表的面部类指标**，改用头部轮廓指标集：

| 指标 | 门槛 | 说明 |
|-|-|-|
| `head_ratio`（头部框面积 ÷ 圆形安全区） | 0.18–0.62，目标 0.35 | 替代 `face_ratio` |
| `head_width_ratio`（头宽 ÷ 画面宽） | 目标 0.50 | 裁切档位 0.44 / 0.50 / 0.56 |
| `center_offset`（头部中心横向偏移） | ≤0.10 | 替代双眼对称/眼位 |
| 内容留白 / 主体占比 / 顶部平切 | 见 4.3 | 与有人脸相同 |

`eye_center_y`、双侧对称、面部占比在 faceless 下**不参与评分**（记 `null`），
manifest 的 `metric_set` 标为 `head_contour`、`face_metrics_applied=false`；
报告必须写明用的是头部轮廓指标，**不得用面部分数冒充**。

全部指标都在**裁切后的 1024 图**上计算；构图类闸门（截断、留白、占比、平切）
以最终成品为准，原图指标另存 `raw` 供对照。

### 5.2 权重与归一化

方案定义五维权重：`face_quality 30 / composition 25 / style 20 / emotion 15 / anatomy 10`。
经两张正样本校准，可用两维改为 **`face_quality 40 / composition 60`**：
头像的取舍主要由构图决定，面部质量只作辅助。其余三维为 `null`，总分按可用权重归一化：

```
total = (40 × face_quality + 60 × composition) ÷ 100
```

`face_quality` 内部：`0.70 × 锐度 + 0.15 × 对称性 + 0.15 × 曝光`。

- 锐度：`100 × (ln(sharp+1) − ln11) ÷ (ln101 − ln11)`，夹到 0–100。
- 对称性：`100 × (1 − clamp(双眼高度差 ÷ 眼距 ÷ 0.18))`；无双眼时正脸记 60，
  侧脸记 70（单眼没有对称对象，不扣分）。
- 曝光：`100 × (1 − clamp((过曝比例 − 0.25) ÷ 0.50))`，过曝 = 脸框内灰度 ≥245 的占比。

`composition`（构图内部权重保持 `0.10/0.65/0.25`，第三项由「留边」改为「完整性」）：

```
100 × (0.10 × 脸占比得分 + 0.65 × 眼位得分 + 0.25 × 完整性得分) − 10 × clamp(cost ÷ 0.10)
脸占比得分 = 1 − |face_ratio − 0.62| ÷ 0.20
眼位得分   = 1 − |eye_center_y − 目标| ÷ 容差
             正脸：目标 0.26，容差 0.14
             侧脸：目标 0.40，容差 0.08（侧脸眼位是主判据）
完整性得分 = 0（顶部/左/右任一边判截断），否则 1
```

满出血模式下不再奖励「离边远」：主体贴边但完整得满分，被画面切断才扣。
`edge_margin` 仍记录在 manifest 里作参考，但**不再参与评分与硬门槛**。

校准依据（两张人工认可正样本）：

| 样本 | 定位 | 置信度 | 脸占比 | 眼位 | 留边 | 锐度 | 总分 |
|-|-|-|-|-|-|-|-|
| adam-4k | profile-head | 0.42 | 0.468 | 0.391 | 0.054 | 61.4 | 80.4 |
| luffy | profile-sclera | 0.52 | 0.409 | 0.409 | 0.029 | 55.9 | 74.4 |

两者眼位共同区间 0.391–0.409，故侧脸目标取 0.40、闸门取 0.32–0.46；
脸占比共同区间 0.409–0.468，旧下限 0.45 会误杀 luffy，故下限放到 0.34。

### 5.3 去重

1024 主图的 8×8 均值哈希，**只在同一轮、同一目标角度的候选之间**比较；
汉明距离 ≤3 判为重复，保留总分高的一张，另一张追加 `duplicate`。

**轮次键解析优先级（从高到低）**：

1. `--round <值>` → `round_source=cli`，最高优先级，覆盖下方全部推断；
2. `brief.round` 字段 → `round_source=brief`；
3. 文件名里的 `rN` / `roundN` → `round_source=filename`（如 `profile_left_v1_r1` → r1）；
4. 父目录名里的 `roundN` / `rN` → `round_source=dir`（如 `<订单目录>/round02` → r2）；
5. 都没有 → `__batch__` / `round_source=unknown`，见下方告警。

**命名约定**：`<angle>_v<variant>_r<round>`，如 `profile_left_v1_r1.jpg`。

- `v` 是**变体**：同一轮内不同写法的编号（v1–v4），不参与轮次识别；
- `r` 是**轮次**：第几次出图/补量（r1、r2…），去重按它分组；
- 同时出现 `v` 与 `r` 时以 `r` 为准；只有 `v` 没有 `r` 时**不拿 v 顶替**，
  继续退到目录名，目录名也没有才告警 unknown。
- 取值先规范化再比较：`1` / `r1` / `round_01` 统一为 `r1`，
  保证 `--round 1`、`brief.round="round_1"` 与文件名 `r1` 能对齐。
- **四级都取不到时不再静默把整批当同一轮**：控制台打印
  「WARNING: 轮次未知（N/M 张…），已按单轮处理，可能误判重复」，
  并在 manifest 每张图记 `round: "__batch__"`、`round_source: "unknown"`。
  仍继续执行（单轮回退最保守），但输出必须带明显告警，由人工确认或补 `--round`。
- **跨轮（含历史版本）互不判 duplicate，历史版本一律保留**；同一轮内不同目标
  角度也不是重复候选（左右侧脸/正脸各自独立）；同一轮的**不同变体**是重复候选，
  跨变体近似重复会被判 `duplicate`（例如同一 `r1` 下的 `v1` 与 `v2`）。
- 阈值由 4 收到 3：统一裁切后不同变体可能落到距离 4，属于误判，不是重复图。

### 5.4 缩图可读性

`0.6 × clamp((edge − 2) ÷ 12) + 0.4 × clamp((contrast − 20) ÷ 45)`，均取 96px 灰度图。

### 5.5 风格一致性闸门（粗指标）

在**裁切后的 1024 主图**上计算，不参与总分，只做通过/驳回：

| 指标 | 算法 | 当前门槛（`tv-anime-cel`） |
|-|-|-|
| `palette_colors` | 12 色中位切分量化，占比 ≥3.5% 计一个主色 | 5–13 |
| `saturation` | HSV 的 S 均值，仅统计 V ≥50 的像素 | 0.08–0.62 |
| `edge_density` | 512px 灰度 FIND_EDGES，响应 ≥40 的像素占比 | 0.015–0.10 |
| `background` | 四角中位数估背景色，统计接近该色的像素占比与明度 stddev；占比 ≥20% 且 std ≤10 为 flat，≥10% 且 ≤18 为 simple，否则 busy | stddev ≤20 且模式为 flat/simple |

- 门槛来自 `references/styles/<style-id>.json` 的 `gate`；未传 `--style` 时闸门为 `null`。
- 不同画风用不同档案，不能互相套阈值：`tv-anime-cel` 的 `edge_density` 上下限、
  高饱和主色数不适用于写实/皮套；写实皮套用 `realistic-armor`（色板 4–16、
  饱和度 0.015–0.20、边缘密度 0.025–0.12、背景 stddev ≤12）。
- `gate.applicable=false`（或无可用档案且角色无面部体系）时，该维度记
  `{"passed": null, "reasons": ["style_not_applicable"]}` 并写明原因；
  **null 不计为 PASS**，交付报告必须标「风格未验证」。
- 任一项不过 → `style_mismatch:<指标>`，直接 REJECT（"明显不在同一体系"）。
- 背景模式用「四角 patch 主色簇」估计（取与最多角一致的那一簇，打平优先上两角），
  不把四角平均：上两角白底、下两角深色主体时平均会折成灰，平涂底被误判 busy。
- **能力边界**：这是粗指标，不是风格 embedding。它能拦「无平涂主色/饱和度异常/没有线稿边缘/
  背景嘈杂」的明显异体系，不能证明画风精细一致；最终仍需人眼对比参考图。

### 5.6 角色特征闸门（人工清单）

清单文件由人填写，脚本只读不算：

```json
{
  "character_id": "satoshi",
  "items": {
    "hair_black_spiky": {"ok": true, "note": "两侧鬓发外炸"},
    "cheek_marks": {"ok": false, "note": "只有一侧有纹"}
  }
}
```

- 必填项来自角色卡 `checklist[].required`；文件里缺失或 `ok=false` → `character_missing:<key>`，REJECT。
- 未传 `--checklist` 时闸门为 `null`；风格/角色单的**交付**不接受 null，必须补做人工核对。
- 人工核对必须看 800px 放大图，同时查「鼻梁后方有没有第二只眼」；清单不能只凭缩略图勾选。
- 侧脸只查**可见侧**脸颊纹；远侧脸颊不可见，不因「另一侧没有」判缺失。

### 5.7 角度判定（侧脸双向 + 正脸启用 + 无脸）

| 项 | 规则 |
|-|-|
| 可见眼数 | 侧脸必须 = 1；正脸必须 = 2（且双眼都检出） |
| 瞳孔位置 | 朝左：≤0.42；朝右：≥0.58（单眼中心相对头框横向占比）；位置缺失不算通过 |
| 鼻尖侧 | 肤色重心 ≤0.44 判左、≥0.56 判右；与瞳孔位置冲突时判方向未定 |
| 朝向 | `facing` 必须等于期望角度的 `yaw`；方向未判定按 `angle_undetermined` 处理 |
| 眼位（侧脸） | 0.32 ≤ `eye_center_y` ≤ 0.46 |
| 眼位（正脸） | 0.24 ≤ `eye_center_y` ≤ 0.38（用正样本校准，不套侧脸区间） |
| 正脸对称 | 眼线倾斜 ≤0.07、双眼水平平衡 ≤0.08 |
| 无脸 | 面罩/高光偏移：朝左 ≤−0.08、朝右 ≥+0.08、正面 \|偏移\| ≤0.06；头盔轮廓对称性交叉校验 |
| 结论 | 全过 → 角度闸门 PASS；方向明确但方向/位置不符 → `angle_mismatch`；方向判不出 → `angle_undetermined` |

- **方向定义**：朝左 = 鼻尖在左、耳朵在右、可见眼在头框左半；
  朝右 = 鼻尖在右、耳朵在左、可见眼在头框右半。
  实测朝左样本 `pupil_edge=0.178`、朝右样本 `0.789`，不要把左右写反。
- **瞳孔极度偏一侧的例外**：`pupil_edge ≤0.12` 或 `≥0.88` 时方向已确定；若鼻尖侧
  不可用（肤色重心落在中线、背景嘈杂）不把明确方向判成未定；鼻尖与瞳孔冲突时仍判未定。
- 角度 id 必须来自 `references/angles.json` 且 `enabled=true`；`reserved` 项（3/4、仰俯）
  命中即 `angle_mismatch`，不再返回 `null`。
- `angle_undetermined` 表示「机器判不出方向」：暂停并请人工确认，不拿性别默认值顶替。
- 已知误判：远眼未被检出的 3/4 脸会被当成侧脸。人工 800px 复查仍不可省。
- 角度判定不进入总分，只作为闸门；总分仍为 `face_quality 40 + composition 60`。

### 5.8 截断判定（替代单一留边阈值）

在裁切后的主图上做像素启发式，区分「贴边但完整」与「被画面切掉」：

1. 四角 patch 主色簇估背景（取与最多角一致的一簇，打平优先上两角），
   曼哈顿距离 >50 的像素记为主体；若背景估不出，跳过本闸门。
2. **只在平涂背景可判定时生效**：内容分析 `reliable=true`（四角平涂且背景占比足够）
   才采信截断结论；写实场景/渐变/模糊背景的边圈不全是背景，贴边段会被误当主体
   （实测 adam 背景非平涂时左/右误判），此时记 `null`、不判死，也不拿它当证据。
3. 沿四条边分别统计主体像素的连续段：
   - `contact`：该边主体像素占比；
   - `max_run`：最长连续段占边长比例。
4. 只有 `max_run ≥ 0.10` **且** 向内 6% 处同一跨度仍有 ≥55% 为主体，才判该边截断。
   发丝尖、细装饰线这类窄接触不算截断。

| 边 | 处理 |
|-|-|
| 顶部 / 左 / 右 | 硬门槛：任一判截断 → `composition_bad:subject_cut`，REJECT |
| 底部 | 胸像正常裁切，只记录 `edge_contact`，不判死 |

- 主体轮廓触边但闭合、只是「接近边缘」→ 放行，不再扣分或驳回。
- 反例：帽子/头发被画布上沿横切、大块颜色直接顶到边 → 判截断。
- **平切头顶补充判据（硬性）**：极狐成品是「成品边缘全是白底、头在画面内部被
  横向切平」，贴边截断判据看不到。因此再检查**内容最高行**：最长连续段 ÷ 内容宽
  >22% 即判 `composition_bad:head_cut`；同时上/下留白与主体占比按 4.3 拦截。
  顶部平切是「在内容内部」发生的，必须用内容框而不是画布边来判断。
- **能力边界**：这是像素启发式，不是语义分割；深色头发与深色画框可能混淆，
  最终仍需人眼确认主体是否完整。

### 5.9 画布闸门（方形 + 无边框）

| 项 | 规则 |
|-|-|
| 方形 | 主图宽高差 ≤2px；裁切与所有导出档位必须为方形 |
| 无边框（圆形） | 512px 灰度上沿 720 条径向射线，在半径 0.40–0.505 倍边长内找最暗点；命中需同时满足：覆盖率 ≥55%、最大连续空档 ≤85°、半径中位数 ≥0.46、半径离散 ≤0.03、谷底亮度比两侧亮底暗 ≥28 且亮底 ≥180 → `canvas_bad:frame` |
| 无边框（直线/装饰框） | 256px 灰度上找深色连通域：跨度 ≥65% 宽高、填充率 ≤35%、贴三边以上 → `canvas_bad:frame` |
| 圆形遮罩 | 归入 `canvas_bad:frame`；流水线不再生成圆形预览 |
| 结论 | 任一不过 → REJECT；该闸门不进入总分 |

- 圆形检测允许圆环相对画布中心偏移、底部被肩线遮住或贴边裁掉一小段；
  实心暗背景不会命中（两侧亮底亮度不足）。
- 边框检测是粗检，只能拦「大跨度装饰圈/画框」；细小边框、半径明显小于 0.46 倍边长的
  内嵌圆环仍需人眼确认。
- 方形由裁切链路保证：`native_crop` 取正方形，`1024/2k/4k` 均保持 1:1。

---

## 6. 驳回原因码

| 码 | 含义 | 处理建议 |
|-|-|-|
| `no_face` | 未检出人脸（**内部信号，不作为交付原因码**） | 先走头部/轮廓回退通道，见下方映射 |
| `locate_failed` | 眼睛/肤色/头部轮廓回退都未定位到主体 | 这是定位失败，不是构图不合格；换变体重出或人工确认 |
| `multi_face` | 多人/多脸 | 直接弃用 |
| `low_conf` | 人脸可信度低 | 人工复核一次，通常弃用 |
| `composition_bad:face_ratio` | 脸占比越界 | 弃用；多为全身或大远景 |
| `composition_bad:eye_y` | 眼睛纵向位置越界 | 弃用；裁切已无法补偿 |
| `composition_bad:subject_cut` | 主体被画面边缘截断 | 弃用或重抽；贴边但完整不触发此码 |
| `composition_bad:head_cut` | 头顶被裁平（内容最高行连续段过宽） | 弃用；原图已丢像素，不能靠再裁还原 |
| `composition_bad:whitespace` | 成品上下留白越界 | 重裁或弃用；勿交付大留白小主体 |
| `composition_bad:subject_share` | 主体占画面比例越界 | 重裁或弃用 |
| `composition_bad:head_ratio` | 无脸模式头部轮廓占比越界 | 重裁或弃用（faced 不出现） |
| `composition_bad:centering` | 无脸模式头部居中偏移越界 | 重裁或弃用（faced 不出现） |
| `canvas_bad:square` | 画布不是 1:1 | 检查裁切/导出链路 |
| `canvas_bad:frame` | 画面有边框或装饰圈 | 弃用；重出时加无边框条款 |
| `thumb_unreadable` | 缩图不可读 | 弃用；缩小后糊成一团 |
| `face_blurry` | 面部发糊 | 弃用 |
| `duplicate` | 重复图（仅同一轮、同一目标角度的候选之间判定） | 保留高分那张；跨轮/不同角度不判重复 |

**定位原因码映射（`no_face` 不出现在 manifest / 报告）**：

```text
眼睛锚点失败
├─ 肤色回退成功           → 继续评分，detect.method = skin-fallback，不写 no_face
├─ 侧脸/无眼白头部回退成功  → 继续评分，detect.method = head-fallback，不写 no_face
├─ 无脸内容框回退成功       → 继续评分，detect.method = content-head，不写 no_face
└─ 全部回退失败            → locate_failed（定位失败，不是构图不合格）
```

`no_face` 只作为内部定位阶段信号；交付侧统一写 `locate_failed` 或对应的
`detect.method`，避免把「定位失败」误读成「构图不合格」。
| `open_failed` | 文件打不开 | 该次调用记为失败 |
| `style_mismatch:<指标>` | 风格粗指标越界 | 弃用；换风格措辞重出，别用「精致/8K」压 |
| `style_unverified` | 无风格档案或维度声明不适用，风格未验证 | **不计为通过**；补做人工风格核对后再交付 |
| `style_not_applicable` | 风格档案显式声明该维度不适用（记 null） | null 不是 PASS；报告标注「不适用 + 原因」 |
| `reference_unsupported` | 当前后端不暴露参考图入参（含后端不可用） | 退出码 4；未获用户同意不得退回文生图 |
| `reference_style_low` | 成品与参考图风格相似度过低 | 弃用或重出；身份相似度仍需人工清单 |
| `character_missing:<项>` | 人工清单必填项未通过 | 弃用；按下表补做或重出 |
| `angle_mismatch` | 方向明确但不是目标角度（含命中 3/4 等 reserved 项） | 弃用；先查鼻梁后方第二只眼 |
| `angle_undetermined` | 机器判不出朝向（瞳孔/鼻尖侧/面罩信号缺失或冲突） | 暂停，人工确认朝向；不要用默认值顶替 |
| `confirm_required` | brief 缺少 `confirm.confirmed=true` | 不生成、不评分；先走确认门写 confirm 块 |

---

## 7. 输出格式与放大

### 7.1 档位

| 档位 | 尺寸 | 来源 | 说明 |
|-|-|-|-|
| `1024` | 1024×1024 | 原生裁切缩放 | 默认交付，PNG |
| `native` | 原生裁切边长（约 840–1100） | 1920 源图直裁 | 归档母版，PNG |
| `2k` | 2048×2048 | 原生裁切 Lanczos 放大 | 高清展示位 |
| `4k` | 4096×4096 | 原生裁切 Lanczos 放大 | 仅大图展示，代价最大 |

flat 布局不单独导出 `native` 母版（它是裁切中间量，不落盘）；成品落
`NN_final.png`，可选档位落 `NN_2k.png` / `NN_4k.png`。

### 7.2 放大手段与代价（本机条件）

- **手段**：Pillow Lanczos 单次插值。
- **速度**：单张 4096 输出约 1–2 秒（纯 CPU）。
- **画质代价**：不新增任何细节，只做插值锐化过渡；从 ~1000px 裁切放大到 4096
  相当于 4 倍插值，放大倍率 >2 后观感明显变软。
- **放大后是否要重过检查**：
  - 构图类指标（`face_ratio`、`eye_center_y`、`cut_edges`）是比例/布尔量，
    放大前后不变，**无需重算**；
  - 可读性/清晰度**必须复检**：脚本对最大放大档做一次眼睛锚点复检并记录
    `recheck.eye_recheck_passed` 与全幅锐度；
  - 锐度按像素统计会随插值变化，只作参考，不能当「更清晰」的证据。
- **不可用**：Real-ESRGAN / waifu2x 等动漫超分**需额外依赖，本机不可用**
  （无 numpy/torch/onnx，pip 无网络）。替代路径：
  ① 只交付 `1024`/`2k`；② 在具备 GPU 的机器上离线超分后替换 `NN_2k.png`/`NN_4k.png`；
  ③ 改用可控生成后端原生出高分辨率（见方案附录 D）。

### 7.3 JPG 压缩（体积受限场景）

`--jpg --jpg-quality 88`，`optimize + progressive`。实测（紫发角色 8 张，1920 原生 → 裁切 → 导出）：

| 档位 | 质量 | 实测体积 | 适用 |
|-|-|-|-|
| 1024 | 92 | 约 160–180 KB | 几乎无损 |
| 1024 | 88（默认） | 127–145 KB | 常规站点头像、IM 头像 |
| 1024 | 85 | 约 125–130 KB | 限 150 KB 的接口 |
| 1024 | 80 | 约 105–115 KB | 限 120 KB 的接口 |
| 1024 | 75 | 约 95–100 KB | 极限压缩，可见轻微损失 |
| 2k | 88 | 328–390 KB | 高清展示位 |
| 4k | 88 | 约 1.0–1.3 MB | 不建议用于体积受限场景 |

经验值：质量 88 是「肉眼几乎无损」的起点；要压到 100 KB 以下用 1024 档 + 质量 75–80；
不要为了压体积把 4K 转 JPG，先降档位再降质量。以上为单角色样本实测，换风格后体积会浮动约 ±20%。

---

## 8. 命名、目录与 manifest

### 8.0 订单落盘（硬性要求，flat 结构）

每单一个短目录，层级压到最少。默认名 `<角色>-<NN>`（如 `satoshi-01`），
由 `rounds.py --new-order --root <根> --character <id>` 分配，重名自动递增；
只有用户明确要求唯一性时才加 `--stamp` 生成 `-MMDD-HHMM` 短时间戳。
订单根目录由用户指定，未指定时用 `<当前工作区>/avatar-orders/`：

```
<订单根目录>/satoshi-01/
├── brief.json
├── prompts.json
├── rounds.json
├── status.json
├── checklist.json
├── manifest.json           # 原图 raw_quality + 成品 final_gate 分列
├── contact_sheet.jpg
├── round01/                # 完全平铺，无任何子目录
│   ├── 01_src.jpg          # 原图
│   ├── 01_final.png        # 1024 成品
│   ├── 01_2k.png           # 仅用户要 2K 时
│   ├── 01_4k.png           # 仅用户要 4K 时
│   └── 01_q88.jpg          # 仅用户要 JPG 时
└── round02/
```

- **每轮一个目录、完全平铺**：不建 `raw/`、`out/`、`crop/`、`native/`、`png/2k/`、
  `export/` 等子目录；规格与格式只靠文件名后缀区分。
- 成品 `NN_final.png`，原图 `NN_src.<ext>`，同目录并列，`NN` 为轮内序号 01、02；
  `_final` 就是「哪张是最终」的唯一答案，`_src` 是生成原图。
- 订单目录不按角度拆分；角度只写在 `brief.json` / `manifest.json`。
- `%TEMP%` / `/tmp` 只能作生成中转，逐张生成后立即 `Move` 进 `roundNN/` 并改名
  `NN_src.<ext>`；流程结束前必须确认轮目录里没有缺图。
- 中断、超预算、脚本报错都不得回滚：已生成图留在 `roundNN/`，
  `status.json` 记录 `state`、`phase`、`raw_count`、`last_error`。
- 清理只能由用户明确要求后执行；`avatar_pipeline.py` 不删除任何订单文件。
- `status.json` 至少含：`order_id`、`state`、`phase`、`created_at`、
  `updated_at`、`raw_count`、`export_count`、`last_error`。
- 复盘时用 `roundNN/NN_src.*` + `manifest.json` 即可完整重现判定；
  原图缺失视为该单不合规，交付时要写明缺失原因。

**新旧结构差异（历史订单不迁移）**：

| | 旧（仅历史订单保留） | 新（默认 `flat`） |
|-|-|-|
| 订单目录名 | `<YYYYMMDD-HHMMSS>-<slug>`（可含角度） | `<角色>-<NN>`，重名递增 |
| 原图 | `<订单>/raw/round_XX/<angle>_v1_r1.jpg` | `<订单>/roundNN/NN_src.jpg` |
| 成品 | `<订单>/out/crop/<id>_1024.png` | `<订单>/roundNN/NN_final.png` |
| 放大/JPG | `out/export/png/2k`、`out/export/jpg` | 同目录 `NN_2k.png` / `NN_q88.jpg` |
| manifest / 联系表 | `<订单>/out/manifest.json`、`out/contact_sheet.jpg` | `<订单>/manifest.json`、`contact_sheet.jpg` |
| 报告 | `out/report.md` | 不生成（报告在对话里给） |

`--layout legacy` 只用于复跑旧订单；新订单一律用默认 `flat`。

### 8.1 目录（flat）

```
<订单目录>/
├── brief.json
├── prompts.json
├── rounds.json              # 轮次与累计预算台账
├── status.json
├── manifest.json
├── contact_sheet.jpg
└── roundNN/                 # 每轮一个目录，完全平铺
    ├── NN_src.<ext>
    ├── NN_final.png
    ├── NN_2k.png            # 可选
    ├── NN_4k.png            # 可选
    └── NN_q<q>.jpg          # 可选
```

`NN` = 轮内序号（01、02…），不是原图文件名；原图改名后 manifest 用
`r<round>-<NN>` 作为 `id`，`file` 记 `roundNN/NN_src.<ext>`。

### 8.2 manifest.json 字段

顶层：

| 字段 | 内容 |
|-|-|
| `meta.tool` / `meta.version` | 脚本标识与版本 |
| `meta.generated_at` | 本地时间 |
| `meta.backend` / `meta.reproducible` | `agent-side` / `false` |
| `meta.native_limit` | `1920` |
| `meta.formats` | 实际导出的档位 |
| `meta.jpg` | `{enabled, quality}` |
| `meta.upscale` | `{method, available, detail_added, note}` |
| `meta.layout` | `flat`（默认）/ `legacy`（仅复跑旧单） |
| `meta.budget` | `{limit, used, remaining, exceeded, source}`；`used` = 轮目录内原图实际张数 |
| `meta.style_profile` / `meta.character` / `meta.angle_matrix` | 本次使用的档案 id 与路径 |
| `meta.face_mode` | `faced` / `faceless`；决定指标集与角度判据 |
| `meta.generation` | `{backend, reference_input_supported, mode, fallback, strength, identity_unverified}` |
| `meta.reference_images` | 实际参与/声明参与的参考图 `[{path, role, exists}]` |
| `meta.checklist` | 人工核对清单是否提供 |
| `meta.order` | `{confirmed, confirmation_source, gender, gender_source, angle, angle_source, needs_gender_confirmation, count, count_capped}` |
| `brief` | 需求单原文（含 `confirm` 块） |
| `brief_effective` | 补默认值后的实际执行参数 |
| `capability` | 可用 / 不可用能力清单与说明 |
| `summary` | `total / pass / reject / best_total / contact_sheet` |
| `items` | 每张图的完整记录 |

flat 布局不再写 `report.md` 与 `budget.json`（预算写进 `manifest.meta.budget` 与
`status.json`）；`legacy` 布局保留旧的 `out/budget.json` / `out/report.md`。
超预算且未加 `--allow-over-budget` 时脚本直接退出（码 3），不评分不导出。

`items[]` 关键字段：

| 字段 | 说明 |
|-|-|
| `id` / `file` | 图标识与原文件名 |
| `verdict` | `PASS` / `REJECT` / `ERROR` |
| `total` | 归一化总分 |
| `face_mode` / `metric_set` / `face_metrics_applied` | 本张用的模式与指标集；faceless 为 `faceless` / `head_contour` / `false` |
| `metrics` | `face_ratio / eye_center_y / edge_margin / cut_edges / edge_contact / sharpness / crop_cost / eye_tilt / blown`；faceless 另记 `head_ratio / head_width_ratio / center_offset`，面部项为 `null` |
| `scores` | `face_quality`（含 `face_quality_parts`）、`composition`、style/emotion/anatomy 为 `null` |
| `gates` | 各闸门布尔值；不可用能力为 `null`；含 `style` / `character` / `angle` / `canvas` / `content` / `content_input` |
| `raw` | 原图侧记录：`content`（内容分析）、`content_gate`（原图内容闸门） |
| `raw_quality` | **原图生成质量分**（与交付无关）：`{scope:"generation", total, scores{face,composition,style,angle}, status:"qualified"/"unqualified"/"unreadable", threshold:60, reasons, note}`；不改变 final verdict，但交付前必须看它 |
| `final_gate` | 成品侧重跑：`{gate_scope:"final_crop", gate, content}`；交付校验以它为准，但**不能抵消 raw 不合格** |
| `style` | `{palette_colors, palette_top, saturation, edge_density, background_stddev, background_mode}` |
| `style_check` | `{style_id, passed, checks, reasons}`；未传风格档案为 `null` |
| `canvas` | `{width, height, aspect, square, frame_like, frame_bbox}` |
| `canvas_check` | `{passed, checks{square,no_frame}, reasons}` |
| `angle_check` | `{expected, enabled, metric_set, detected{visible_eyes,pupil_edge,skin_centroid,nose_side,facing,is_profile,visor_offset,contour_asym}, passed, checks, reason, direction_known}`；`reason` 为 `angle_mismatch` / `angle_undetermined`；faceless 时 `visible_eyes=null`、`metric_set=head_contour` |
| `reference_similarity` | `{style_similarity, identity_similarity, manual_identity_checklist[], per_reference[]}`；无 embedding 时 `identity_similarity=null` |
| `character_check` | `{character_id, passed, items[], missing[], reasons[]}`；未传清单为 `null` |
| `thumb` | 96px 可读性分数与分量 |
| `reasons` | 原因码数组 |
| `detect` | 定位方式、`conf`、`eye_source`、`profile`、`eye_y`、`face_box`、`skin_coverage` |
| `outputs` | 按格式分键（flat 文件名）：`png_1024` = `roundNN/NN_final.png`；可选 `png_2k` = `NN_2k.png`、`png_4k` = `NN_4k.png`、`jpg_1024_q88` = `NN_q88.jpg` |
| `upscale` | `applied / method / tiers{tier:{size,factor,detail_added}}` |
| `recheck` | 放大后复检：构图不变说明、眼睛复检、全幅锐度 |

---

## 9. 能力矩阵（本机核实）

| 能力 | 状态 | 说明 |
|-|-|-|
| 生成（1920 方形） | 可用 | Agent 侧 `GenerateImage` |
| 人脸定位（眼睛锚点 + 皮肤回退 + 侧脸单眼/无眼白头部） | 可用 | 纯 Pillow 启发式；侧脸走眼白 + 头部轮廓，浅色皮肤无眼白时用头部比例估眼线 |
| 无脸/皮套定位（内容框头部轮廓） | 可用（平涂底） | `content-head`；非平涂底不可靠时记 `locate_failed`，不猜 |
| 裁切补偿 | 可用 | 三档目标占比 + 全幅候选；faceless 用头宽 0.44/0.50/0.56 + 全幅 |
| 锐度 / 缩图可读性 / 去重 | 可用 | Pillow + 均值哈希 |
| 风格粗指标 | 可用（粗筛） | 主色数/饱和度/边缘密度/背景平涂度；不是 embedding；赛璐璐与写实各用各的档案 |
| 角色特征核对 | 可用（人工） | 清单勾选，缺必填项不放行；无脸角色用轮廓类清单 |
| 角度判定 | 可用（侧脸双向 + 正脸；无脸走面罩/轮廓） | 正脸：双眼 + eye_y 0.24–0.38 + 倾斜/平衡；3/4、仰俯仍 reserved |
| 截断判定 | 可用（仅平涂底） | 贴边连续段 + 向内延续；非平涂底（写实/渐变/模糊背景）记 `null`，不判死 |
| 方画布 / 边框粗检 | 可用（粗筛） | 宽高比 + 径向暗环 + 大跨度低填充深色连通域；细小/内嵌圆环需人工确认 |
| PNG / JPG 导出 | 可用 | 含 2K/4K 插值放大 |
| 参考图入参（当前宿主） | **不可用** | `trae-generateimage` 只有 `prompt`/`image_size`；`backends.json` 登记为 `supported=false`，默认 `fallback=fail` 退出 4 |
| 参考图风格相似度 | 可用（像素统计） | 色板直方图/饱和度/边缘密度；只说明同体系 |
| 身份相似度（embedding） | 不可用 | 记 `null` + 人工清单，不拿色板冒充身份 |
| 动漫超分（Real-ESRGAN 等） | 不可用 / 需额外依赖 | 无 numpy/torch，pip 无网络 |
| OCR 文字检测 | 不可用 | 闸门 `null` |
| NSFW 检测 | 不可用 | 闸门 `null` |
| 情绪四分类 | 不可用 | 闸门 `null` |
| 风格 embedding | 不可用 | 精确风格相似度算不了；5.5 的粗指标只做粗筛 |
| 解剖/多指检查 | 不可用 | 闸门 `null` |
| 身份一致性 / 可复现 | 不可用 | 生成端无 seed / 参考图 |
