# sdz-ptah

用 agent 侧生图能力批量生成精致动漫头像，覆盖「一句话需求 → 候选图交付」的完整流程：
需求 → 提示词编译 → 4 变体 × 2 次批量出图 → 裁切补偿 → 裁切后评分与硬门槛 → 联系表 / 候选清单 + PNG / JPG 导出。

## 名字由来

`sdz-ptah` 取自埃及工匠之神 **Ptah**——以心思构想、以言语造物，对应这个项目的核心：
**用一句话生成一个形象**。

## 能力边界（写实话）

- **不承诺同一角色不变脸**：生成端没有 seed、参考图与 LoRA，只能做到「同批次看起来像同一角色」。
- **生成不可复现**：同一段提示词两次结果不同，台账只记录提示词与时间。
- **构图靠裁切补偿**：生成图眼睛稳定偏低，不靠提示词修构图，由裁切把眼睛抬到上部 1/3。
- **2K / 4K 是插值放大**：用 Pillow Lanczos 从原生约 1000px 的裁切放大，不新增细节，倍率越高越软。
- **部分检查不可用**：OCR、NSFW、情绪、风格、解剖与动漫超分（Real-ESRGAN 等）当前无本地依赖，对应闸门记为 `null`。

## 安装

在仓库根目录执行：

```bash
# 主写法
python skills/install.py

# Windows 备选：python 报 9009 说明没进 PATH，用 py -3
py -3 skills/install.py
```

默认安装到 TRAE 的全局技能目录 `%USERPROFILE%\.trae-cn\skills\sdz-ptah-avatar\`。
这个路径由 TRAE 规定、不能自选；装好后本机所有 TRAE 工作区与 agent 会话都能加载。

- 默认优先创建符号链接，改源码立即生效；
- Windows 无创建符号链接权限时自动回退为复制；
- **本机当前是复制模式**，改完 `skills/sdz-ptah-avatar/` 下的源码后需要重新执行上面的脚本同步。

装到其他宿主：

```bash
# 只扫描并打印本机实际存在的宿主技能目录，不安装
python skills/install.py --detect

# 装到指定宿主的技能目录：<target>/sdz-ptah-avatar/
python skills/install.py --target ~/.codex/skills
```

本机实测检测到 `skills/` 的宿主目录：`.trae-cn`（默认）、`.agents`、`.cc-switch`、`.codex`、
`.hub`、`.qoder-cn`、`.qoderwork`、`.qwenworkcn`、`.stepfun`、`.workbuddy`、
`.workbuddy-ai`、`.config/mimocode`。默认安装只写 TRAE 的 `.trae-cn\skills\`，其他目录未写入。

## 使用

在新会话里直接说需求即可，例如：

> 帮我生成一个银白长发、蓝瞳、清冷感的头像，做社交平台圆头像。

可选参数：默认输出 PNG（1024）；需要高清可指定 2K / 4K；有体积限制的网站或 App 可要 JPG。
每个需求最多 12 次生成调用，首轮 8 张（4 变体 × 2 次），不足再补量。

## 目录

| 目录 | 内容 |
|-|-|
| `skills/` | skill 源码（`SKILL.md`、`references/`、`scripts/`）与 `install.py` |
| `ai-anime-avatar/outputs/` | 方案文档（设计依据与阈值来源） |
| `ai-anime-avatar/verify/` | 验证用的需求单、生成原图与对照样本 |

验证生成的成品图（`verify/out*/`）体积较大且可由脚本复现，已加入 `.gitignore`，不随仓库提交。

## 依赖

- Python 3（安装脚本只用标准库）
- Pillow（裁切与评分脚本依赖，不需要 numpy / OpenCV）
- 动漫超分需额外依赖，当前**不可用**；替代路径见方案文档附录 D。
