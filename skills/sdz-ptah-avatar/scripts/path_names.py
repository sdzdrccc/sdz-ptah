# -*- coding: utf-8 -*-
"""路径段规整（slug）：所有"把名字变成目录名 / 文件前缀"的地方共用这一份。

为什么单独成模块
----------------
原来有四处各写了一份正则：

    server.py:safe_name              允许 . + -，保留大小写
    rounds.py:order_dir_for          只允许 [0-9A-Za-z_-]，转小写
    confirm_order.py:detect_prefix   只允许 [0-9A-Za-z_-]
    generate.py:detect_prefix        只允许 [0-9A-Za-z_-]

规则不一致时同一个订单会被拆到两个目录；而且旧规则会把中文全部折叠成
`order` / `avatar`，导致**不同中文角色共用一个目录**。本模块把这些统一。

只用标准库；随 skill 一起分发（scripts/ 在 sys.path 首位，故可直接 import）。
"""

import re
import unicodedata

# 单个路径段最大字符数：中文按 UTF-8 每字 3 字节，64 字约 192 字节，
# 低于 NTFS 单段 255 字节上限，也给订单根目录前缀留出余量。
NAME_MAX_LEN = 64

# Windows 保留设备名：即使带扩展名也不允许（CON.txt 同样非法）。
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)

_MISSING = object()


def safe_slug(value, lower=False, extra="", fallback=_MISSING):
    """把任意输入规整成单个路径段（目录名 / 文件前缀）。

    参数
    ----
    value     原始值，允许 None（按空串处理）
    lower     是否转小写。rounds.py 的目录名约定转小写，保持既有目录不变
    extra     额外允许的字符。server.py 传 ".+" 以兼容既有的点号 slug；
              脚本侧一律不传，保持旧的字符集
    fallback  结果为空时返回它；不传则抛 ValueError

    规则
    ----
    1. NFKC 归一化：全角字母数字折回半角，兼容字符合并（`小雪。` → `小雪.`）
    2. 除 Unicode 字母数字、下划线、短横（及 extra）外，一律折叠为 `-`；
       因此 `/`、`\\`、控制字符等路径分隔与非法字符不可能穿透
    3. 连续短横合并，去掉首尾的点、空格、短横
    4. Windows 保留设备名加 `_` 前缀规避
    5. 超过 NAME_MAX_LEN 截断
    6. 结果必须是单一相对段；空串、`.`、`..` 走 fallback 或抛错

    幂等：safe_slug(safe_slug(x)) == safe_slug(x)。
    """
    raw = unicodedata.normalize("NFKC", str(value if value is not None else "")).strip()
    text = ""
    if raw:
        text = re.sub("[^\\w%s]+" % re.escape(extra), "-", raw)
        text = re.sub(r"-{2,}", "-", text)
        text = text.strip("- ." + extra)
        if text in (".", ".."):
            text = ""
    if text:
        if lower:
            text = text.lower()
        if text.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
            text = "_" + text
        if len(text) > NAME_MAX_LEN:
            text = text[:NAME_MAX_LEN].strip("- ." + extra)
            if text in (".", ".."):
                text = ""
    if not text:
        if fallback is _MISSING:
            raise ValueError(
                "名称不合法：%r（需至少包含一个字母、数字或中文）" % (value,))
        return fallback
    return text
