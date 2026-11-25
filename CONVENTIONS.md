# sdz-ptah 命名与目录规范

> 目的：让「文件名/目录名本身能说明职责」，减少歧义与散落文件。
> 原则：**先写规范，再动文件；每改一处，回写引用；历史日志不改**。
> 生效：2026-11-26。存量偏差按 `整改清单` 分级推进，不一次性强改。

---

## 1. 顶层目录职责

| 目录 | 职责 | 入库 | 命名状态 |
|---|---|---|---|
| `skills/sdz-ptah-avatar/` | 技能本体（自包含、可安装） | 是 | 合规 |
| `sdz-ptah-app/` | 本地 Web 控制台（服务 + 前端 + 测试） | 部分 | 合规 |
| `ai-anime-avatar/` | 方案 / 评审 / 验证资产（知识资产） | 部分 | 合规 |
| `architecture/` | 架构设计草稿 | 是 | 合规 |
| `avatar-orders/` | 运行时订单数据（图片 + 台账） | **否** | 合规，待加 gitignore |
| `docs/`（本文所在外的规范） | 项目级规范文档 | 是 | 新增 |
| `.workbuddy/` | 工作日志与项目记忆 | 否 | 合规，待加 gitignore |
| `.trae/` `.screenshots/` | 宿主本地配置 / 截图缓存 | 否 | 已 gitignore |

**规则**：顶层只用「小写英文 + 连字符」；不新增中文顶层目录。

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

---

## 3. 备份文件策略

散落的 `.bak-*` 属于临时安全网，**不应长期停留在源码树**。

- 命名：`<原名>.bak-<原因>`（现状合规），如 `server.py.bak-before-no-web-prompt-check`。
- 存放：统一归档到 `archive/backups/`，按日期子目录 `YYYY-MM-DD/`。
- 生命周期：变更稳定后 7 天内确认保留或删除；删除前对比无差异再动。
- **当前偏差**：12 个 `.bak-*` 散落在 `skills/`、`sdz-ptah-app/`、`avatar-orders/`（详见整改清单 B1）。

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
- A1 `.gitignore` 补 `avatar-orders/`、`sdz-ptah-app/uploads/`、`*.bak-*`、`.workbuddy/`、诊断输出 txt。
- A2 本文落盘为项目规范基线。

### B 档 · 低风险（移动 + 轻量回写）— ✅ 已完成 2026-11-26
- B1 12 个 `.bak-*` 已归档到 `archive/backups/2026-11-26/`（保留原目录层级）。
- B2 诊断脚本 + 输出已移入 `ai-anime-avatar/review/diagnostics/`（日志保留原路径快照，另追加迁移说明）。
- 引用回写：`回归_峰值检测.py` 的 `BAK` 路径已改指归档位置；并修复其 `.bak` 加载缺陷（`SourceFileLoader`）。

### C 档 · 中风险（需回写多处引用）— ✅ 已完成 2026-11-26
- C1 诊断脚本已统一为 `diag_NN_<主题>.py`，输出为 `.out.txt`；5 个脚本重命名后全部验证可执行。历史日志路径保留不回改，另在当日日志追加迁移说明。
- C2 `verify` 输出目录已统一为 `out-<变体>`；`.gitignore` 回写为单条规则 `out-*/`，验证目录不再出现在 git 状态。

### D 档 · 高风险（不建议主动做）
- D1 `基准/` 改英文目录名（影响 gitignore + manifest 路径 + 方案文档）。
- D2 顶层目录重命名（影响 README + 所有脚本相对路径）。

> 执行策略：默认只做 A 档；B/C 档需确认后逐档执行；D 档保持不动，除非明确要求。
