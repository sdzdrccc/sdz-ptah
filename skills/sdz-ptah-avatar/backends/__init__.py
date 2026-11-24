# -*- coding: utf-8 -*-
"""生成后端适配器包。

用法（在 skill 根目录下）：
  py -3 -m backends list                     # 列出注册表中的后端与许可
  py -3 -m backends probe <backend-id>       # 能力 / 可用性检测（ComfyUI 需已启动）
  py -3 -m backends selftest                 # 用 mock 后端跑一遍单段 + 两段式

生成订单图片请用 scripts/generate.py（含确认门、预算记账与续跑）。
"""

from .base import (EXIT_BACKEND, EXIT_BUDGET, EXIT_CAPABILITY, EXIT_NEEDS_AGENT,
                   INTERFACE_VERSION, SCHEMA, AdapterError, CapabilityError,
                   ConsentError, GeneratedImage, GenerationRequest,
                   GenerationResult, NeedsAgent, ReferenceImage)

__all__ = [
    "SCHEMA", "INTERFACE_VERSION", "EXIT_CAPABILITY", "EXIT_BUDGET", "EXIT_BACKEND",
    "EXIT_NEEDS_AGENT",
    "AdapterError", "CapabilityError", "ConsentError", "NeedsAgent",
    "GenerationRequest", "GenerationResult", "GeneratedImage", "ReferenceImage",
]
