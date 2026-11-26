# 命名与目录整理 · 变更说明

- **日期**：2026-11-26
- **范围**：项目文件与目录命名整理（A / B / C 档）
- **依据**：`CONVENTIONS.md`（命名与目录规范）
- **原则**：先定规范、再动文件；改一处、回写引用；历史日志不改；可回退

---

## 一、总览

| 项 | 内容 |
|---|---|
| 备份文件归档 | 12 个 `.bak-*` → `archive/backups/2026-11-26/` |
| 诊断脚本归位 + 重命名 | 9 个文件 → `ai-anime-avatar/review/diagnostics/`，统一 `diag_NN_` |
| 验证输出目录改名 | 5 个目录 → 统一 `out-<变体>` |
| 新增规范文档 | `CONVENTIONS.md`、`CHANGELOG-命名整理.md` |
| 修改配置 | `.gitignore`（补 4 类忽略 + verify 规则合并） |
| **未改动** | 所有源码逻辑、判据、评分、README、D 档目录 |

---

## 二、A 档 · 规范落地

### A1 新增 `CONVENTIONS.md`
项目级命名与目录规范基线，含：
- 顶层目录职责表（入库 / 不入库）
- 5 类文件命名规则（脚本 / 诊断 / 配置 / 文档 / 运行产物）
- 备份与诊断文件策略
- 5 条硬规则：不改判据造绿灯 · 改路径必回写引用 · 日志 append-only · 可复现产物即 gitignore · 小步提交
- A/B/C/D 整改清单

### A2 `.gitignore` 补 4 类运行时文件
新增：
```
.workbuddy/                    # 工作日志与项目记忆
avatar-orders/                 # 运行时订单数据
sdz-ptah-app/uploads/          # 网页上传缓存
*.bak-*                        # 临时备份快照
```
效果：工作区待提交项从 60+ 降到 32，只剩该入库的源码。

---

## 三、B 档 · 归档与归位

### B1 备份文件归档（12 个）

从三处源码目录移入 `archive/backups/2026-11-26/`，**保留原目录层级**避免同名冲突：

| 原位置 | 文件 |
|---|---|
| `avatar-orders/avatar/` | `brief.json.bak-before-spec-v1` · `prompt-input.json.bak-before-skin-fix` · `prompt-input.json.bak-before-spec-v1` |
| `sdz-ptah-app/` | `server.py.bak-before-no-web-prompt-check` · `simple_input.py.bak-before-no-web-prompt-check` · `simple_input.py.bak-before-remove-skin-fix` |
| `skills/sdz-ptah-avatar/` | `SKILL.md.bak-before-prompt-spec` |
| `skills/sdz-ptah-avatar/references/` | `modelscope-runbook.md.bak-before-prompt-spec` · `protocol.md.bak-before-prompt-spec` |
| `skills/sdz-ptah-avatar/scripts/` | `avatar_pipeline.py.bak-before-peak-split` · `generate.py.bak-before-normalize` · `generate.py.bak-normalize-reverted-src` |

归档后源码树 `.bak-*` 残留 **0**。

### B2 诊断脚本归位
`ai-anime-avatar/review/` 根目录的 9 个诊断脚本与输出，移入 `review/diagnostics/`。

### B3 引用回写 + 顺带修 bug
- `回归_峰值检测.py` 的 `BAK` 路径改指向归档新位置。
- **既有缺陷修复**：该脚本原用 `spec_from_file_location` 加载 `.bak-*` 后缀会返回 `None`（与文件位置无关，是原代码问题），改用 `SourceFileLoader` 显式加载。
- 验证：3 个样本改前 / 改后一致，无回归。

---

## 四、C 档 · 重命名

### C1 诊断脚本统一 `diag_NN_主题`

| 原名 | 新名 |
|---|---|
| `诊断_朝向检测.py` / `_输出.txt` | `diag_01_朝向检测.py` / `.out.txt` |
| `诊断2_工作图格子.py` / `_输出.txt` | `diag_02_工作图格子.py` / `.out.txt` |
| `诊断3_连通域内幕.py` / `_输出.txt` | `diag_03_连通域内幕.py` / `.out.txt` |
| `诊断4_眼睛区域归属.py` / `_输出.txt` | `diag_04_眼睛区域归属.py` / `.out.txt` |
| `回归_峰值检测.py` | `diag_05_峰值检测回归.py` |

验证：5 个脚本重命名后逐一执行，全部 `exit=0`。

### C2 `verify` 输出目录统一 `out-<变体>`

| 原名 | 新名 |
|---|---|
| `out/` | `out-baseline/` |
| `out_global/` | `out-global/` |
| `out_installed/` | `out-installed/` |
| `out_4k_probe/` | `out-4k-probe/` |
| `control_out/` | `out-control/` |

`.gitignore` 回写：原 5 条规则合并为单条 `ai-anime-avatar/verify/out-*/`。
验证：`git check-ignore` 5 个目录全部 IGNORED；git 状态里 verify 输出 0 项。

---

## 五、引用与日志处理

- **代码引用**：仅 `回归_峰值检测.py` 一处硬编码路径，已回写。
- **工作日志**：`.workbuddy/memory/*.md` 中的旧路径 / 旧文件名是**当时快照，保留不改写**；另在 `2026-11-26.md` 追加「路径迁移记录」「C 档执行」两节说明现路径。
- **规范文档**：`CONVENTIONS.md` 的 B / C 档状态同步为已完成。

---

## 六、未改动项（D 档）

- `ai-anime-avatar/基准/` 中文目录名 — 保留（已 gitignore，manifest 与方案文档有引用）
- 顶层目录重命名 — 保留（README 与所有脚本相对路径依赖）

理由：改动会连锁影响 gitignore、manifest 路径记录、方案文档，收益不抵风险。

---

## 七、回退方式

全部为**移动 / 重命名**，未删除任何文件，可直接反向操作：

| 想回退 | 做法 |
|---|---|
| 备份文件 | 从 `archive/backups/2026-11-26/` 按子目录移回原位置 |
| 诊断脚本 | 按本文第四节的映射表反向改名；如移回 review 根目录，需同时回写 `diag_05` 的 `BAK` 路径 |
| verify 目录 | 按本文第四节的映射表反向改名；`.gitignore` 恢复为原 5 条规则 |
| 规范文档 | 删除 `CONVENTIONS.md`、本文，还原 `.gitignore` 对应段落 |

> 回退前建议先对比 `git status`，确认没有新增的未跟踪文件被误伤。

---

## 八、后续变更：备份已清理（2026-11-26）

上表「备份文件 → 从 `archive/backups/2026-11-26/` 移回」这一回退路径**已失效**——当天确认 12 个备份对应的当前文件全部存在、代码稳定（119 项测试全绿）后，已删除整个 `archive/` 目录。

- 删除前逐一核验：12 个 `.bak-*` 对应的当前文件均存在，无信息丢失。
- `archive/` 本身被 `.gitignore` 的 `*.bak-*` 规则挡在版本库外，删除不影响已提交内容。
- 其余回退路径（诊断脚本、verify 目录、规范文档）不受影响。
