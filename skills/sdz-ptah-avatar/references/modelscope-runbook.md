# ModelScope API 出图执行手册（给执行智能体）

> 适用对象：能在用户 Windows 电脑上**打开终端执行命令**的智能体（TRAE、Agnes、Claude Code、Codex 等）。
> 目标：通过魔搭 API-Inference 生成动漫头像，全程不需要用户在魔搭网页上手工上传和填写。
> 本手册只覆盖 `modelscope-qwen-edit` / `modelscope-qwen-edit-2511` 两个在线后端；通用规则以 `SKILL.md` 为准，两者冲突时以 `SKILL.md` 为准。
>
> **提示词不由人手写。** 一律用 `scripts/compile_prompt.py` 现场编译（第 4 节 ③），
> 规则来源是 `references/protocol.md` §2.6.1 / §2.8 / §2.9。本手册只保留命令，
> 不保存提示词正文——正文会随角色卡与模板版本变化，抄在文档里必然过期。

---

## 0. 必须遵守的规则

1. **token 规则（最重要）**
   - 适配器实际按顺序读 `MODELSCOPE_TOKEN` → `MODELSCOPE_API_KEY` → `MODELSCOPE_SDK_TOKEN`（见 `backends/modelscope.py`），三者任一存在即可；推荐只用第一个。**不得**在聊天里向用户索要 token，**不得**打印、回显、写入任何文件（brief、提示词文件、日志、脚本、提交记录都不行）。
   - 只允许用下面这条命令检查是否已设置，它只输出 set / missing，不回显值：
     ```powershell
     if ($env:MODELSCOPE_TOKEN -or $env:MODELSCOPE_API_KEY -or $env:MODELSCOPE_SDK_TOKEN) { "set" } else { "missing" }
     ```
   - 结果是 `missing` 时：停下，请用户按第 1 节自己设置，然后**重启终端**再继续。
2. **确认门**：没有用户的明确确认（确认 / 生成 / confirm），不运行第 4 节第 ② 步。
3. **外发同意**：在线后端会把参考图发给魔搭。运行 `--egress-consent` 前必须告诉用户这一点，并得到同意。
4. **预算**：每单默认 10 次调用，`generate.py` 会预检。退出码 5 时停下问用户，不要自己加 `--allow-over-budget`。
5. **不换后端、不退回文生图**：任何退出码 4 都是停下报告，不要自行改用别的后端或去掉参考图。
6. **不用公共图床**：参考图由适配器以 base64 直接发送。如果服务不接受（见第 6 节），停下报告，**不要**把参考图传到 freeimage.host 之类的公共图床。
7. **不手工记账**：`generate.py` 会自动写 `rounds.json` / `generation.json` / `prompts.json` / `status.json`，不要再运行 `rounds.py --record`。
8. **不删除、不覆盖**订单目录里的已有文件；清理只能由用户决定。
9. **AIGC 标识**：生成图里有魔搭写入的隐式标识元数据，不要删除或篡改。
10. **许可提醒**：ModelScope 免费推理服务条款为非商用。交付时要告诉用户。

---

## 1. 一次性准备（由用户本人完成）

| 项 | 做法 |
|-|-|
| 魔搭账号 | 完成实名认证并绑定阿里云 |
| 访问令牌 | 在 modelscope.cn/my/myaccesstoken 选「新建访问令牌」→ **仅读权限令牌**。如果第一次出图报 401/403，再换「细粒度令牌」，只勾推理调用相关权限。不要用写权限或管理员权限令牌 |
| 环境变量 | 用户在**自己的** PowerShell 里执行（智能体不要代为执行，因为命令里含明文 token）：<br>`[Environment]::SetEnvironmentVariable("MODELSCOPE_TOKEN", "<令牌>", "User")`<br>设置后**重启终端 / IDE / 智能体**，新开的终端才会读到 |
| 撤销 | 不用了就删掉：`[Environment]::SetEnvironmentVariable("MODELSCOPE_TOKEN", $null, "User")`，并在魔搭页面删除令牌 |
| 运行环境 | Python 3（命令用 `py -3`），已安装 Pillow |

> 说明：用户级环境变量以明文保存在本机用户配置里，本机其他程序也能读到。介意的话可以只在单个终端里临时设置（`$env:MODELSCOPE_TOKEN = "..."`），但那样只有那一个终端窗口能用。

---

## 2. 固定路径

```powershell
$skill = "F:\zxc\Project\sdz-ptah\skills\sdz-ptah-avatar"
$root  = "F:\zxc\Project\sdzdrccc\头像\小智\api-orders"   # 订单根目录；其他角色可换
```

一个角色一个订单目录：`$root\<角色 slug>`。图片全部平铺在里面，不建子目录。

---

## 3. 自检（不消耗额度）

```powershell
if ($env:MODELSCOPE_TOKEN -or $env:MODELSCOPE_API_KEY -or $env:MODELSCOPE_SDK_TOKEN) { "set" } else { "missing" }
cd $skill
py -3 -m backends probe modelscope-qwen-edit
```

- 期望看到 `"ok": true` 和 `"reachable": true`。
- 未设 token 时的实际输出（本机 2026-11-24 实测，退出码仍是 **0**，不要按退出码判断）：
  ```json
  { "ok": false, "reachable": true,
    "detail": "服务可达，但未设置 token（环境变量 MODELSCOPE_TOKEN / MODELSCOPE_API_KEY / MODELSCOPE_SDK_TOKEN）" }
  ```
  `ok: false` 且 detail 写「未设置 token」：回到第 1 节。
- `reachable: false`：网络不通，停下报告。
- `probe` **不会**验证 token 是否有效；有效性要到第一次出图才知道。
- `probe` 只请求 `GET /v1/models`（不带鉴权、15 秒超时），不消耗推理额度。

---

## 4. 出图流程

### ① 新建订单并写需求单

```powershell
py -3 "$skill\scripts\rounds.py" --new-order --root $root --character <角色slug>
```

记下输出里的 `order_id`，然后在 `$root\<角色slug>\brief.json` 写最小需求单（UTF-8）：

```json
{
  "order_id": "<上一步的 order_id>",
  "character_id": "<角色slug>",
  "gender": "male",
  "angle": "front",
  "angle_source": "user",
  "count": 1,
  "budget": 10,
  "style_id": "tv-anime-cel"
}
```

- `gender`：male / female / unspecified。`angle`：front / profile_left / profile_right。
- 首次测试 `count` 用 1，确认能出图后再加。
- **不要手写 `generation` 块**：第 ② 步的 `--stages` 会把它写进 `brief.json`
  （`backend` / `stages` / `mode` / `reference_images` / `seed` / `egress_consent`）。
  只改 brief 不走 ② 时，后端会落回默认的 `agnes-aigc-img2img`——它没有负向通道，
  编译出来的 ModelScope 负向词会被丢掉，等于白改。

### ② 确认（需要用户明确同意）

先向用户列出：后端（ModelScope 在线 Qwen-Image-Edit）、张数、参考图路径、**参考图会发往魔搭**、**免费推理非商用**，得到「确认生成」后执行：

```powershell
$order = "$root\<角色slug>"
py -3 "$skill\scripts\confirm_order.py" --brief "$order\brief.json" `
  --confirm "确认生成" `
  --reference "<参考图路径>" `
  --stages "single:modelscope-qwen-edit@steps=30" `
  --seed 100 --egress-consent
```

- 期望退出码 0，最后一行包含 `stages: single:modelscope-qwen-edit  seed=100  egress_consent=True`。
- 想用 2511 版本作对比：把后端换成 `modelscope-qwen-edit-2511`。
- 可调分段参数（写在 `@` 后，用 `;` 分隔）：`steps`、`guidance`、`seed`、`negative`。

### ③ 写提示词（agent 编写，按 prompt-spec-v1）

**现行做法**：提示词由执行 agent 编写，颜色从参考图实测，完整规则见
`references/prompt-writing-spec.md`。步骤：

1. 取色：`py -3 "$skill\scripts\ref_palette.py" --image <参考图> --skin 皮肤=<框> --region <部位>=<框> --json "$order\palette.json"`
2. 按规范第 3 节七段顺序写正向段，按第 5 节写负向段（MiniMax 负向必须留空）。
3. 存成 `$order\prompt-input.json`（字段见规范第 7 节）。
4. 自查：`py -3 "$skill\scripts\check_prompt.py" --prompt-file "$order\prompt-input.json" --backend <后端 id>`，退出码 0 才进入第 ④ 步。

以下编译器用法保留为**可选**：角色有角色卡时，可以先用它起草，再按规范改颜色与措辞。

```powershell
py -3 "$skill\scripts\compile_prompt.py" `
  --character "$skill\references\characters\<角色slug>.json" `
  --style "$skill\references\styles\tv-anime-cel.json" `
  --backend modelscope-qwen-edit `
  --angle front `
  --identity full `
  --expression "<表情>" --expression-en "<英文对照>" `
  --out "$order\prompts.compiled.json"
```

- `--backends` 缺省已优先读 `references/backends.v2.json`（登记了 modelscope 两个后端）。
  **不要**手动指向 `backends.json`：1.0 注册表里没有 modelscope 条目，会把「支持负向词」
  误判成不支持，静默改成 inline。需要锁定时写全路径
  `--backends "$skill\references\backends.v2.json"`。
- `--identity full`：ModelScope 的 Qwen-Image-Edit 是编辑类模型，实测复述全量身份更稳。
  协议默认（`auto` → reference 模式用 `compact`）适用于纯参考图宿主；这里显式覆盖。
  角色卡没有 `prompt_block_en` 时 `full` 会直接报错，改回 `compact`。
- 期望输出：
  ```
  prompt_version : <角色>-<风格>-skin-anchor-v1
  prompt_sha256  : <64 位>
  backend        : modelscope-qwen-edit
  negative_mode  : separate（依据 capabilities.negative_prompt）
  skin_anchor    : base #E9C1AB H21 S27 V91 Lab[...] | shadow ... | cap ...
  ```
- 产出 JSON 含 `prompt_zh` / `prompt_en` / `negative` / `prompt_version` /
  `prompt_sha256` / `style_version` / `character_version` / `skin_anchor` /
  `negative_mode`，`generate.py` 会把这些原样记进 `prompts.json`。
- 肤色必须落成 hex + 色相/饱和度/明度，且底色/暗部/高光三档齐全；出现
  `小麦色` / `暖调皮肤` / `日晒感` 这类词、或用 `…` 省略，`generate.py` 在第 ④ 步
  直接拒绝（退出码 2）。**旧的 `prompts.next.json`（含「暖调小麦色皮肤」）已被这条规则拦下，
  不要再拿来用。**
- 只有没有角色卡的角色（如孙悟空 / `goku`）才退回手写：「正向 … 反向 …」txt 转 JSON：
  ```powershell
  py -3 -c "import json,sys; t=open(sys.argv[1],encoding='utf-8').read(); p,n=t.split('反向',1); json.dump({'prompt_zh':p.replace('正向','',1).strip(),'negative':n.strip()},open(sys.argv[2],'w',encoding='utf-8'),ensure_ascii=False,indent=2)" "<提示词.txt>" "$order\prompts.next.json"
  ```
  转完仍要过第 ④ 步的审计；手写稿没有 `prompt_version`，可复现性差一档。

### ④ 预检（不调用、不扣额度）

```powershell
py -3 "$skill\scripts\generate.py" --order $order --prompt-file "$order\prompts.compiled.json" --dry-run
```

期望退出码 0、`errors` 为空、`calls_needed` 等于张数、`warnings` 为空。
预检同时会跑提示词审计：有省略号或被禁的模糊色词时直接退出码 2，不会走到调用。

### ⑤ 正式出图

```powershell
py -3 "$skill\scripts\generate.py" --order $order --prompt-file "$order\prompts.compiled.json"
```

- 每张通常要等几十秒到几分钟，中途没有进度输出，属于正常现象，不要中断。
- 成功后订单目录里会出现 `<角色slug>-avatar-<N>-src.png`（或 `.jpg`）。
- 退出码不是 0 时按第 5 节处理。

### ⑥ 裁切、评分、导出

```powershell
py -3 "$skill\scripts\avatar_pipeline.py" --raw $order --brief "$order\brief.json" `
  --formats 1024 --style "$skill\references\styles\tv-anime-cel.json" --budget 10
```

- 产出 `<角色slug>-avatar-<N>.png`（1024 成品）、联系表和 `manifest.json`。
- **已知误判**：白底头像的风格闸门会失效，黑色瞳孔可能让正脸被判成侧脸（路线图 M2-1 / M2-3）。所以评分出现 `REJECT` 时，不要据此重抽，以人工看图为准。

#### ⑥b 肤色复测（必做，不出图）

提示词里的三档色锚点要靠实测收口（协议 §2.10）：

```powershell
py -3 "$skill\scripts\skin_check.py" `
  --image "$order\<角色slug>-avatar-1-src.png" `
  --character "$skill\references\characters\<角色slug>.json" `
  --order $order
```

| 退出码 | 含义 | 动作 |
|-|-|-|
| 0 | PASS（ΔE ≤6、色相差 ≤4°、饱和度差 ≤6） | 交付 |
| 3 | NEAR（ΔE ≤12 等） | 可交付，报告里注明偏差方向 |
| 6 | FAIL | 改提示词/换 seed 再出一轮；不要靠后处理调色 |
| 4 | 无法测量（未定位到脸） | 用 `--box x0,y0,x1,y1` 手动给脸框，或人工判断 |
| 7 | STOP | 连续两轮同一指标没收敛：**停止调词**，转后处理或换底模 |

`--order` 会把每轮结果追加到 `skin-history.json` 并做两轮停判。
退出码 7 时不要在提示词上继续加形容词，那是在赌随机。同一提示词多次抽样的
b\* 方差本身就有 10 以上，抽卡不是调色手段（协议 §2.11）。

### ⑦ 交付与回访

向用户报告（见第 7 节），问「这版满意吗？」，再按回答记录：

```powershell
py -3 "$skill\scripts\rounds.py" --order $order --set-satisfaction --round 1 --satisfaction satisfied
```

不满意时按 `SKILL.md` 第 8 步：选改进方向后，用 `generate.py --directions <方向id>` 重出。

---

## 5. 退出码处理

### `generate.py`

| 码 | 含义 | 执行智能体怎么做 |
|-|-|-|
| 0 | 成功，已落盘并记账 | 进入第 ⑥ 步 |
| 2 | 输入错误，或有未完成的生成 | 读提示。有未完成生成时问用户：`--resume` 继续，还是 `--discard-pending` 放弃 |
| 4 | 未确认 / 缺 token / token 无效（401/403）/ 未同意外发 / 参数超限 | **停下报告**原文最后几行。401/403：请用户换细粒度令牌或检查实名、阿里云绑定 |
| 5 | 预算不足 | 停下问用户是否追加 |
| 8 | 服务故障：任务失败（如内容审核）、超时、连不上、HTTP 400 | 先看错误原文，按第 6 节判断。临时故障（超时、连不上、429）可以等 1–2 分钟后执行一次 `--resume`；其他情况停下报告 |
| 9 | 不会出现（只用于宿主侧后端） | — |

续跑命令：

```powershell
py -3 "$skill\scripts\generate.py" --order $order --resume
```

### `confirm_order.py`

0 成功；2 输入或参考图无效、分段写法错误、后端未登记（读提示修正后重跑）。

### `avatar_pipeline.py`

0 成功；2 目录里没有图；3 超预算；4 未确认或后端能力核对不通过。

---

## 6. 首次真实调用需要观察的三件事

以下三点还没在真实服务上验证过，第一次出图时请特别留意，并写进报告：

| # | 待确认 | 怎么判断 |
|-|-|-|
| 1 | 服务是否接受 base64 格式的参考图 | 第 ⑤ 步报 HTTP 400 且错误里提到 `image_url` 或图片格式 → **不接受**。停下报告，不要改用公共图床 |
| 2 | 生成图有没有用上参考图 | 看图：帽子、白领、黑内搭、黄纽扣、绿背带、脸颊闪电纹是否和参考图一致。如果像是完全无视参考图，写进报告 |
| 3 | 网页端的「参考强度」API 是否支持 | 目前没传这个参数。不要自己往请求里加字段，只在报告里记下出图效果 |

实际请求形状（`backends/modelscope.py` 的 `_payload`，已按代码核对）：
`POST {base}/v1/images/generations`，头 `Authorization: Bearer <token>` +
`X-ModelScope-Async-Mode: true`；体 `model` / `prompt` / `negative_prompt`（有才发）/
`size`（`1024x1024`）/ `seed` / `steps` / `guidance`（有才发）/ `image_url`（base64 data URI 数组）。
返回 `task_id` 后每 5 秒 `GET /v1/tasks/{task_id}`、带 `X-ModelScope-Task-Type: image_generation`，
600 秒超时；超时不自动重提（避免重复扣额度），交 `--resume`。
画布固定 1024 方图——注册表里的 `1328` 只表示模型可接受，`generate.py` 不传。

### 6.1 已登记的两个在线生图后端（2026-11-24）

网页端实测过的两个模型已由维护者登记进 `backends.v2.json`，可直接选用：

| 后端 id | 模型 | 许可 | 魔粒 | 特殊限制 |
|-|-|-|-|-|
| `modelscope-qwen-image-2.1` | `Qwen/Qwen-Image-2.1` | Qwen 研究许可（商用需看条款） | 1 | 最多 10 张参考图；负向词可用 |
| `modelscope-minimax-h3-image` | `MiniMax/MiniMax-H3-Image` | 社区许可（年营收超 100 万美元或提供托管服务需书面授权） | 2 | **无负向通道**：brief 里带负向词会被静默丢弃，`check()` 返回 warning 提示改写为描述句；steps / guidance / size 均不发送（传了会 HTTP 400） |

- 两者都标 `verified=false`：**真实 token 出图尚未验证**，第一次用按第 6 节三件事观察并写进报告。
- `probe` 会显示「模型不在公开列表中」警告——生图模型本就不在对话清单 `/v1/models` 里，属已知误报，不是故障（详见 `ai-anime-avatar/review/魔搭API可用模型清单.md` 第 5 节）。
- 用 MiniMax 时提示词编译要把排除项写成正向描述句；负向词写了也不生效，别误以为闸门已过滤。
- 两个模型都非商用许可叠加非商用免费推理服务，**只作内部测试**，不作为对外交付路线。

---

## 7. 报告模板

每次运行结束，在订单目录写 `run-report.md`（不含 token），并在聊天里给用户同样内容：

```markdown
# ModelScope 出图报告 <order_id>

- 时间：<开始–结束>
- 后端 / 模型：modelscope-qwen-edit / Qwen/Qwen-Image-Edit
- 参数：张数 <n>，seed <seed>，steps <steps>，参考图 <文件名>
- 各步退出码：确认 <>，预检 <>，生成 <>，评分 <>
- 产出文件：<列表>
- 预算：已用 <used> / 上限 <limit>
- 首次调用观察（第 6 节）：
  1. base64 参考图：接受 / 不接受（错误原文）
  2. 参考图是否生效：<观察>
  3. 其他：<>
- 评分结果（仅供参考）：<verdict 与原因码>
- 失败时：错误原文最后 10 行
- 提醒：免费推理服务非商用；图片含魔搭 AIGC 隐式标识，未删除
```

不要修改 `references\backends.v2.json` 的 `verified` 字段和方案文档；这两处由维护者根据报告更新。

---

## 8. 已准备好的测试订单

### 8.1 小智（satoshi）

| 项 | 值 |
|-|-|
| 订单目录 | `F:\zxc\Project\sdzdrccc\头像\小智\api-orders\satoshi` |
| order_id | `20261124-000312`（`brief.json` 已写好：正脸、1 张、预算 10） |
| 提示词 | `prompts.compiled.json`（已按第 ③ 步编译：`modelscope-qwen-edit` / `front` / `--identity full` / 表情「闭嘴微笑」，`negative_mode=separate`，底色 `#E9C1AB`、暗部 `#C99A93`、高光上限 `#F2DCCB`）。同目录旧的 `prompts.next.json` 含「暖调小麦色皮肤」，已被审计拦下，**不要用** |
| 参考图 | `F:\zxc\Project\sdzdrccc\头像\小智\refs\参考图.jpg` |
| 当前进度 | 第 ① ③ 步已完成（提示词已编译，预检已在临时副本验证通过：`errors` 为空、`calls_needed=1`），从第 ② 步「确认」开始；确认前仍需征得用户同意 |
| 一键脚本 | 同目录 `run.ps1` 按顺序执行 ②④⑤⑥，输出写入 `run-log.txt`。它会在缺 token 时**交互式**要求输入，所以执行智能体不要用它；请按第 4 节逐条执行。**注意 `run.ps1` 第 31/34 行仍指向旧的 `prompts.next.json`（会被审计拒绝），手工跑之前把那两处改成 `prompts.compiled.json`** |

本单的两条命令可直接粘贴（已按真实路径填好，无占位符）：

```powershell
$skill = "F:\zxc\Project\sdz-ptah\skills\sdz-ptah-avatar"
$order = "F:\zxc\Project\sdzdrccc\头像\小智\api-orders\satoshi"

# ③ 重新编译（换了表情/角度/后端时都要重跑，会刷新 prompt_sha256）
py -3 "$skill\scripts\compile_prompt.py" `
  --character "$skill\references\characters\satoshi.json" `
  --style "$skill\references\styles\tv-anime-cel.json" `
  --backend modelscope-qwen-edit --angle front --identity full `
  --expression "闭嘴微笑" --expression-en "closed-mouth smile" `
  --out "$order\prompts.compiled.json"

# ② 确认（需先拿到用户同意；会归档参考图并记录 sha256）
py -3 "$skill\scripts\confirm_order.py" --brief "$order\brief.json" `
  --confirm "确认生成" `
  --reference "F:\zxc\Project\sdzdrccc\头像\小智\refs\参考图.jpg" `
  --stages "single:modelscope-qwen-edit@steps=30" `
  --seed 100 --egress-consent
```

### 8.2 孙悟空（goku）

| 项 | 值 |
|-|-|
| 订单目录 | `F:\zxc\Project\sdzdrccc\头像\孙悟空\api-orders\goku` |
| order_id | `20261124-003300`（`brief.json` 已写好：男、正脸、1 张、预算 10、画风 tv-anime-cel） |
| 提示词 | `prompts.next.json`（手写稿，第 ③ 步可跳过）。**goku 没有角色卡，编译器不覆盖**：`references/characters/` 下只有 `satoshi` 与 `fox-armor`。手写稿已通过当前审计（无模糊色词、无省略号），但没有 `prompt_version`，可复现性弱一档；要纳入编译器需先建 `characters/goku.json`（含 `skin_spec` 三档 + `prompt_block_en`） |
| 参考图 | `F:\zxc\Project\sdzdrccc\头像\孙悟空\Image-00-20-06.png`（1080×1080 PNG，正脸胸像、白底，头顶发尖被画面裁掉） |
| 当前进度 | 第 ① ③ 步已完成，从第 ② 步「确认」开始；确认前仍需征得用户同意 |

确认命令（第 ② 步，得到用户同意后执行）：

```powershell
$order = "F:\zxc\Project\sdzdrccc\头像\孙悟空\api-orders\goku"
py -3 "$skill\scripts\confirm_order.py" --brief "$order\brief.json" `
  --confirm "确认生成" `
  --reference "F:\zxc\Project\sdzdrccc\头像\孙悟空\Image-00-20-06.png" `
  --stages "single:modelscope-qwen-edit@steps=30" `
  --seed 100 --egress-consent
```

这一单要额外观察并写进报告：
- 参考图头顶发尖被裁掉了，提示词要求「镜头略微拉远、发尖完整入镜」。看生成图是否补全了发尖，还是照原样裁切。
- 有没有画成金发、超级赛亚人或发光气场（负向词已排除）。
- 评分脚本的已知误判（白底、黑瞳）对这张同样适用，`REJECT` 不作为重抽依据。

### 8.3 共同注意

小智（satoshi）和孙悟空（goku）都是第三方 IP 角色，只作内部测试，生成图不要提交进公开仓库。两单先后执行，**不要并发**（ModelScope 适配器按单并发设计）。
