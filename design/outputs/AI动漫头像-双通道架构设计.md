# 双通道架构设计（Agent 通道 / 网页通道）

| 项目 | 内容 |
|-|-|
| 文档用途 | 架构基线 / 实施依据 / 评审对象 |
| 状态 | **方案定稿，未实施**。本文只描述设计，不含已验证的实现 |
| 适用范围 | `app/` 控制台与 `skills/sdz-ptah-avatar/` 流水线的接入层；不改变生成算法与评分阈值 |
| 依赖的现有文档 | `README.md`、`references/protocol.md`、`AI动漫头像-后端适配器接口设计.md`、`AI动漫头像生成方案.md` |
| 关键前提 | **不部署 ComfyUI、不租 GPU**；阶段一生成后端只有 ModelScope 在线推理 |
| 决策日期 | 2026-11-25 |

> **本文档的诚实声明**：文中引用的行号与行为均来自当前仓库代码，可核对；但**所有"可行性"判断都未经真实出图验证**。ModelScope 四个后端在注册表中均为 `verified=false`，必须先完成第 0 阶段验证，本设计才具备实际交付意义。
>
> **行号说明**：2026-11-25 的 `safe_name` 与 `last_json` 修复改变了 `server.py` 与 `scripts/*.py` 的行号。文中带行号的引用可能已过时，**请按函数名检索**（如 `safe_name`、`last_json`、`create_order`），不要按行号定位。

> **修订（2026-11-25，用户决策）：网页通道改为极简表单，直接收正反提示词。**
> 本文 3.3（网页收身份描述）、7.1（网页提示词一律由 `compile_prompt.py` 编译）对**网页通道**不再适用，以本段为准：
>
> - 网页只有：名称、正向提示词、反向提示词、参考图、尺寸、数量、外发同意；方案、预算（固定 10 次调用）、seed 走服务端默认（`config.json` 的 `default_preset`）。
> - 新端点 `POST /api/generate` 一步完成「建单 → 存参考图 → 确认 → 出图（或 `dry_run`）」，agent 通道也可直接用；原来的 4 个端点保留，不删。
> - 订单标记 `prompt_source: "manual"`，提示词原样写入 `prompt-input.json` 交给 `generate.py`，**不经编译器**；`audit_prompts`（省略号、模糊色词）仍然生效。依赖编译器记录的闸门（肤色锚点、角色卡核对）对这类订单记 `null`，**null 不算通过**（见 7.1 末段）。
> - 尺寸写入 `brief.render_size`，`generate.py` 按它出图（默认 1024×1024；范围 512–2048，8 的倍数）。只有 1024×1024 有真实出图记录，其余在页面上标「未验证」。
> - 免费推理的非商用提示固定显示在页面上，不再提供「可商用」勾选。
> - 网页不调用 `avatar_pipeline.py`，所以出图后不会自动裁切、评分；需要时仍按 runbook 用命令行评分。

---

## 0. 一页结论

- **一套后端、两个入口**。Agent 通道与网页通道共用同一个 HTTP 服务、同一套订单目录、同一份台账。不做两套脚本。
- **Agent 通道走 HTTP**（决策 A）：agent 调用 `server.py` 已有的 4 个端点，不自建旁路。理由是订单状态必须共享——否则网页看不到 agent 的产出。
- **同一角色禁止并发**（决策 B）：任一通道对同一角色目录持锁期间，另一通道返回 `409 order_busy`。用 `O_EXCL` 标记文件实现，不引入第三方依赖。
- **网页通道不接大模型**。网页收集的是"身份描述"，最终提示词一律由 `compile_prompt.py` 规则编译产出，并回显给用户。
- **`mode` 一词已被占用**，新增字段命名为 `channel`（取值 `agent` / `web`），避免与 `generation.mode`（`reference` / `text2img`）冲突。
- **ComfyUI 留门不接**：保留后端代码、工作流与注册表条目，只在方案层标为不可选；业务层禁止出现 `if backend == "modelscope"` 一类分支。

**动手前必须先做的三件事**（按顺序）：

1. 用真实 token 跑通一次 ModelScope 出图，并在 UI 上保留 `verified=false` 警告（第 11 节 R1）。
2. 确认出图尺寸与 `avatar_pipeline.py` 的 `NATIVE_LIMIT = 1920` 假设是否冲突（第 11 节 R3）。
3. ~~修掉 `safe_name()` 对中文的限制~~ —— **已完成（2026-11-25）**，连带修了另外三处重复规则，见第 5.4 节 C1。

---

## 1. 术语与命名

### 1.1 channel 与 mode 的区别

项目里 `mode` 已经有两个既有含义，不能再复用：

| 词 | 既有含义 | 出处 | 本文是否复用 |
|-|-|-|-|
| `mode` | `reference` / `text2img`（是否用参考图） | `protocol.md` 第 65 行；`compile_prompt(..., mode, ...)`；`server.py:440` | ❌ 不复用 |
| `mode` | 生成方案里的 `mode` 字段 | `generation-presets.json` | ❌ 不复用 |
| `channel` | —— | 本文新增 | ✅ 新增字段 |

**`channel`**：`"agent"` | `"web"`，标记该订单由哪个入口创建。

### 1.2 通道名

对外文案用「Agent 通道 / 网页通道」，不用「Agent 模式 / 用户模式」。理由：`用户` 在本项目里已指"最终使用者"，而 agent 通道的使用者也是人，"用户模式"会产生歧义。

---

## 2. 架构总览

```
┌─────────────────────┐        ┌─────────────────────┐
│   Agent 通道         │        │   网页通道           │
│   （宿主 LLM 驱动）   │        │   （表单，无 LLM）    │
└──────────┬──────────┘        └──────────┬──────────┘
           │ HTTP                          │ HTTP
           │ （同一套 4 个端点）            │
           └──────────────┬────────────────┘
                          ▼
        ┌────────────────────────────────────────┐
        │  app/server.py                          │
        │  ├─ 互斥检查（channel 无关）             │
        │  ├─ 订单读写（brief/rounds/status）      │
        │  └─ 以子进程调用 skills/scripts/*.py     │
        └──────────────┬─────────────────────────┘
                       ▼
        ┌────────────────────────────────────────┐
        │  skills/sdz-ptah-avatar/                │
        │  ├─ compile_prompt.py  规则编译器        │
        │  ├─ generate.py        预算 / 记账 / 续跑│
        │  ├─ avatar_pipeline.py 裁切 / 评分 / 导出│
        │  └─ backends/          modelscope.py    │
        └──────────────┬─────────────────────────┘
                       ▼
              ┌──────────────────┐
              │ ModelScope 在线   │
              │ 推理 API（HTTP）  │
              └──────────────────┘
```

**关键点**：agent 通道在图中与网页通道**平级**，不绕过 `server.py`。这让两条通道天然共享订单、台账、互斥与错误码。

---

## 3. 两种通道的定义

### 3.1 能力矩阵

| 维度 | Agent 通道 | 网页通道 |
|-|-|-|
| 入口 | 宿主 agent 会话（Codar / TRAE 等） | 浏览器 |
| 需求输入 | 自然语言 + 图片 | 表单字段 + 图片 |
| 谁把需求变成结构化数据 | **LLM**（agent 自带） | **人**（直接填表单） |
| 是否调 `compile_prompt.py` | 是 | 是 |
| 是否可多轮改稿 | 是（改 brief 重新确认） | 有限（改表单重提） |
| 失败原因解释 | 由 LLM 翻译退出码 | 展示结构化错误码 |
| 台账 | 同一套 | 同一套 |
| 产出目录 | 同一套 | 同一套 |

**网页通道在能力上是 agent 通道的子集。** 这是有意的成本选择，不是缺陷：网页不接 LLM，省掉一把 key、一段输出校验和一类失败模式（见第 7 节）。

### 3.2 Agent 通道的职责边界

Agent **只负责把自然语言变成 `brief.json` + 角色卡**，不负责写最终提示词。

理由（硬性）：

- `compile_prompt.py` 内置 `BANNED_VAGUE` 禁词表（第 32 行），「小麦色」「日晒感」「古铜色」等词会被直接拒绝；
- 肤色锚点由 `skin_spec` 的 hex 经 `hex_to_rgb` / `rgb_to_hsv` / `rgb_to_lab` 计算（第 176–204 行），LLM 给不出可运算的结构就会被拒；
- 否则 `prompts.json` 里没有可审计的编译记录，`COMPILER_VERSION` / `PROMPT_TEMPLATE` 失效。

**允许的越界**：agent 可以传 `expression`、`background`、`angle` 这类编译器参数，因为它们本来就是自由文本入参。

### 3.3 网页通道的输入定义

网页收集的是 **`prompt_block`（身份描述）**，不是最终 prompt。

| 网页字段 | 写入位置 | 现有支持 |
|-|-|-|
| 身份描述（中文） | `card.prompt_block` | ✅ `ensure_character()`（`server.py:143`） |
| 身份描述（英文，可选） | `card.prompt_block_en` | ✅ 同上，缺省回退中文 |
| 肤色三色 | `card.skin_spec.base/shadow/highlight_cap` | ⚠️ 写死 `DEFAULT_SKIN_SPEC`（`server.py:36-40`） |
| 表情 | `expression` 参数 | ❌ 未暴露 |
| 背景 | `background` 参数 | ⚠️ 有默认值 |
| 角度 / 性别 | `angle` / `gender` | ✅ 已有 |
| 风格 | `style_id` | ✅ 已有 |
| 张数 / 预算 | `count` / `budget` | ✅ 已有 |

**`temperament`（气质）不列入表单**：`compile_prompt()` 的参数表里没有它，它只进报告头，不影响出词。

---

## 4. 数据契约

### 4.1 brief.json 新增字段

```json
{
  "order_id": "20261125-011500",
  "channel": "agent",
  "channel_source": "server.py:create_order",
  "generation": {
    "mode": "reference",
    "backend": "modelscope-qwen-image-2.1"
  }
}
```

| 字段 | 必需 | 取值 | 说明 |
|-|-|-|-|
| `channel` | 是（新单） | `agent` / `web` | 订单来源通道 |
| `channel_source` | 否 | 字符串 | 记录写入点，便于排查 |

**兼容性**：旧订单没有 `channel`，读取时按 `null` 处理，回填为 `"web"` 并在 UI 标注「历史单」。**不得**用默认值覆盖原文件。

### 4.2 confirm.source 扩展

现状（`confirm_order.py:199`）：

```python
parser.add_argument("--source", default="chat", choices=["chat", "card"], ...)
```

映射为 `chat_message` / `confirmation_card`。因此**当前网页与 agent 在台账里无法区分**。

改为：

| `--source` | 写入 `confirm.source` | 使用方 |
|-|-|-|
| `chat` | `chat_message` | 兼容保留 |
| `card` | `confirmation_card` | 兼容保留 |
| `web` | `web_form` | 网页通道 |
| `agent` | `agent_tool_call` | Agent 通道 |

`server.py:439` 处由固定 `--source card` 改为按 `channel` 映射。

### 4.3 目录布局（不变）

```
<orders_root>/<character_slug>/
├── brief.json                     # 当前单
├── brief-<旧order_id>.json        # 归档（rounds.py:498）
├── rounds.json                    # 台账，含 orders[] 多单
├── status.json
├── generation.json
├── manifest.json
├── prompts.json
└── <prefix>-avatar-N.png 等
```

**不按 channel 分目录**（决策 A 的必然结果）：网页必须能打开 agent 建的单。多单隔离靠 `order_id`，不靠目录层级。

---

## 5. API 契约

### 5.1 端点清单

| 方法 | 路径 | 状态 | 用途 |
|-|-|-|-|
| GET | `/api/meta` | 已有（`server.py:585`） | 角色/风格/token 状态 |
| GET | `/api/presets` | 已有（`:587`） | 方案列表，含 `selectable` |
| GET | `/api/orders` | 已有（`:591`） | 订单列表 |
| GET | `/api/orders/{name}` | 已有（`:596`） | 订单详情 |
| GET | `/api/orders/{name}/file/{file}` | 已有（`:593`） | 取文件 |
| POST | `/api/orders` | 已有（`:602`） | 建单 |
| POST | `/api/orders/{name}/upload-reference` | 已有（`:604`） | 上传参考图 |
| POST | `/api/orders/{name}/confirm` | 已有（`:607`） | 确认门 |
| POST | `/api/orders/{name}/generate` | 已有（`:610`） | 出图 |

**结论：Agent 通道不需要新端点**，这是本方案成本低的主因。

### 5.2 Agent 通道调用序列

```
1. GET  /api/presets
   → 只选 selectable=true 且 policy.verified 为 true 或经用户确认的方案
   → 当前只有 modelscope-* 可能可用（取决于 token）

2. POST /api/orders
   {
     "character_id": "小雪",            // 目录名由 slug 规则生成，中文可用（见 5.4 C1）
     "character_name": "小雪",           // 显示名，可中文
     "character_desc": "银白长发、蓝瞳、清冷",
     "character_desc_en": "long silver hair, blue eyes, aloof",
     "style_id": "tv-anime-cel",
     "gender": "female",
     "angle": "profile_right",
     "count": 2,
     "budget": 10,
     "channel": "agent"
   }
   → { ok, order: "<character_slug>" }

3. POST /api/orders/{order}/upload-reference
   { "name": "ref.png", "data": "data:image/png;base64,..." }

4. POST /api/orders/{order}/confirm
   {
     "preset_id": "modelscope-qwen-2-1",
     "confirm_text": "确认生成",
     "references": ["<上一步返回的文件名>"],
     "egress_consent": true,
     "budget": 10
   }

5. POST /api/orders/{order}/generate
   { "count": 2 }

6. GET  /api/orders/{order}
   → 读 status.json / files / budget，轮询至 state 非 generating
```

**顺序硬约束**：必须"建单 → 传图 → 确认 → 生成"。`confirm_order.py` 会校验参考图是否已归档（`archive_references`，第 134 行），跳过第 3 步会因参考图不足被拒。

**agent 不得并行发起第 5 步**：`generate.py` 有 `generation-pending.json` 机制，重复调用会返回退出码 2。

### 5.3 错误码映射

现有 `generate.py` 退出码（README 第 149–158 行）需要映射成 HTTP 语义，供 agent 判断是否重试：

| 退出码 | 含义 | HTTP | `code` | agent 应做什么 |
|-|-|-|-|-|
| 0 | 成功 | 200 | — | 取图 |
| 2 | 输入错误 / 有未完成生成 | 400 / 409 | `bad_input` / `pending_exists` | 修正参数，或用 `resume` / `discard_pending` |
| 4 | 未确认 / 能力不满足 / 未同意外发 | 403 | `not_confirmed` / `capability` / `no_egress_consent` | **不得重试**，需人确认 |
| 5 | 预算不足 | 402 | `budget_exhausted` | 询问是否追加 |
| 8 | 后端故障 | 502 | `backend_error` | 可 `resume` 重试 |
| 9 | 等待宿主执行（host 后端） | 501 | `needs_host` | 本方案下**不应出现** |
| — | 订单被占用 | 409 | `order_busy` | 稍后重试（第 6 节） |

### 5.4 已发现的契约约束

**C1：中文路径段——比预想严重，已修复（2026-11-25）**

原以为只有 `server.py` 一处问题：

```python
# server.py（修复前）
text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
if not text or text in (".", ".."):
    raise ValueError("名称不合法：%r" % value)
```

实际排查发现**四处各写了一份规则**，且不一致：

| 位置 | 字符集 | 大小写 | 中文结果 |
|-|-|-|-|
| `server.py:safe_name` | `[A-Za-z0-9_.-]` | 保留 | 抛 `ValueError` |
| `rounds.py:order_dir_for`（第 136 行） | `[0-9A-Za-z_-]` | 转小写 | **`order`** |
| `confirm_order.py:detect_prefix`（第 126 行） | `[0-9A-Za-z_-]` | 保留 | **`avatar`** |
| `generate.py:detect_prefix`（第 95 行） | `[0-9A-Za-z_-]` | 保留 | **`avatar`** |

比 `ValueError` 更糟的是 `rounds.py` 的行为：所有中文角色都落到同一个 `<root>/order/` 目录，**不同角色互相覆盖台账**。而 `server.py` 又把 brief 写在 `<root>/<中文名>/`，一个订单被拆成两个目录。

**已采用的修法**（原推荐的「前端强制 ASCII slug」方案作废）：

新增 `skills/sdz-ptah-avatar/scripts/path_names.py` 作为唯一实现，四处统一调用：

| 调用方 | 调用方式 |
|-|-|
| `server.safe_name` | `safe_slug(value, extra=".+")`，保留既有 `v1.0` / `x+y` 类 slug |
| `rounds.order_dir_for` | `safe_slug(character, lower=True, fallback="order")`，保留旧的转小写约定 |
| `confirm_order.detect_prefix` | `safe_slug(prefix, fallback="avatar")` |
| `generate.detect_prefix` | 同上 |

规则：NFKC 归一化 → 非允许字符折叠为 `-` → 收边 → Windows 保留设备名加 `_` → 截断 64 字符。

**兼容性**：既有 ASCII 目录名（`satoshi`、`fox-armor`、`tv-anime-cel`）逐字节不变，已由测试锁定。唯一行为变化是连续短横会被合并（`a--b` → `a-b`），仓库内无此类目录。

**验证**：`app/tests/` 下 **30 项测试**全部通过，含真实建目录、`rounds.json` 落盘，以及直接运行 `rounds.py` 子进程验证 stdout 可被 `last_json` 解析。已验证范围：进程内 API 调用 + 临时目录，**未验证浏览器与 agent 通道的真实网络流程**。

**C2：上传是 base64 data URL，20MB 上限**

`upload_reference()`（`server.py:388`）接收 `data:image/...;base64,`，超过 20MB 拒绝。而 `modelscope.py:158` 的 `_image_ref()` 又会把文件重新 base64 塞进请求体，等于**两次 base64 膨胀**。

必须在入库前压缩（见 7.2）。

**C3：`/api/orders/{name}` 的 `name` 是目录名，不是 `order_id`**

`order_dir()`（`server.py:83`）按目录名解析。agent 传 `order_id` 会 404。建单响应返回的 `order` 字段才是目录名。

---

## 6. 并发与互斥（决策 B：同一角色禁止并发）

### 6.1 为什么必须做

- 全项目**没有任何文件锁**（`fcntl` / `msvcrt` / `filelock` 均未使用，已确认）；
- `rounds.json` 的 `save_ledger()`（`rounds.py:234`）是读-改-写；
- `do_new_order()`（`rounds.py:471`）会把旧 `brief.json` `os.replace` 成归档文件；
- 两条通道同时建单会互相归档对方的 brief。

### 6.2 实现方式（不引入依赖）

标记文件 + `O_CREAT|O_EXCL` 原子创建：

```
<order_dir>/.active-lock
内容: {"pid": 1234, "channel": "agent", "acquired_at": "2026-11-25T01:15:00"}
```

规则：

| 场景 | 行为 |
|-|-|
| 无锁文件 | 获取成功，写入标记 |
| 有锁，pid 存活且未超时 | 拒绝，返回 `409 order_busy`，附 `holder_channel` 与 `acquired_at` |
| 有锁，pid 不存在（进程已死） | 视为陈旧锁，记录一条 warning 后接管 |
| 有锁，超过 `LOCK_TTL`（默认 30 分钟） | 同上，接管并告警 |
| 正常结束 / 异常 | `finally` 中删除标记文件 |

**粒度**：以订单目录（角色）为单位加锁，而非全局。不同角色可并行。

**获取时机**：
- `POST /api/orders`（建单，因为要归档 brief）
- `POST /api/orders/{name}/confirm`
- `POST /api/orders/{name}/generate`

**不获取**：所有 `GET` 端点（只读）。

### 6.3 已知不覆盖的场景

- 多个 `server.py` 实例（多机 / 多进程）同时跑：锁文件在共享盘上仍有效，但**网络文件系统的 `O_EXCL` 语义不保证**。单实例部署不受影响。
- agent 绕过 HTTP 直接调 `generate.py`：锁机制失效。这是决策 A 的另一个理由。

---

## 7. 提示词与图片处理的边界

### 7.1 提示词：谁产出什么

| 环节 | 产出 | 执行者 |
|-|-|-|
| 需求 → 结构化 | `brief.json` + 角色卡 | Agent 的 LLM / 网页表单的人 |
| 结构化 → 提示词 | `prompt_zh` / `prompt_en` + `prompts.json` | `compile_prompt.py`（规则，无模型） |
| 提示词 → 图片 | 原图 | `backends/modelscope.py` |

**网页必须回显编译结果**：把 `prompts.json` 里的正/负向提示词展示给用户，让"为什么这么写"可审计。这也是排除"用户直接写英文 tag"的替代方案。

**若确需手写 prompt**（高级模式），必须：

- 写 `prompt_source: "manual"`；
- manifest 中把依赖编译器的闸门（肤色一致性、禁词检查）记 `null`；
- UI 明确提示「**null 不算通过**」（与 `README` 第 34 行口径一致）。

### 7.2 图片处理：两处，均不需要模型

**① 入库前压缩（必须）**

| 参数 | 值 | 理由 |
|-|-|-|
| 长边 | ≤ 1536 | 覆盖 1024 输出来源，留缩放余量 |
| 格式 | JPEG q88（有透明通道时保留 PNG） | 兼顾体积与可判读性 |
| 目标体积 | 300 KB – 1 MB | 避免 base64 后请求体过大（5.4 C2） |
| 工具 | Pillow（项目已有依赖） | 不引入新库 |

**② 出图后处理（已有）**

`avatar_pipeline.py` 的定位 → 裁切 → 评分 → 闸门 → 导出，纯 Pillow + 规则。

### 7.3 网页不需要 LLM 的证明

`compile_prompt.py` 的导入仅 `argparse / colorsys / hashlib / json / os / re / sys`（第 19–25 行），无任何网络或模型调用。**"写提示词"本身是确定性计算。**

真正的缺口只有"自然语言 → 角色卡"一步，而网页用表单直接填掉这一步。

---

## 8. 后端范围与 forward-compat

### 8.1 阶段一可用后端

仅 `engine = http_modelscope`：

| 后端 id | 状态 | 可商用 |
|-|-|-|
| `modelscope-qwen-edit` | `verified=false` | 待确认（免费推理条款非商用） |
| `modelscope-qwen-edit-2511` | `verified=false` | 同上 |
| `modelscope-qwen-image-2.1` | `verified=false` | 同上 |
| `modelscope-minimax-h3-image` | `verified=false` | 同上 |

不可用但**保留**：

| 后端 | engine | 保留原因 |
|-|-|-|
| `agnes-aigc-img2img` | `host` | 维护者本地实验仍可用 |
| `trae-generateimage` | `host` | 同上 |
| `comfyui-*`（4 个） | `comfyui` | 后续接回 |

### 8.2 留门的三条规则

1. **不删** `backends/comfyui.py`、`workflows/*.json`、`backends.v2.json` 中的 comfyui 条目；
2. **不在业务层写死后端**。禁止 `if backend == "modelscope"` / `if channel == "web" then modelscope` 这类分支。后端选择一律经 `generation-presets.json` + `list_presets.py` 的 `selectable` 逻辑；
3. **不删** `agent_side.py`。它是 host 后端的握手协议（`sdz-agent-call/1.0`，第 60 行），以后接回宿主工具要用。

### 8.3 方案可用性的现成机制

`list_presets.py` 已经实现了降级判断，**不需要重写**：

| 判断 | 位置 | 行为 |
|-|-|-|
| 环境变量缺失（如 token） | `_env_missing()` 第 68 行 | 加入 `reasons` → `selectable=false` |
| 后端 `available=false` | 第 90 行 | 同上 |
| 引擎无适配器 | 第 92 行 | 同上 |
| 非商用 | 第 188 行 | `commercial=true` 时报错拦截 |
| 宿主工具 | 第 127 行 | warning：「独立网页可能只产出调用单」 |

**这意味着"阶段一只暴露 ModelScope"基本是配置工作，不是开发工作。**

---

## 9. 与现有代码的差异清单

| # | 文件 | 位置 | 改动 | 规模 |
|-|-|-|-|-|
| 1 | `app/server.py` | `safe_name()` | 委托 `path_names.safe_slug`（✅ 已完成） | ~10 行 |
| 1b | `skills/.../scripts/path_names.py` | 新增 | 共享 slug 规则（✅ 已完成） | ~75 行 |
| 1c | `rounds.py` / `confirm_order.py` / `generate.py` | 136 / 126 / 95 | 改用共享规则（✅ 已完成） | 各 1–3 行 |
| 1d | `app/server.py` | `last_json()` | 支持多行 JSON（✅ 已完成） | ~12 行 |
| 2 | `app/server.py` | `create_order()` ~333 | 接收并写入 `channel` | ~10 行（✅ 2026-11-25，待跑测试） |
| 3 | `app/server.py` | `confirm_order()` ~439 | `--source` 按 channel 映射 | ~5 行（✅ 2026-11-25，待跑测试） |
| 4 | `app/server.py` | `upload_reference()` 388 | 入库前压缩 | ~25 行（✅ 2026-11-25，新模块 `image_prep.py`，模块已实测，服务接入待跑测试） |
| 5 | `app/server.py` | `_dispatch()` ~581 | 退出码 → HTTP 状态映射 | ~30 行（✅ 2026-11-25，新模块 `api_errors.py`，待跑测试） |
| 6 | `app/server.py` | 新模块 `orderlock.py` | 互斥锁 | ~45 行（✅ 2026-11-25，`orderlock.py`，待跑测试） |
| 7 | `skills/.../confirm_order.py` | 199 | `--source` choices 扩展 | 2 行（✅ 2026-11-25，待跑测试） |
| 8 | `skills/.../rounds.py` | `save_ledger()` 234 | 临时文件 + `os.replace` 原子写 | ~10 行（✅ 已有） |
| 9 | `app/web/index.html` | 建单表单 | 肤色色板、表情输入 | ~40 行 |
| 10 | `app/web/app.js` | ~225 | 新字段提交、编译结果回显 | ~35 行 |
| 11 | `app/config.json` | — | 增 `lock_ttl_s`、`image_max_edge` | 2 行（`lock_ttl_s` ✅，`image_max_edge` ✅） |

**合计约 220 行，不含测试。** 无新增第三方依赖。

---

## 10. 分期与验收

### 阶段 0：前置验证（必须先做，未完成不得进入阶段 1）

| 编号 | 项 | 通过标准 |
|-|-|-|
| P0-1 | ModelScope 真实出图 | 至少 1 个后端出图成功，记录 seed / 尺寸 / 耗时 |
| P0-2 | 出图尺寸兼容性 | 确认 `avatar_pipeline.py` 在非 1920 原生图上能定位与裁切 |
| P0-3 | 中文 slug | 确认修复后中文角色名可建单且目录可读 |

### 阶段 1：双通道打通

| 编号 | 项 | 通过标准 |
|-|-|-|
| P1-1 | 网页通道全流程 | 表单 → 确认 → 生成 → 出图，`channel=web`、`confirm.source=web_form` |
| P1-2 | Agent 通道全流程 | 脚本调 4 个端点完成同样流程，`channel=agent`、`confirm.source=agent_tool_call` |
| P1-3 | 互斥 | 两通道并发同角色，第二个返回 `409 order_busy` 且不破坏文件 |
| P1-4 | 提示词可审计 | `prompts.json` 含 `COMPILER_VERSION`、正/负向词，网页可回显 |
| P1-5 | 方案过滤 | comfyui / host 方案在网页不可选，且不报错 |

### 阶段 2：健壮性

| 编号 | 项 | 通过标准 |
|-|-|-|
| P2-1 | 错误码 | 退出码 2/4/5/8 分别映射到正确 HTTP 状态与 `code` |
| P2-2 | 陈旧锁 | 强杀进程后 30 分钟内可被接管 |
| P2-3 | 图片压缩 | 10 MB 原图入库后 ≤ 1 MB 且视觉可判读 |
| P2-4 | 无 token 降级 | 未配 token 时方案列表为空且给明确提示，不静默回退 |

### 阶段 3（不在本文范围）

公网部署需要的鉴权、多租户隔离、HTTPS、内容审核、AIGC 标识。**未实施前只允许本地/局域网使用**（与 `README` 第 220 行「不提供托管服务」一致）。

---

## 11. 未验证项与风险

| 编号 | 风险 | 影响 | 处置 |
|-|-|-|-|
| **R1** | 4 个 ModelScope 后端全部 `verified=false` | 上云后**没有任何已验证的生成路线**；`prompts.json` 与 manifest 会一直带 warning | 阶段 0 必须先出图并人工评分，把结论写回注册表；UI **不得**为好看而隐藏 warning |
| **R2** | 免费推理条款**非商用** + 动态限流 | 不能收费；单并发（`modelscope.py` 第 18 行）导致排队 | 自用可接受；商用需换商业 API 适配器（路线图 M4 未做） |
| **R3** | `avatar_pipeline.py` 的 `NATIVE_LIMIT = 1920`（第 151 行） | 若 ModelScope 出图远小于 1920，裁切与评分阈值可能失准 | P0-2 实测；失准则记录为已知限制，**不得**私自放宽阈值 |
| **R4** | host 后端在云上不可用 | `agnes-aigc-img2img` 是唯一 `verified=true` 的路线，云上无法使用 | 已在阶段一排除；文档中不得把宿主路线描述为云端可用 |
| **R5** | ~~中文 slug 限制~~ | ~~中文角色名无法建单~~ | **已修复（2026-11-25）**，见 5.4 C1；`rounds.py` 原会把所有中文角色落到同一个 `order/` 目录，属数据覆盖级问题 |
| **R6** | 锁文件在网络盘语义不保证 | 多实例部署时互斥可能失效 | 阶段 1 只支持单实例；多实例需改用数据库或分布式锁 |
| **R7** | 参考图外发 | ModelScope 接收 base64 图片，等于把用户图片上传第三方 | `egress_consent` 必须由人确认；公网部署前需隐私声明 |
| **R8** | ~~`last_json()` 无法解析 `echo()` 的多行 JSON~~ | ~~`order_dir` / `order_id` 总走回退值，跨秒会与 `rounds.json` 不一致~~ | **已修复（2026-11-25）**：改为「行首 `{` + `json.raw_decode`」；对照实验证实旧实现返回 `None`、新实现正确解析出 `order_dir` / `order_id` |

---

## 12. 非目标（明确不做）

- ❌ Agent 操控浏览器（Playwright 点网页）——层级错误，脆弱且不可审计；
- ❌ 网页内置大模型——网页通道刻意不接 LLM；
- ❌ 两套脚本 / 两套台账——一切复用同一后端；
- ❌ 按 channel 分目录——会切断"网页查看 agent 订单"的能力；
- ❌ 删除 ComfyUI 相关代码与工作流——留门；
- ❌ 多租户、计费、公网鉴权——阶段 3 另行设计；
- ❌ 修改评分阈值或闸门口径——本文只动接入层。

---

## 附录：决策记录

| 编号 | 决策 | 备选 | 理由 |
|-|-|-|-|
| A | Agent 通道走 HTTP 调 `server.py` | 直接调 Python 脚本 | 状态共享；锁机制只对 HTTP 入口有效；错误码统一 |
| B | 同一角色禁止并发（`409`） | 允许并发 + 文件锁 | 无锁库；`do_new_order` 的 brief 归档是顺序语义，并发下易错 |

> 两条决策均由用户在 2026-11-25 明确指定。若后续需要放开，须先补并发安全测试。
