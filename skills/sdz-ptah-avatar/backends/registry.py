# -*- coding: utf-8 -*-
"""后端注册表：读取 references/backends.json（兼容 1.0 与 2.0）。"""

import json
import os

from .agent_side import AgentSideAdapter
from .base import SCHEMA, CapabilityError
from .comfyui import ComfyUIAdapter
from .dashscope import DashScopeAdapter
from .minimax import MiniMaxAdapter
from .mock import MockAdapter
from .modelscope import ModelScopeAdapter

ENGINES = {
    "comfyui": ComfyUIAdapter,
    "host": AgentSideAdapter,
    "mock": MockAdapter,
    "http_modelscope": ModelScopeAdapter,
    "http_minimax": MiniMaxAdapter,
    "http_dashscope": DashScopeAdapter,
}

# 1.0 条目 id → 2.0 等价规格（保持旧订单可复跑）
_V1_HOST = {
    "agnes-aigc-img2img": {
        "engine": "host",
        "host": {"tool": "agnes_aigc__image_to_image",
                 "params": {"prompt_zh": "original_prompt", "prompt_en": "enhanced_prompt",
                            "reference": "reference_image", "size": "size", "ratio": "ratio"}},
        "capabilities": {"reference_max": 1, "reference_roles": ["identity"],
                         "sizes": [1024, 1920], "stages": ["single", "identity"],
                         "prompt_style": "natural_zh"},
    },
    "trae-generateimage": {
        "engine": "host",
        "host": {"tool": "GenerateImage", "params": {"prompt_zh": "prompt", "size": "image_size"}},
        "capabilities": {"reference_max": 0, "sizes": [1024, 1920],
                         "stages": ["single"], "prompt_style": "natural_zh"},
    },
}


def default_path():
    """优先 2.0 注册表 backends.v2.json；缺失时读 1.0 backends.json（自动映射）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    refs = os.path.join(os.path.dirname(here), "references")
    v2 = os.path.join(refs, "backends.v2.json")
    return v2 if os.path.isfile(v2) else os.path.join(refs, "backends.json")


def skill_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def upgrade_v1(doc):
    """把 1.0 注册表就地映射为 2.0 结构；未知条目标 available=false。"""
    out = {"schema": SCHEMA, "default_backend": doc.get("default_backend"),
           "fallback_policy": doc.get("fallback_policy"), "backends": []}
    for b in doc.get("backends", []):
        spec = dict(_V1_HOST.get(b.get("id"), {}))
        spec.update({k: b[k] for k in ("id", "display_name", "available", "verified", "note") if k in b})
        if not spec.get("engine"):
            spec["engine"] = "unknown"
            spec["available"] = False
        out["backends"].append(spec)
    return out


def load(path=None):
    path = path or os.environ.get("SDZ_BACKENDS") or default_path()
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not str(doc.get("schema", "")).startswith("generation-backends/2"):
        doc = upgrade_v1(doc)
    return doc


def get(doc, backend_id, root=None):
    specs = {b["id"]: b for b in doc.get("backends", [])}
    spec = specs.get(backend_id)
    if spec is None:
        raise CapabilityError("未知后端 %r：先在 references/backends.json 登记" % backend_id)
    cls = ENGINES.get(spec.get("engine"))
    if cls is None:
        raise CapabilityError("后端 %r 的引擎 %r 尚未实现适配器" % (backend_id, spec.get("engine")))
    return cls(spec, root=root or skill_root())


def summary(doc):
    rows = []
    for b in doc.get("backends", []):
        lic = b.get("license") or {}
        rows.append({
            "id": b.get("id"), "engine": b.get("engine"),
            "available": b.get("available") is not False,
            "verified": bool(b.get("verified")),
            "external_reproducible": b.get("external_reproducible"),
            "commercial_use": lic.get("commercial_use"),
            "stages": (b.get("capabilities") or {}).get("stages"),
        })
    return rows
