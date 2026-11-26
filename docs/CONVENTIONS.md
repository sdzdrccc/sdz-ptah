# sdz-ptah 命名与目录规范

> 目的：让「文件名/目录名本身能说明职责」，减少歧义与散落文件。
> 原则：**先写规范，再动文件；每改一处，回写引用；历史日志不改**。
> 生效：2026-11-26。存量偏差按 `整改清单` 分级推进，不一次性强改。

---

## 1. 顶层目录职责

| 目录 | 职责 | 入库 |
|---|---|---|
| `skills/sdz-ptah-avatar/` | 技能本体（自包含、可安装） | 是 |
| `app/` | 本地 Web 控制台（服务 + 前端 + 测试） | 部分 |
| `design/` | 方案 / 评审 / 验证 / 校准基准（知识资产） | 部分 |
| `docs/` | 项目级规范文档与变更记录 | 是 |
| `orders/` | 运行时订单数据（图片 + 台账） | **否** |

**规则**：顶层只用「小写英文 + 连字符」；不新增中文顶层目录。2026-11-26 已按此规整：原 `ai-anime-avatar/` → `design/`、原 `基准/` → `design/baselines/`、废弃的 `architecture/` 与空目录 `.trae/`、`.screenshots/` 已删除、规范文档收进 `docs/`。

---

## 2. 文件命名规则

按文件类型分五类，每类一套：

| 类型 | 规则 | 示例 |
|---|---|---|
| **可执行脚本** | 小写 snake_case，动词或职责开头 | `confirm_order.py`、`avatar_pipeline.py`、`rounds.py` |
| **诊断 / 临时脚本** | `diag_NN_<主题>.py`，统一放 `review/diagnostics/` | `diag_01_orientation.py` |
| **配置 / 数据** | 小写 kebab-case；有版本用 `.vN` 后缀 | `tv-anime-cel-solidbg.json`、`backends.v2.json` |
| **文档** | 中文可读名，系列用 `主题-子标题` | `AI动漫头像生成方案.md`、`魔搭可用模型整理.md` |
| **运行产物** | 时间戳 `YYYYMMDD-HHMMSS-<slug>` 或 `<前缀>-<用途>-<序号>` | `20261125-231304`、`avatar-avatar-2-src.png` |

**统一约定**
- 目录名：优先小写 kebab-case（`prompt-examples/`、`characters/`）。
- 不用空格、不用大写开头、不用双下划线。
- 序号用两位补零（`01`、`02`），避免 `2` 与 `10` 排序错乱。
- 扩展名小写。
- **订单目录默认名**：网页「保存名称」留空时用 `avatar-<YYYYMMDD>`（本地日期，如 `avatar-20261126`），前端 `defaultName()` 与服务端 `default_order_name()` 同一规则。**不要固定写成 `avatar`**——固定名会把所有未命名订单堆进同一目录，分不清是新单还是续跑。日期粒度只到日，同一天内的第二单会并入同一目录（即「继续这单」的语义）；要按单隔离就手工在名称里加时分。

---

## 3. 备份文件策略

散落的 `.bak-*` 属于临时安全网，**不应长期停留在源码树**。

- 命名：`<原名>.bak-<原因>`（现状合规），如 `server.py.bak-before-no-web-prompt-check`。
- 存放：统一归档到 `archive/backups/`，按日期子目录 `YYYY-MM-DD/`。
- 生命周期：变更稳定后 7 天内确认保留或删除；删除前对比无差异再动。
- **当前偏差**：12 个 `.bak-*` 散落在 `skills/`、`app/`、`orders/`（详见整改清单 B1）。

---

## 4. 诊断 / 临时输出

今天产生的诊断脚本与输出散落在 `ai-anime-avatar/review/` 根目录，命名不统一（有无序号、`诊断_`/`诊断N_`/`回归_` 三种前缀）。

- 脚本：归入 `ai-anime-avatar/review/diagnostics/`，命名 `diag_NN_<主题>.py`。
- 输出：同目录 `diag_NN_<主题>.out.txt`，与脚本一一对应。
- 注意：`.workbuddy/memory/` 日志里记录的是**当时路径**，属历史快照；若移动文件，**不回改日志**（append-only），另在当日日志追加「已迁移」说明即可。

---

## 5. 特殊目录说明

- `ai-anime-avatar/基准/`：中文名、已 gitignore 且被多处引用（manifest、方案文档）。**保留不改**（legacy），仅在本文档登记。
- `ai-anime-avatar/verify/out-<变体>/`：验证输出，已 gitignore（规则 `out-*/`）。2026-11-26 已统一命名：`out-baseline`（原 out）、`out-global`、`out-installed`、`out-4k-probe`、`out-control`（原 control_out）。
- `backends.json` 与 `backends.v2.json` 并存：前者是 1.0 兼容保留。**保留**，但新代码一律用 `.v2`。

---

## 6. 变更原则（硬规则）

1. **不擅自改判据或阈值来制造绿灯**——评分不合格但用户认可时，走满意度台账（`rounds.py --set-satisfaction`）如实记录，不改 manifest / brief / 阈值。
2. **引用回写**：改文件名或路径后，必须同步更新所有引用（README、代码、`protocol.md`、脚本参数默认值、工作日志追加说明）。
3. **日志 append-only**：`.workbuddy/memory/*.md` 只追加，不改写历史条目。
4. **可复现优先**：运行产物能被脚本重建的，一律 gitignore，不入库。
5. **小步提交**：一次只做一类改动，做完验证，再进下一类。

---

## 7. 整改清单（按风险分级）

### A 档 · 零风险（可直接做）
- A1 `.gitignore` 补 `orders/`、`app/uploads/`、`*.bak-*`、`.workbuddy/`、诊断输出 txt。
- A2 本文落盘为项目规范基线。

### B 档 · 低风险（移动 + 轻量回写）— ✅ 已完成 2026-11-26
- B1 12 个 `.bak-*` 已归档到 `archive/backups/2026-11-26/`（保留原目录层级）。
- B2 诊断脚本 + 输出已移入 `ai-anime-avatar/review/diagnostics/`（日志保留原路径快照，另追加迁移说明）。
- 引用回写：`回归_峰值检测.py` 的 `BAK` 路径已改指归档位置；并修复其 `.bak` 加载缺陷（`SourceFileLoader`）。
- 后续（同日）：确认当前文件齐全、代码稳定后，`archive/` 已删除，B1 备份不再保留。`diag_05_峰值检测回归.py` 已改为从 git 历史（`4ff34fc`）读取旧版代码，不再依赖本地备份，验证通过。

### C 档 · 中风险（需回写多处引用）— ✅ 已完成 2026-11-26
- C1 诊断脚本已统一为 `diag_NN_<主题>.py`，输出为 `.out.txt`；5 个脚本重命名后全部验证可执行。历史日志路径保留不回改，另在当日日志追加迁移说明。
- C2 `verify` 输出目录已统一为 `out-<变体>`；`.gitignore` 回写为单条规则 `out-*/`，验证目录不再出现在 git 状态。

### D 档 · 高风险（不建议主动做）
- D1 `基准/` 改英文目录名（影响 gitignore + manifest 路径 + 方案文档）。
- D2 顶层目录重命名（影响 README + 所有脚本相对路径）。

> 执行策略：默认只做 A 档；B/C 档需确认后逐档执行；D 档保持不动，除非明确要求。

### 后续：中度规整（2026-11-26，大人确认后执行）
- D1 `基准/` → `design/baselines/`（已执行，gitignore + manifest + 风格档案 note 全部回写）。
- D2 顶层改名：`ai-anime-avatar/` → `design/`（已执行，15 处引用回写）。
- 附带：废弃的 `architecture/draft/api_design.md`（自带"已被取代"警告）与空目录 `.trae/`、`.screenshots/` 已删除；`CONVENTIONS.md`、`CHANGELOG-命名整理.md` 收进 `docs/`。
- 本节为历史记录；当前目录状态以第 1 节表格为准。

### 后续：订单目录改名（2026-11-26）
- `avatar-orders/` → `orders/`（已执行）。理由：与代码自身词汇对齐——配置项 `orders_root`、路由 `/api/orders`、`orderlock.py`、`create_simple_order` 全部用 `order(s)`，目录名跟代码同名，少一层心智翻译；且更短，符合"紧凑"。
- 影响面：该目录 **0 个文件入库**（`orders/` 在 `.gitignore`），改名是纯本地 `mv`，无 git 历史牵连，随时可 `mv` 回滚。
- 回写：`app/config.json`（绝对路径）、`app/server.py`（`DEFAULT_CONFIG.orders_root`）、`.gitignore`、README（含 8 处示例路径与目录树）、`skills/.../SKILL.md`、`references/protocol.md`、`design/outputs/` 方案正文、5 个诊断脚本默认路径、2 个测试夹具注释/路径。
- `orders/avatar/generation.json` 内的绝对 `path` 字段同步改写：该字段是产物指针，改名后旧值不可解析；seed / sha256 / prompt 等生成事实一律未动。`.workbuddy/memory/` 日志按 append-only 规则保留旧名，不回改。

### 后续：角色占位名归真（2026-11-26）
- 现象：订单目录里出现 `orders/avatar/`，且文件名是 `avatar-avatar-1.png`——`avatar` 是建单时角色名缺省的**兜底值**，不是设计上多一层。按「一个角色一个目录」，这一层是角色目录，不能删层级，只能改名。
- 处置：`orders/avatar/` → `orders/satoshi/`（角色即 `references/characters/satoshi.json`，形象与产物一致）；产物前缀 `avatar-avatar-*` / `avatar-ref-*` → `satoshi-avatar-*` / `satoshi-ref-*`；`brief.character` / `character_id`、`manifest.meta.order_dir` 同步为 `satoshi`。
- 未动：`app/uploads/avatar-upload-*.jpg`（原始上传，非本次范围）、`sdz-ptah-avatar`（技能名）、`anime-avatar/`（工具标识）。
- 校验：11 个 json 全部可解析；ledger 引用的 17 个文件名全部存在；119 项测试 OK；`diag_01` 读图正常。
- 教训：正则 `[\\/]+` 贪婪匹配双反斜杠会漏掉一个，导致 JSON 出现非法转义。**批量改路径后必须逐个 `json.load` 复验**，不能只看替换计数。

### 后续：控制台目录去项目前缀（2026-11-26）
- `sdz-ptah-app/` → `app/`（已执行）。理由：仓库本身就叫 `sdz-ptah`，目录名再带一遍项目名属纯冗余；`app/` 与 `design/`、`docs/`、`orders/`、`skills/` 同为短名，职责由一级目录名即可表达。
- 安全性依据：该目录内**没有任何代码硬编码自己的目录名**——`PROJECT_ROOT` / `APP_DIR` 全部由 `__file__` 推导，改名不影响运行时；git 识别为 rename，历史保留。
- 回写：`.gitignore`（`app/uploads/`、`app/custom/`）、README 目录树（并重算 `#` 注释列对齐）、`design/outputs/` 架构图（含框线内边距）、`app/tests/test_safe_name.py` 运行说明。
- **规则补充（去前缀的边界）**：
  - **仓库内目录** → 去掉项目前缀（`app/` 而非 `sdz-ptah-app/`）。
  - **可安装包名** → 保留前缀作命名空间（`skills/sdz-ptah-avatar/` 不动）：它会被拷到 `~/.workbuddy/skills/<名字>/`，全局目录下可能与其他项目的 avatar skill 同名，前缀是唯一性保障。
  - **工具标识 / schema 字段** → 不动（`anime-avatar/avatar_pipeline.py` 的 `tool` 字段、`sdz-generation-log/1.0` 等），属对外契约。
- 说明：本文档中 12:42 之前的条目写于改名之前，其中的 `sdz-ptah-app` 即现行 `app/`；上文路径已统一为现行名，便于检索，语义未变。
