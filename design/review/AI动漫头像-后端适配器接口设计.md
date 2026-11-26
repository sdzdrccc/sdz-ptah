# AI 动漫头像 · 后端适配器接口设计

| 项目 | 内容 |
|-|-|
| 日期 | 2026-11-23 |
| 对应方案 | `outputs\AI动漫头像生成方案.md` 第 2.9 节（路线）、4.2 节 S2（后端适配器）、4.5 节（台账） |
| 接口版本 | `INTERFACE_VERSION = 0.1.0`；注册表 `generation-backends/2.0` |
| 代码位置 | `skills\sdz-ptah-avatar\backends\`（适配器包）、`scripts\generate.py`（生成入口） |
| 状态 | 已接入现有流水线（2026-11-23，见第 10 节）；已用 mock 后端 + 模拟 ComfyUI 服务端到端测试，**尚未连真实 ComfyUI / GPU** |

---

## 1. 要解决的问题

- **现状**：生成由宿主（Agnes / TRAE）在 Python 之外执行，`avatar_pipeline.py` 只负责能力检测和评分，manifest 里后端记为 `agent-side`。
- **新需求**：
  - 项目默认路线是本地 ComfyUI（Animagine / Neta Lumina / Qwen-Image-Edit），需要 Python **直接调用**。
  - 两段式（身份步 → 画风步）需要把第一段的输出自动交给第二段。
  - 宿主后端仍要保留为维护者路线，并与本地后端共用同一套校验、台账和 manifest 字段。
- **必须保持的现有规则**（来自 `SKILL.md` / `backends.json` 1.0）：
  - 不得静默回退：能力不满足时退出码 4，不得自动换后端或退回文生图。
  - 预算按订单累计，超额先问用户。
  - 订单目录平铺，命名 `<prefix>-avatar-<N>-src.<ext>`，编号跨订单连续。
  - 缺模型的闸门记 `null`，`null` 不算通过。

---

## 2. 两类适配器

| 类型 | `kind` | 谁执行生成 | 代表 | 流程 |
|-|-|-|-|-|
| 可执行适配器 | `executable` | Python 直接调用 | ComfyUI 本地、Mock，以后的 HTTP API | `check → probe → 上传参考图 → 提交 → 轮询 → 下载 → 结果` |
| 宿主侧适配器 | `agent_side` | 宿主智能体 | Agnes `image_to_image`、TRAE `GenerateImage` | `check → 写调用单 <prefix>-call-<N>.json → 抛 NeedsAgent（退出码 9）→ 宿主执行并放回图片 → 再次调用时 ingest` |

```
             ┌──────────── GenerationRequest ────────────┐
brief.json ─►│ prompt_zh / prompt_en / negative / refs /  │
             │ seed / steps / guidance / denoise / stage  │
             └──────────────────┬─────────────────────────┘
                                ▼
                     runner.run(stages)          ← 预算按 calls 预检
                 ┌──────────────┴──────────────┐
        Adapter.check()（统一校验，失败退出码 4）
                 │                             │
     executable ▼                   agent_side ▼
  ComfyUI HTTP / Mock          写 <prefix>-call-<N>.json
                 │              → NeedsAgent（退出码 9）
                 │              → 宿主执行 → 图片放回订单目录
                 ▼                             ▼
          GenerationResult（统一 manifest 字段）
                                ▼
            <prefix>-avatar-<N>-src.png → avatar_pipeline.py 评分（不变）
```

---

## 3. 数据契约

### 3.1 请求 `GenerationRequest`

| 字段 | 类型 | 说明 | 对应现有字段 |
|-|-|-|-|
| `order_id` / `prefix` / `start_index` | str / str / int | 订单、文件前缀、本次第一张编号 | `brief.order_id`、`--prefix`、跨订单连续编号 |
| `prompt_zh` / `prompt_en` | str | 中文原文 / 英文或 tag 串 | `original_prompt` / `enhanced_prompt` |
| `negative` | str \| None | 负向词；后端不支持时只发警告，由提示词编译层改写 | 新增 |
| `references` | [ReferenceImage] | `path` / `role`（identity / style / outfit / pose）/ `sha256` | `generation.reference_images` |
| `init_image` | str \| None | 两段式第二段输入，由 runner 自动填写 | 新增 |
| `denoise` | float \| None | img2img 强度 | 旧字段 `strength` |
| `seed` / `steps` / `guidance` | | 后端不支持 seed 时忽略并警告 | 新增 |
| `width` / `height` / `count` | int | 默认 1024 / 1024 / 1 | `size`、`count` |
| `stage` | str | `single` / `identity` / `style` | 新增 |
| `egress_consent` | bool | 同意把参考图发往远端服务 | 新增（对应方案第 8 节第 1 条） |

### 3.2 结果 `GenerationResult`（写入 manifest）

| 字段 | 说明 |
|-|-|
| `adapter_id` / `kind` / `stage` | 哪个适配器、哪一段 |
| `images[]` | `path` / `index` / `seed` / `width` / `height` / `sha256` |
| `calls` | 实际调用次数（预算按它记账） |
| `params` | 实际发给后端的参数，**不含密钥** |
| `model` | `name` / `version` / `quant` / `sha256` |
| `reproducible` + `reproducible_reason` | 见第 7 节 |
| `license` | 调用时的许可快照：`id` / `commercial_use` / `notes` |
| `env` | ComfyUI 版本、PyTorch 版本、GPU、显存总量和空闲量（来自 `/system_stats`） |
| `timings` | `total_s` / `per_image_s` |
| `warnings` | 校验警告（如"无 seed，结果不可复现"） |

---

## 4. 能力注册表 `backends.json` 2.0

新文件：`references\backends.v2.json`。**现有 `backends.json`（1.0）未改动**，读取时自动映射为 2.0（`registry.upgrade_v1`）。1.0 里未知的条目（如 `sdxl-img2img-ref`）映射为 `available=false`。

| 字段 | 说明 |
|-|-|
| `id` / `display_name` | 唯一 id |
| `engine` | `comfyui` / `host` / `mock`（以后加 `http_modelscope`、`http_dashscope` 等） |
| `available` / `verified` | 沿用 1.0 语义：`available=false` 拒绝执行；`verified=false` 时报告中注明未实测 |
| `external_reproducible` | 外部用户能否无私有工具、无实名账号复现（对应方案 2.1 新列） |
| `data_egress` | 是否把参考图发往远端；为 true 时必须 `egress_consent=true` |
| `capabilities` | `seed` / `negative_prompt` / `steps` / `guidance` / `reference_max` / `reference_roles` / `init_image` / `denoise_range` / `batch_max` / `sizes` / `stages` / `prompt_style`（`booru_tags` / `natural_zh` / `natural_en`） |
| `comfyui` | `url` / `workflow`（**API 格式**）/ `bindings` / `output_node` / `timeout_s` |
| `host` | `tool` + 参数名映射（统一字段 → 宿主入参名） |
| `model` / `license` | 模型版本与许可快照（对应方案 9.1） |
| `secrets` | 只写环境变量名（如 `MODELSCOPE_TOKEN`），**不写密钥本身** |
| `policy` | `retry`（默认 1）/ `backoff_s` / `max_concurrency` |
| `defaults` | 推荐的 steps、guidance、负向词 |

### 4.1 ComfyUI 绑定 `bindings`

一个注册表条目就是一个 profile：一份工作流文件，加上"统一字段 → 工作流节点输入"的映射。

```json
"bindings": {
  "positive": ["6", "text"],   "negative": ["7", "text"],
  "seed": ["3", "seed"],       "steps": ["3", "steps"],   "guidance": ["3", "cfg"],
  "denoise": ["3", "denoise"], "init_image": ["10", "image"],
  "width": ["5", "width"],     "height": ["5", "height"],
  "reference_1": ["<节点id>", "image"],
  "filename_prefix": ["9", "filename_prefix"]
}
```

- 一个字段可以绑定多个节点：`[["6","text"], ["12","text"]]`。
- `probe` 会逐项核对绑定的节点和输入是否存在于工作流中，缺失则拒绝执行。
- 拿到 UI 格式的工作流（含 `nodes` / `links`）会直接报错，提示在 ComfyUI 里用"导出（API）"重新导出。

### 4.2 已登记的后端

| id | 引擎 | 阶段 | 状态 |
|-|-|-|-|
| `agnes-aigc-img2img` | host | single / identity | 可用（维护者路线，已实测） |
| `trae-generateimage` | host | single | 可用（对照） |
| `comfyui-animagine-t2i` | comfyui | single | 工作流已附（`workflows\sdxl_t2i.api.json`），待新卡实测 |
| `comfyui-animagine-i2i` | comfyui | style | 工作流已附（`workflows\sdxl_i2i.api.json`），denoise 范围 0.15–0.6 |
| `comfyui-qwen-edit-2511` | comfyui | single / identity | `available=false`：需从 ComfyUI 内置模板导出 API 格式工作流并填写 bindings |
| `comfyui-neta-lumina-t2i` | comfyui | single | `available=false`：需从官方工作流导出 API 格式并填写 bindings |
| `modelscope-qwen-edit` | http_modelscope | single / identity | 适配器已实现（2026-11-23），模拟服务测试通过；待真实 token 出图验证 |
| `modelscope-qwen-edit-2511` | http_modelscope | single / identity | 同上；模型不在公开列表，需实际调用确认 |


### 4.3 ModelScope 适配器（`backends\modelscope.py`）

| 项 | 说明 |
|-|-|
| 协议 | `POST /v1/images/generations`（`X-ModelScope-Async-Mode: true`）→ `task_id`；`GET /v1/tasks/{id}`（`X-ModelScope-Task-Type: image_generation`）轮询到 `SUCCEED` / `FAILED`，从 `output_images[0]` 下载 |
| 发送字段 | `model` / `prompt`（中文段优先）/ `negative_prompt` / `size` / `seed` / `steps` / `guidance` / `image_url[]`（参考图，最多 3 张） |
| 参考图 | 默认以 base64 data URI 直接放进 `image_url`，**不经第三方图床**；服务若不接受，需改方案（`image_input`） |
| token | 只从环境变量读：`MODELSCOPE_TOKEN`（兼容 `MODELSCOPE_API_KEY` / `MODELSCOPE_SDK_TOKEN`）；不写入 params、manifest、generation.json |
| 限流 | 单并发，两次提交间隔 ≥ `min_interval_s`（默认 2 s）；HTTP 429 / 5xx / 连接失败重试 1 次（退避）；**任务超时不自动重提**（避免重复消耗额度），交给 `--resume` |
| 错误 | 401 / 403（token 无效、未实名、未绑定阿里云）→ 退出码 4；任务 `FAILED`（如内容审核）、超时、服务不可达 → 退出码 8 |
| 输出 | 原样保存下载的字节（保留服务写入的 AIGC 隐式标识元数据），扩展名跟随返回链接 |
| 可复现 | 有 seed，但在线模型无版本锁，恒为 `reproducible=false` |
| 许可 | 免费推理服务条款为非商用；`data_egress=true`，必须 `egress_consent` |
| 未确认 | 网页端的「参考强度」（strength）在 API 是否有对应参数未知，未传；网页端的 Qwen-Image-2.1 / MiniMax-H3 能否经 API 调用未知 |

---

## 5. 统一校验（`Adapter.validate`）

调用前一律先校验，有错误就以退出码 4 终止，不做任何静默降级：

| 检查 | 结果 |
|-|-|
| `available=false` | 错误 |
| 阶段不在 `capabilities.stages` 内 | 错误（例：用文生图后端当身份步） |
| 参考图数量超过 `reference_max`、角色不受支持、文件不存在、sha256 与 brief 不一致 | 错误 |
| 有 `init_image` 但后端不支持 img2img | 错误 |
| `denoise` 超出范围，或后端没有该入参 | 错误 |
| `data_egress=true` 且未 `egress_consent` | 错误 |
| 后端无 seed 却传了 seed | 警告（结果标为不可复现） |
| 后端无负向通道却传了 negative | 警告（交给提示词编译层改写成描述句） |
| 尺寸不在常用尺寸内 | 警告 |

---

## 6. 阶段编排（`runner.run`）

| 组合 | 说明 |
|-|-|
| `single` | 单段生成 |
| `identity` | 只做身份步（如单独用 Qwen-Image-Edit） |
| `identity → style` | 两段式（方案 2.12） |

- 其它组合（如只有 `style`）直接拒绝。
- **预算预检**：需要的调用次数 = 张数 × 段数，超过剩余预算直接拒绝并提示先确认追加，不截断、不静默超额。
- **两段式命名**：
  - 第一段产物改名为 `<prefix>-avatar-<N>-s1.<原扩展名>`，留作证据。
  - 第二段以它为 `init_image`，输出标准的 `<prefix>-avatar-<N>-src.png`，交给现有评分流水线。
  - 第二段不传参考图。
- **seed 继承**：画风步默认沿用同一张图在身份步的 seed（逐张不同）；只有在 `stages[1]` 里显式写了 `seed` 才统一覆盖。
- 身份步是宿主后端时：第一次调用写出调用单并以退出码 9 返回；宿主放回图片后再次运行，会接着执行画风步。

---

## 7. 可复现判定

`reproducible=true` 要同时满足：

1. 后端支持 seed，且每张图都记录了 seed；
2. 模型锁定了 `version` 或 `sha256`；
3. ComfyUI 后端还要记录 `comfyui_version` 和 `pytorch_version`。

缺任何一项都为 `false`，并在 `reproducible_reason` 里写明原因。宿主后端恒为 `false`（原因："后端无 seed"），和现有 manifest 里 `reproducible: false` 的口径一致。

---

## 8. 错误、重试与退出码

| 退出码 | 异常 | 含义 |
|-|-|-|
| 4 | `CapabilityError` / `ConsentError` | 未确认、能力不满足、未同意外发、后端未接入、工作流配置错误、缺少或无效的 token（与现有流水线一致） |
| 5 | — | 预算不足（与 `rounds.py` 相同含义），先问用户 |
| 8 | `AdapterError` | 后端故障：重试后仍失败、ComfyUI 未启动或不可达 |
| 9 | `NeedsAgent` | 已写出调用单，等待宿主执行（不算失败） |

> 初版设计用的是 5 / 6，接入时改为 8 / 9：`rounds.py` 已用 5（预算）、6（重复轮次）、7（满意度待回填）。

- `TransientError`（连接失败、HTTP 5xx、超时）按 `policy.retry` 重试，默认 1 次，退避时间递增。
- 4xx 错误、工作流校验失败、执行报错不重试。
- **不跨后端回退**：一个后端失败不会自动换另一个。换后端必须修改 `brief.generation.stages` 并重新走确认门。

---

## 9. 安全与合规

- **密钥**：只从环境变量读取（如 `MODELSCOPE_TOKEN`），不写进 brief、manifest 或调用单。仓库不内置任何共享 key（对应方案 2.7）。
- **参考图外发**：远端后端（`data_egress=true`）必须先 `egre## 10. 接入现有流水线（2026-11-23 完成）

| # | 改动 | 位置 |
|-|-|-|
| 1 | 新增 `--stages`（`阶段:后端id[@键=值;...]`）、`--seed`、`--egress-consent`、`--backends`；确认前核对分段后端已登记且 available；`params_hash` 覆盖分段、seed、外发同意 | `scripts\confirm_order.py` |
| 2 | 新增生成入口：读 brief → 预检（`--dry-run`）→ 按调用次数预检预算 → `runner.run` → 记 `rounds.json`（`count` = 调用次数，另记 `images` / `calls` / `stages` / 改进方向）→ 写 `generation.json`、`prompts.json`、`status.json`；支持 `--resume` / `--discard-pending` | `scripts\generate.py`（新文件） |
| 3 | `normalize_brief` 保留 `stages` / `seed` / `egress_consent` 并校验阶段组合；默认读 `backends.v2.json`（缺失时读 1.0）；`check_generation` 兼容 2.0 条目并逐段核对，`backend_verified` 取各段之「与」；原图扫描跳过 `-s1` 证据图；manifest 写入 `meta.backend` / `meta.reproducible` / `meta.generation_log`，每张加 `generator`（后端、seed、身份步证据图） | `scripts\avatar_pipeline.py` |
| 4 | 第 2 步加选后端与分段说明；第 4 步改为 `generate.py`（含退出码表与宿主执行调用单的步骤）；第 8 步重出改用 `generate.py --directions`；旧的手工流程保留给没有 `generate.py` 的宿主 | `SKILL.md` |
| 5 | 适配器包补充：续跑（已有的 `-s1` / `-src` 不重做、不重复计数）、失败时带回已花调用数、宿主侧只为缺图编号写调用单、ComfyUI 未启动与配置错误分开报 | `backends\` |
| — | 未改动：`rounds.py`、`references\backends.json`（1.0）、`protocol.md` 与全部风格 / 角色 / 角度档案 | — |

**接入后的一单流程**：

```
rounds.py --new-order → 写 brief → confirm_order.py（--stages / --seed / --egress-consent）
→ 编译提示词 → generate.py --dry-run → generate.py
   ├─ 0：原图已落盘并记账 → avatar_pipeline.py 评分
   ├─ 9：宿主执行 <prefix>-call-<N>.json → generate.py --resume
   ├─ 8：修复后端（如启动 ComfyUI）→ generate.py --resume，或 --discard-pending
   └─ 4 / 5：改 brief 重新确认 / 问用户追加预算
```

**仍需你在新卡到手后完成**：

1. 导出 Qwen-Image-Edit-2511 与 Neta Lumina 的 API 格式工作流到 `workflows\`，在 `backends.v2.json` 填 `bindings`，`available` 改为 `true`。
2. 把两份 SDXL 工作流里的 `ckpt_name` 改成本机实际文件名，并在注册表填模型 sha256。
3. 逐个 `py -3 -m backends probe <id>`，按实测表跑 T1–T5，实测通过的把 `verified` 改为 `true`。

": "satoshi-ref-2.png", "role": "identity", "sha256": "b3b83c37…907d"}],
  "seed": 100,
  "egress_consent": false,
  "fallback": "fail",
  "text2img_consent": false
}
```

---

## 11. 测试结果（2026-11-23）

测试用 mock 后端和一个模拟的本地 ComfyUI 服务进行，**未连接真实 ComfyUI 或 GPU**。

| 用例 | 结果 |
|-|-|
| mock 单段 ×2、两段式 ×2：文件命名、调用次数（2 / 4） | 通过 |
| 拦截：预算不足、阶段组合非法、denoise 越界、阶段不匹配、后端未接入、引擎未实现、未同意外发、参考图超上限 | 通过（均为退出码 4） |
| 宿主侧：写调用单 → 退出码 9（初版为 6）→ 放回 `.jpg` 后成功读回；无 seed / 无负向词的警告 | 通过（修复了 1 个 bug：原先认不出宿主返回的 `.jpg`，会重复要求生成） |
| 两段式 宿主身份步 → 画风步：`-s1.jpg` 保留原扩展名 | 通过（修复了 1 个 bug：原先把 `.jpg` 改名为 `.png`） |
| 两段式 seed 继承：逐张为 100 / 101 / 102，显式指定时统一覆盖 | 通过（修复了 1 个 bug：原先每张都用同一个 seed） |
| 1.0 注册表自动映射 | 通过 |
| ComfyUI：probe、multipart 上传、提交、轮询、下载、seed / denoise / init_image / 正负向词写入工作流、503 自动重试一次、服务未启动时拒绝执行 | 通过（模拟服务） |
| UI 格式工作流被拒绝 | 通过 |

### 11.1 接入后的端到端测试（2026-11-23）

在模拟环境里依次运行 `confirm_order.py → generate.py → avatar_pipeline.py`（mock 身份步 + 模拟 ComfyUI 画风步；宿主侧用手工放回图片模拟）：

| 用例 | 结果 |
|-|-|
| 两段式 2 张：调用 4 次、预算 4/10、`-s1` 与 `-src` 齐全；画风步 seed 100 / 101、denoise 0.35、注册表默认负向词与步数写入工作流；503 自动重试 | 通过 |
| 第二轮编号接续（第 3 张）、seed 接续（102）、轮次自动为 2；改进方向按 `feedback.json` 解析记账，未知方向报错 | 通过 |
| 预算不足（需 6 剩 4）→ 退出码 5；未确认 → 4 | 通过 |
| 评分流水线：不把 `-s1` 当原图；manifest 记 `meta.backend`、`reproducible`、`generation_log`，每张 `generator` 含后端、seed、身份步证据图；`stages` 与 `backend_verified` 正确 | 通过 |
| 宿主侧：未同意外发 → 4；同意后 → 9，写出 2 份调用单；放回 1 张后续跑 → 9，只为缺的那张重写调用单；放回全部 → 0，记 2 次调用；manifest 后端记为 Agnes、不可复现 | 通过 |
| 有未完成生成时直接重跑 → 2，提示 `--resume` / `--discard-pending` | 通过 |
| ComfyUI 未启动 → 8，已花的 2 次身份步调用记进 pending；启动后 `--resume` 只跑画风步，总计 4 次 | 通过（修复：原先报 4） |
| `--discard-pending`：已花调用补进 `unaccounted`；`rounds.py --reconcile` 不把 `-s1` 当成未记账原图；`rounds.py --check` 满意度待回填仍返回 7 | 通过 |
| 工作流缺失 → 4（配置错误） | 通过 |
| 1.0 注册表 + 旧 brief：`check_generation` 结果与改动前一致 | 通过 |

### 新卡到手后的第一次运行

在 `skills\sdz-ptah-avatar` 目录下：

```powershell
# 1. 启动 ComfyUI（默认 http://127.0.0.1:8188；其它地址用环境变量 COMFYUI_URL 指定）
# 2. 用 2.0 注册表
$env:SDZ_BACKENDS = "references\backends.v2.json"
py -3 -m backends list
py -3 -m backends probe comfyui-animagine-t2i   # 检查连通、版本、显存、绑定
py -3 -m backends selftest                       # 不需要 GPU
```

`workflows\sdxl_*.api.json` 里的 `ckpt_name` 写的是 `animagine-xl-4.0-opt.safetensors`，请按本机实际文件名修改，并把 sha256 填进注册表。
