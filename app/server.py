#!/usr/bin/env python3
"""sdz-ptah 本地网页控制台：只读调用现有流水线脚本，不另造生成逻辑。

只用 Python 标准库。启动：py -3 server.py
浏览器访问：http://127.0.0.1:8765/
"""
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
WEB_DIR = os.path.join(APP_DIR, "web")
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
CUSTOM_CHAR_DIR = os.path.join(APP_DIR, "custom", "characters")
CUSTOM_STYLE_DIR = os.path.join(APP_DIR, "custom", "styles")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8765,
    "open_browser": True,
    "orders_root": os.path.join(PROJECT_ROOT, "orders"),
    "skill_root": os.path.join(PROJECT_ROOT, "skills", "sdz-ptah-avatar"),
    "default_preset": "modelscope-qwen-edit-2511",
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_SKIN_SPEC = {
    "base": {"hex": "#E9C1AB"},
    "shadow": {"hex": "#C99A93"},
    "highlight_cap": {"hex": "#F2DCCB"},
}


def load_json(path, fallback=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return fallback


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_PATH, {}) or {})
    os.makedirs(cfg["orders_root"], exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return cfg


CONFIG = load_config()
SKILL_ROOT = CONFIG["skill_root"]
SCRIPTS = os.path.join(SKILL_ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
from list_presets import resolve_presets, validate_selection  # noqa: E402
# 路径段规整与流水线脚本共用同一份规则（scripts/path_names.py）。
# 两边规则不一致会把同一个订单拆到两个目录，因此这里不再本地实现。
from path_names import (NAME_MAX_LEN, WINDOWS_RESERVED_NAMES,  # noqa: E402,F401
                        safe_slug)
sys.path.insert(0, APP_DIR)
import api_errors  # noqa: E402
import image_prep  # noqa: E402
import orderlock  # noqa: E402
import simple_input  # noqa: E402


def orders_root():
    return CONFIG["orders_root"]


def default_order_name():
    """未填写「保存名称」时的默认目录名：按本地日期生成，例如 avatar-20261126。

    以前这里固定兜底成 "avatar"，导致所有未命名的订单都堆进同一个目录，
    分不出是新单还是续跑。改用日期后，目录名自带可读的区分度。
    """
    return "avatar-" + datetime.now().strftime("%Y%m%d")


def order_character(payload):
    """取建单用的角色标识（订单目录名）。

    缺失即报错。旧版这里兜底成字面量 "satoshi"，后果是：一个空的
    POST /api/orders 就能在真实订单根下静默建出一张 satoshi 单，
    并覆盖同名目录里已有的 brief.json。宁可 400，也不要凭空建单。
    """
    raw = (payload or {}).get("character_id") or (payload or {}).get("character")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("缺少 character_id：建单必须指定角色（不接受空角色名）")
    return safe_name(raw.strip())


def safe_name(value):
    r"""把任意输入规整成单个路径段（目录名 / 文件主干），保留中文等 Unicode 字母。

    规则：
    - NFKC 归一化：全角字母数字折回半角，兼容字符合并（`小雪。` → `小雪.`）
    - 除 Unicode 字母数字、下划线、点、加号、短横外，一律折叠为 `-`；
      因此 `/`、`\`、控制字符等路径分隔与非法字符不可能穿透
    - 去掉首尾的点、空格、短横（Windows 会静默丢弃尾部点与空格）
    - Windows 保留设备名加 `_` 前缀规避
    - 超过 NAME_MAX_LEN 截断
    - 结果必须是单一相对段，空串、`.`、`..` 一律拒绝
    """
    return safe_slug(value, extra=".+")


def order_dir(name):
    path = os.path.join(orders_root(), safe_name(name))
    if not os.path.isdir(path):
        raise ValueError("订单目录不存在：%s" % name)
    return path


def locked(path, channel):
    """同一订单目录的互斥锁（设计第 6 节）；被占用抛 orderlock.OrderBusy。"""
    def _warn(holder, why):
        print("[lock] 接管陈旧锁（%s）：%s" % (why, holder))
    ttl = int(CONFIG.get("lock_ttl_s") or orderlock.DEFAULT_TTL_S)
    return orderlock.order_lock(path, str(channel or "web")[:20], ttl_s=ttl, on_takeover=_warn)


def order_channel(path):
    brief = load_json(os.path.join(path, "brief.json"), {}) or {}
    return brief.get("channel") or "web"


def run_py(script, *args):
    cmd = [sys.executable, script] + [str(a) for a in args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, cwd=SKILL_ROOT, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def last_json(text):
    r"""取输出里最后一个完整的 JSON 对象。

    脚本侧用 `echo()`（`json.dumps(..., indent=2)`）打印结果，是**多行** JSON；
    旧实现逐行 `json.loads` 永远失败，导致 `order_dir` / `order_id` 总走回退值，
    跨秒时会和 `rounds.json` 的 order_id 不一致（R8）。

    只把「行首的 `{`」当候选起点：JSON 字符串里的换行会被转义成 `\n`，
    所以真实文本中行首出现的 `{` 只会是某个 JSON 文档的开头。
    """
    raw = text or ""
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"(?m)^\{", raw))):
        try:
            obj, _end = decoder.raw_decode(raw, match.start())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def custom_character_path(cid):
    return os.path.join(CUSTOM_CHAR_DIR, safe_name(cid) + ".json")


def custom_style_path(sid):
    return os.path.join(CUSTOM_STYLE_DIR, safe_name(sid) + ".json")


def builtin_character_path(cid):
    return os.path.join(SKILL_ROOT, "references", "characters", safe_name(cid) + ".json")


def builtin_style_path(sid):
    return os.path.join(SKILL_ROOT, "references", "styles", safe_name(sid) + ".json")


def find_character_path(cid):
    custom = custom_character_path(cid)
    if os.path.isfile(custom):
        return custom
    builtin = builtin_character_path(cid)
    return builtin if os.path.isfile(builtin) else None


def find_style_path(sid):
    custom = custom_style_path(sid)
    if os.path.isfile(custom):
        return custom
    builtin = builtin_style_path(sid)
    return builtin if os.path.isfile(builtin) else None


def ensure_character(cid, payload, gender):
    """已有档案直接用；有自由描述则在 custom/ 生成一份，不改 skill 原件。"""
    cid = safe_name(cid)
    base_path = find_character_path(cid)
    base = load_json(base_path, {}) or {} if base_path else {}
    desc = str(payload.get("desc") or "").strip()
    desc_en = str(payload.get("desc_en") or "").strip()
    name = str(payload.get("name") or "").strip()
    if base_path and not desc and not desc_en and not name:
        return base_path
    card = dict(base)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    card.update({
        "schema": "character-card/1.1",
        "character_id": cid,
        "display_name": name or base.get("display_name") or cid,
        "version": "web-%s" % stamp,
        "gender": gender,
        "prompt_block": desc or base.get("prompt_block") or cid,
        "prompt_block_en": desc_en or base.get("prompt_block_en") or desc or cid,
        "skin_spec": base.get("skin_spec") or DEFAULT_SKIN_SPEC,
        "face": base.get("face") or {},
    })
    target = custom_character_path(cid)
    save_json(target, card)
    return target


def ensure_style(sid, payload):
    """已有风格直接用；自由描述生成轻量风格档案，画布与评分门槛沿用默认。"""
    sid = safe_name(sid)
    base_path = find_style_path(sid)
    base = load_json(base_path, {}) or {} if base_path else {}
    desc = str(payload.get("desc") or "").strip()
    name = str(payload.get("name") or "").strip()
    if base_path and not desc and not name:
        return base_path
    default = load_json(builtin_style_path("tv-anime-cel"), {}) or {}
    style = dict(base)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    style.update({
        "schema": "style-profile/1.0",
        "style_id": sid,
        "display_name": name or base.get("display_name") or sid,
        "version": "web-%s" % stamp,
        "status": "active",
        "prompt_anchor": desc or base.get("prompt_anchor") or sid,
        "prompt_forbidden": base.get("prompt_forbidden") or default.get("prompt_forbidden") or [],
        "canvas": base.get("canvas") or default.get("canvas") or {},
        "gate": base.get("gate") or default.get("gate") or {},
    })
    target = custom_style_path(sid)
    save_json(target, style)
    return target


def profile_options(folder, id_key):
    rows = []
    if not os.path.isdir(folder):
        return rows
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".json"):
            continue
        doc = load_json(os.path.join(folder, name), {}) or {}
        pid = doc.get(id_key) or os.path.splitext(name)[0]
        rows.append({
            "id": pid,
            "label": doc.get("display_name") or doc.get("name") or pid,
            "custom": os.path.abspath(folder).startswith(os.path.abspath(APP_DIR)),
        })
    return rows


def read_reference_file(path, base_dir):
    text = str(path or "").strip()
    if not text:
        return None
    if os.path.isabs(text):
        return text if os.path.isfile(text) else None
    candidate = os.path.join(base_dir, safe_name(os.path.basename(text)))
    return candidate if os.path.isfile(candidate) else None


def file_entries(folder):
    out = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.lower().endswith(IMAGE_EXTS):
            out.append({"name": name, "size": os.path.getsize(path)})
    return out


def budget_summary(brief, rounds):
    current = (brief or {}).get("order_id") or (rounds or {}).get("order_id")
    used = 0
    for item in ((rounds or {}).get("rounds") or []):
        if current and item.get("order_id") not in (None, current):
            continue
        used += int(item.get("calls") or item.get("count") or 0)
    limit = int((brief or {}).get("budget") or (rounds or {}).get("budget_limit") or 10)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def list_orders():
    rows = []
    for name in sorted(os.listdir(orders_root())):
        path = os.path.join(orders_root(), name)
        if not os.path.isdir(path):
            continue
        brief = load_json(os.path.join(path, "brief.json"), {}) or {}
        rounds = load_json(os.path.join(path, "rounds.json"), {}) or {}
        status = load_json(os.path.join(path, "status.json"), {}) or {}
        gen = brief.get("generation") or {}
        rows.append({
            "name": name,
            "order_id": brief.get("order_id") or rounds.get("order_id") or "",
            "character_id": brief.get("character_id") or name,
            "confirmed": (brief.get("confirm") or {}).get("confirmed") is True,
            "preset_id": gen.get("preset_id"),
            "backend": gen.get("backend"),
            "stages": ["%s:%s" % (s.get("stage"), s.get("backend"))
                       for s in (gen.get("stages") or []) if isinstance(s, dict)],
            "state": status.get("state") or "idle",
            "phase": status.get("phase") or "",
            "budget": budget_summary(brief, rounds),
            "files": len(file_entries(path)),
            "updated_at": status.get("updated_at"),
        })
    return rows


def order_detail(name):
    path = order_dir(name)
    brief = load_json(os.path.join(path, "brief.json"), {}) or {}
    rounds = load_json(os.path.join(path, "rounds.json"), {}) or {}
    status = load_json(os.path.join(path, "status.json"), {}) or {}
    generation = load_json(os.path.join(path, "generation.json"), {}) or {}
    pending = load_json(os.path.join(path, "generation-pending.json"), None)
    prompts = load_json(os.path.join(path, "prompts.json"), {}) or {}
    gen = brief.get("generation") or {}
    return {
        "name": name,
        "path": path,
        "order_id": brief.get("order_id") or rounds.get("order_id") or "",
        "brief": brief,
        "status": status,
        "rounds": rounds,
        "generation": generation,
        "pending": pending,
        "prompt_count": len(prompts.get("prompts") or []),
        "files": file_entries(path),
        "budget": budget_summary(brief, rounds),
        "confirmed": (brief.get("confirm") or {}).get("confirmed") is True,
        "stages": gen.get("stages") or [],
    }


def merge_options(builtin, custom):
    rows = list(builtin)
    seen = {x.get("id") for x in rows}
    for row in custom:
        if row.get("id") in seen:
            continue
        rows.append(row)
    return rows


def meta_payload():
    characters = merge_options(
        profile_options(os.path.join(SKILL_ROOT, "references", "characters"), "character_id"),
        profile_options(CUSTOM_CHAR_DIR, "character_id"),
    )
    styles = merge_options(
        profile_options(os.path.join(SKILL_ROOT, "references", "styles"), "style_id"),
        profile_options(CUSTOM_STYLE_DIR, "style_id"),
    )
    preset_doc, preset_rows = resolve_presets()
    token_set = any(os.environ.get(k) for k in ("MODELSCOPE_TOKEN", "MODELSCOPE_API_KEY"))
    return {
        "simple": simple_preset_info(preset_rows),
        "sizes": simple_input.SIZE_CHOICES,
        "ratios": simple_input.RATIOS,
        "verified_sizes": list(simple_input.VERIFIED_SIZES),
        "count_choices": list(simple_input.COUNT_CHOICES),
        "default_model": CONFIG.get("default_model") or "modelscope-qwen-image-2.1",
        "max_count": simple_input.MAX_COUNT,
        "simple_budget": SIMPLE_BUDGET,
        "project_root": PROJECT_ROOT,
        "skill_root": SKILL_ROOT,
        "orders_root": orders_root(),
        "characters": characters,
        "styles": styles,
        "preset_version": preset_doc.get("version"),
        "modelscope_token_set": token_set,
        "python": sys.version.split()[0],
    }


SIMPLE_BUDGET = 10


def simple_preset_info(presets):
    """极简页用的默认方案摘要：是否可用、不可用原因、是否需要参考图 / 外发同意。"""
    pid = CONFIG.get("default_preset") or "modelscope-qwen-edit-2511"
    p = next((x for x in presets if x.get("id") == pid), None)
    if p is None:
        return {"preset_id": pid, "label": pid, "selectable": False,
                "reasons": ["方案不存在：%s（检查 config.json 的 default_preset）" % pid],
                "warnings": [], "needs_reference": True, "egress": True, "verified": False}
    policy = p.get("policy") or {}
    return {"preset_id": pid, "label": p.get("label") or pid,
            "selectable": bool(p.get("selectable")),
            "reasons": list(p.get("reasons") or []),
            "warnings": list(p.get("warnings") or []),
            "needs_reference": p.get("mode") != "text2img",
            "egress": bool(policy.get("data_egress")),
            "verified": bool(policy.get("verified"))}


ENGINE_GROUP = {"http_modelscope": "魔搭在线", "http_minimax": "官方 API",
                "http_dashscope": "官方 API",
                "comfyui": "本机 ComfyUI", "host": "宿主工具"}


def registry_backends():
    reg = load_json(os.path.join(SKILL_ROOT, "references", "backends.v2.json"), {}) or {}
    return [b for b in reg.get("backends") or [] if isinstance(b, dict) and b.get("id")]


def credits_per_image(spec):
    text = " ".join(str(x) for x in ((spec.get("license") or {}).get("notes"), spec.get("note")) if x)
    m = re.search(r"(\d+(?:\.\d+)?)\s*魔粒\s*/\s*次", text)
    return float(m.group(1)) if m else None


def model_preset(spec, use_reference, params=None):
    """把单个后端临时包装成"单段方案"，交给 list_presets 的同一套校验。"""
    caps = spec.get("capabilities") or {}
    ref_max = int(caps.get("reference_max") or 0)
    mode = "reference" if (use_reference and ref_max) else "text2img"
    engine = spec.get("engine")
    token_env = (spec.get("secrets") or {}).get("token_env")
    stage = {"stage": "single", "backend": spec["id"]}
    if params:
        stage["params"] = dict(params)
    return {
        "id": "model:%s" % spec["id"],
        "label": spec.get("display_name") or spec["id"],
        "scene": ENGINE_GROUP.get(engine, engine or ""),
        "stages": [stage],
        "mode": mode,
        "requires": {
            "reference_min": 1 if mode == "reference" else 0,
            "reference_max": ref_max if mode == "reference" else 0,
            "reference_roles": list(caps.get("reference_roles") or ["identity"]),
            "egress": bool(spec.get("data_egress")),
            "secrets": [token_env] if token_env else [],
            "comfyui": engine == "comfyui",
            "host_tool": engine == "host",
        },
        "cost": {"calls_per_image": 1, "credits_per_image": credits_per_image(spec)},
    }


def resolve_model(spec, use_reference, params=None):
    """返回 (preset_doc, 已解析的方案)。宿主工具后端在网页里只会产出调用单，标为不可选。"""
    doc, rows = resolve_presets(items=[model_preset(spec, use_reference, params)])
    row = rows[0]
    if spec.get("engine") == "host":
        row["reasons"] = list(row.get("reasons") or []) + ["宿主工具只能在 agent 会话里执行，网页无法直接出图"]
        row["selectable"] = False
    if spec.get("engine") == "comfyui":
        # 本机 ComfyUI：需要服务已启动，工作流已导出（available=false 时 resolve_presets 已给出原因）
        note = "需要本机 ComfyUI 服务已启动（默认 127.0.0.1:8188）"
        if note not in (row.get("warnings") or []):
            row["warnings"] = list(row.get("warnings") or []) + [note]
    return doc, row


def scheme_of(spec):
    """方案（大类）：只按接入通道分，不按厂商拆。

    以前这里按模型名再切成「魔搭在线 · Qwen / MiniMax / 其他」，但方案卡的用途
    只是回答"走哪条通道"，具体模型在「模型管理」里选——按厂商拆既多一层点击，
    也会随新模型上线不断长出新的方案卡。故统一归「魔搭在线」。
    """
    engine = spec.get("engine")
    return ENGINE_GROUP.get(engine) or "其他"


def model_options():
    """模型下拉框数据：注册表里支持单段出图的后端 + 能力 + 可用性。"""
    out = []
    for spec in registry_backends():
        caps = spec.get("capabilities") or {}
        if spec.get("engine") == "mock" or "single" not in (caps.get("stages") or []):
            continue
        _, row = resolve_model(spec, bool(caps.get("reference_max")))
        lic = spec.get("license") or {}
        out.append({
            "id": spec["id"],
            "label": spec.get("display_name") or spec["id"],
            "model_name": (spec.get("model") or {}).get("name") or (spec.get("modelscope") or {}).get("model"),
            "engine": spec.get("engine"),
            "group": ENGINE_GROUP.get(spec.get("engine"), spec.get("engine") or "其他"),
            "scheme": scheme_of(spec),
            "selectable": bool(row.get("selectable")),
            "reasons": row.get("reasons") or [],
            "warnings": row.get("warnings") or [],
            "verified": bool(spec.get("verified")),
            "data_egress": bool(spec.get("data_egress")),
            "commercial_use": lic.get("commercial_use"),
            "license_id": lic.get("id"),
            "credits_per_image": credits_per_image(spec),
            "defaults": spec.get("defaults") or {},
            "note": spec.get("note") or "",
            "caps": {
                "negative_prompt": bool(caps.get("negative_prompt")),
                "seed": bool(caps.get("seed")),
                "steps": bool(caps.get("steps")),
                "guidance": bool(caps.get("guidance")),
                "reference_max": int(caps.get("reference_max") or 0),
                "sizes": list(caps.get("sizes") or [1024]),
            },
        })
    return out


def create_order(payload):
    character = order_character(payload)
    style_id = safe_name(payload.get("style_id") or "tv-anime-cel")
    gender = payload.get("gender") or "unspecified"
    if gender not in ("male", "female", "unspecified"):
        raise ValueError("gender 只能是 male / female / unspecified")
    angle = payload.get("angle") or ""
    if angle and angle not in ("front", "profile_left", "profile_right"):
        raise ValueError("angle 不合法")
    count = max(1, min(20, int(payload.get("count") or 1)))
    budget = max(1, min(200, int(payload.get("budget") or 10)))
    channel = str(payload.get("channel") or "web").strip().lower()
    if channel not in ("web", "agent"):
        raise ValueError("channel 只能是 web / agent")

    character_desc = str(payload.get("character_desc") or "").strip()
    character_desc_en = str(payload.get("character_desc_en") or "").strip()
    character_name = str(payload.get("character_name") or "").strip()
    style_desc = str(payload.get("style_desc") or "").strip()
    style_name = str(payload.get("style_name") or "").strip()

    card_path = ensure_character(character, {
        "name": character_name, "desc": character_desc, "desc_en": character_desc_en,
    }, gender)
    style_path = ensure_style(style_id, {"name": style_name, "desc": style_desc})
    card = load_json(card_path, {}) or {}

    rc, out, err = run_py(os.path.join(SCRIPTS, "rounds.py"), "--new-order",
                          "--root", orders_root(), "--character", character)
    if rc != 0:
        return {"ok": False, "rc": rc, "stdout": out, "stderr": err}
    info = last_json(out) or {}
    path = info.get("order_dir") or os.path.join(orders_root(), character)
    oid = info.get("order_id") or datetime.now().strftime("%Y%m%d-%H%M%S")
    brief = {
        "order_id": oid,
        "ordered_at": info.get("ordered_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "character_id": character,
        "character": card.get("display_name") or character_name or character,
        "character_path": card_path,
        "character_desc": character_desc,
        "style_id": style_id,
        "style_path": style_path,
        "style_desc": style_desc,
        "gender": gender,
        "gender_source": "user" if payload.get("gender") else "unspecified",
        "angle": angle,
        "angle_source": "user" if angle else "default",
        "count": count,
        "budget": budget,
        "size": "1024",
        # 网页只负责"生成原图"，不跑 avatar_pipeline.py 的裁切/评分/导出，
        # 所以这里记的 formats 只是本单意图；真正的交付档由流水线 --formats 决定
        # （默认 1024,2k,4k，见 CONVENTIONS §8）。
        "formats": ["1024"],
        "channel": channel,
        "channel_source": "server.py:create_order",
    }
    save_json(os.path.join(path, "brief.json"), brief)
    return {"ok": True, "order": os.path.basename(path), "detail": order_detail(os.path.basename(path)),
            "stdout": out, "stderr": err}


def create_simple_order(name, c):
    """极简建单：不生成角色卡 / 风格档案，提示词由用户直接提供（prompt_source=manual）。"""
    rc, out, err = run_py(os.path.join(SCRIPTS, "rounds.py"), "--new-order",
                          "--root", orders_root(), "--character", name)
    if rc != 0:
        return {"ok": False, "rc": rc, "stdout": out, "stderr": err,
                **api_errors.describe(rc, out, err)}
    info = last_json(out) or {}
    od = info.get("order_dir") or name
    path = od if os.path.isabs(od) else os.path.join(orders_root(), od)
    oid = info.get("order_id") or datetime.now().strftime("%Y%m%d-%H%M%S")
    brief = {
        "order_id": oid,
        "ordered_at": info.get("ordered_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "character_id": name,
        "character": name,
        "style_id": "tv-anime-cel",
        "gender": "unspecified",
        "gender_source": "unspecified",
        "angle": "front",
        "angle_source": "default",
        "count": c["count"],
        "budget": SIMPLE_BUDGET,
        "size": "1024",
        # 同上：网页不产出交付档，formats 仅为意图记录。
        "formats": ["1024"],
        "render_size": "%dx%d" % (c["width"], c["height"]),
        "channel": c["channel"],
        "channel_source": "server.py:simple_generate",
        "prompt_source": "manual",
        "prompts_input": {"positive": c["positive"], "negative": c["negative"]},
        "ui_settings": {k: c.get(k) for k in ("model", "preset_id", "use_reference", "seed",
                                              "steps", "guidance", "count")},
    }
    save_json(os.path.join(path, "brief.json"), brief)
    prompt_doc = {"prompt_zh": c["positive"], "prompt_source": "manual"}
    if c["negative"]:
        prompt_doc["negative"] = c["negative"]
    save_json(os.path.join(path, "prompt-input.json"), prompt_doc)
    return {"ok": True, "order": os.path.basename(path), "path": path, "stdout": out, "stderr": err}


def simple_generate(payload):
    """网页 / agent 的一步式入口：建单 → 存参考图 → 确认 → 出图（或 dry-run）。

    调用方（_dispatch）已持有该名称目录的互斥锁；这里不再加锁。
    """
    c = simple_input.clean(payload)
    name = safe_name(c["name"])
    # 模糊肤色词已在 clean() 里自动换成具体色值；把修改记录和修正后的提示词带回网页
    autofix_info = {"autofix": c.get("autofix") or [],
                    "positive": c["positive"], "negative": c["negative"]}

    if c["model"]:
        spec = next((b for b in registry_backends() if b.get("id") == c["model"]), None)
        if spec is None:
            raise ValueError("未知模型：%s" % c["model"])
        caps = spec.get("capabilities") or {}
        params = {}
        if c["steps"] is not None and caps.get("steps"):
            params["steps"] = c["steps"]
        if c["guidance"] is not None and caps.get("guidance"):
            params["guidance"] = c["guidance"]
        preset_doc, preset = resolve_model(spec, c["use_reference"], params)
        preset_id = preset["id"]
        # 用户主动关掉"图片编辑 / 图生图"开关，本身就是显式选择文生图
        if not c["use_reference"]:
            c["text2img_consent"] = True
    else:
        preset_id = c["preset_id"] or CONFIG.get("default_preset") or "modelscope-qwen-edit-2511"
        preset_doc, presets = resolve_presets()
        preset = next((p for p in presets if p.get("id") == preset_id), None)
        if preset is None:
            raise ValueError("方案不存在：%s" % preset_id)
    if not preset.get("selectable"):
        reasons = list(preset.get("reasons") or ["方案当前不可用"])
        return {"ok": False, "code": "preset_unavailable", "http_status": 503,
                "error": "；".join(reasons), "errors": reasons, "retryable": False}

    needs_ref = preset.get("mode") != "text2img"
    ref = c["reference"] if needs_ref else None   # 无参考图方案忽略已选的图
    if needs_ref and not ref:
        raise ValueError("该方案需要参考图，请先上传")
    raw = None
    if ref:
        data_url = str(ref.get("data") or "")
        if "," not in data_url or not data_url.startswith("data:"):
            raise ValueError("需要 data URL 格式的参考图内容")
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1], validate=False)
        except Exception:  # noqa: BLE001
            raise ValueError("参考图编码无效")
        image_prep.prepare(raw)  # 提前校验能否解码，避免留下空订单

    errors, warnings = validate_selection(preset, refs=1 if ref else 0,
                                          egress_consent=c["egress_consent"],
                                          text2img_consent=c["text2img_consent"],
                                          commercial=False)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings,
                "error": "；".join(errors), **api_errors.describe_messages(errors)}

    created = create_simple_order(name, c)
    if not created.get("ok"):
        return created
    order = created["order"]

    refs = []
    if ref:
        up = upload_reference(order, {"name": ref.get("name") or "reference.png",
                                      "data": ref["data"]})
        refs = [up["path"]]

    conf = confirm_order(order, {"preset_id": preset_id, "confirm_text": "确认生成",
                                 "references": refs, "egress_consent": c["egress_consent"],
                                 "text2img_consent": c["text2img_consent"],
                                 "seed": c["seed"], "budget": SIMPLE_BUDGET},
                         preset=preset, preset_doc=preset_doc)
    conf["order"] = order
    if not conf.get("ok"):
        conf.setdefault("error", "；".join(str(e) for e in conf.get("errors") or []))
        return conf

    res = generate_order(order, {"count": c["count"], "dry_run": c["dry_run"]})
    res["order"] = order
    res["warnings"] = warnings
    res.update(autofix_info)
    if not res.get("ok"):
        res.setdefault("error", (res.get("stderr") or res.get("stdout") or "")[-400:])
    return res


def upload_reference(name, payload):
    path = order_dir(name)
    raw_name = safe_name(os.path.basename(payload.get("name") or "reference.png"))
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in IMAGE_EXTS:
        raise ValueError("只支持 png / jpg / jpeg / webp")
    data_url = str(payload.get("data") or "")
    if "," not in data_url or not data_url.startswith("data:"):
        raise ValueError("需要 data URL 格式的文件内容")
    raw = base64.b64decode(data_url.split(",", 1)[1], validate=False)
    if not raw:
        raise ValueError("文件为空")
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("参考图超过 20MB，请先压缩")
    raw, ext, prep = image_prep.prepare(
        raw,
        max_edge=int(CONFIG.get("image_max_edge") or image_prep.DEFAULT_MAX_EDGE),
        max_bytes=int(CONFIG.get("image_max_bytes") or image_prep.DEFAULT_MAX_BYTES))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_name = "%s-upload-%s%s" % (os.path.basename(path), stamp, ext)
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as fh:
        fh.write(raw)
    return {"ok": True, "name": saved_name, "path": saved_path,
            "size": len(raw), "order": os.path.basename(path), "prepare": prep}


def confirm_order(name, payload, preset=None, preset_doc=None):
    path = order_dir(name)
    brief = load_json(os.path.join(path, "brief.json"), {}) or {}
    if preset is None:
        preset_id = payload.get("preset_id")
        preset_doc, presets = resolve_presets()
        preset = next((p for p in presets if p.get("id") == preset_id), None)
        if preset is None:
            raise ValueError("未知方案：%s" % preset_id)
    preset_doc = preset_doc or {}

    refs = []
    for item in payload.get("references") or []:
        ref = read_reference_file(item, path)
        if ref and ref not in refs:
            refs.append(ref)

    egress = bool(payload.get("egress_consent"))
    text2img = bool(payload.get("text2img_consent"))
    commercial = bool(payload.get("commercial"))
    errors, warnings = validate_selection(preset, refs=len(refs),
                                          egress_consent=egress,
                                          text2img_consent=text2img,
                                          commercial=commercial)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings,
                **api_errors.describe_messages(errors)}

    budget = int(payload.get("budget") or brief.get("budget") or 10)
    args = ["--brief", os.path.join(path, "brief.json"),
            "--confirm", str(payload.get("confirm_text") or "确认生成"),
            "--source", "agent" if brief.get("channel") == "agent" else "web",
            "--budget", str(budget),
            "--mode", preset.get("mode") or "reference",
            "--stages", preset.get("stages_arg"),
            "--backends", os.path.join(SKILL_ROOT, "references", "backends.v2.json")]
    if payload.get("gender"):
        args += ["--gender", str(payload["gender"])]
    if payload.get("angle"):
        args += ["--angle", str(payload["angle"])]
    if refs:
        args += ["--reference", ",".join(refs)]
    if egress:
        args.append("--egress-consent")
    if text2img:
        args.append("--text2img-consent")
    if payload.get("seed") not in (None, ""):
        args += ["--seed", str(int(payload["seed"]))]

    rc, out, err = run_py(os.path.join(SCRIPTS, "confirm_order.py"), *args)
    result = {"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err,
              "warnings": warnings, "errors": [] if rc == 0 else [err or out]}
    result.update(api_errors.describe(rc, out, err, path))
    if rc == 0:
        brief = load_json(os.path.join(path, "brief.json"), {}) or {}
        gen = brief.setdefault("generation", {})
        gen["preset_id"] = preset.get("id")
        gen["preset_version"] = preset_doc.get("version")
        gen["preset_snapshot"] = {
            "label": preset.get("label"),
            "stages_arg": preset.get("stages_arg"),
            "contract_sha256": preset.get("contract_sha256"),
            "warnings": warnings,
            "notes": preset.get("notes") or [],
        }
        save_json(os.path.join(path, "brief.json"), brief)
        result["detail"] = order_detail(name)
    return result


def generate_order(name, payload):
    path = order_dir(name)
    extra = []
    compile_out = ""
    if payload.get("resume"):
        extra.append("--resume")
    elif payload.get("discard_pending"):
        extra.append("--discard-pending")
    elif (load_json(os.path.join(path, "brief.json"), {}) or {}).get("prompt_source") == "manual":
        # 手写提示词订单：不经 compile_prompt.py，提示词原样交给 generate.py（仍过 audit_prompts）
        prompt_file = os.path.join(path, "prompt-input.json")
        if not os.path.isfile(prompt_file):
            raise ValueError("缺少 prompt-input.json：手写提示词订单必须有它")
        extra += ["--prompt-file", prompt_file]
        if payload.get("count"):
            extra += ["--count", str(int(payload["count"]))]
    else:
        brief = load_json(os.path.join(path, "brief.json"), {}) or {}
        gen = brief.get("generation") or {}
        backend = gen.get("backend")
        stages = gen.get("stages") or []
        if not backend and stages:
            backend = stages[0].get("backend")
        backend = backend or "agnes-aigc-img2img"
        character = safe_name(brief.get("character_id") or os.path.basename(path))
        style_id = safe_name(brief.get("style_id") or "tv-anime-cel")
        card = brief.get("character_path") or find_character_path(character)
        style = brief.get("style_path") or find_style_path(style_id)
        if not card or not os.path.isfile(card):
            raise ValueError("角色卡不存在：%s" % card)
        if not style or not os.path.isfile(style):
            style = os.path.join(SKILL_ROOT, "references", "styles", "tv-anime-cel.json")
        prompt_file = os.path.join(path, "prompts.json")
        cp_args = ["--character", card, "--style", style, "--backend", backend,
                   "--angle", brief.get("angle") or "front",
                   "--mode", gen.get("mode") or "reference",
                   "--out", prompt_file]
        crc, cPout, cperr = run_py(os.path.join(SCRIPTS, "compile_prompt.py"), *cp_args)
        compile_out = cPout
        if crc != 0:
            return {"ok": False, "stage": "compile", "rc": crc,
                    "stdout": cPout, "stderr": cperr,
                    **api_errors.describe(crc, cPout, cperr, path)}
        extra += ["--prompt-file", prompt_file]
        if payload.get("count"):
            extra += ["--count", str(int(payload["count"]))]
    if payload.get("dry_run"):
        extra.append("--dry-run")
    if payload.get("allow_over_budget"):
        extra.append("--allow-over-budget")

    rc, out, err = run_py(os.path.join(SCRIPTS, "generate.py"),
                          "--order", path, *extra)
    result = {"ok": rc == 0, "rc": rc, "stdout": compile_out + out, "stderr": err,
              "detail": order_detail(name)}
    result.update(api_errors.describe(rc, out, err, path))
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[web] " + (fmt % args))

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return json.loads(raw or "{}")

    def _result(self, res):
        """处理函数返回的 dict 里若带 http_status，用它作为响应状态码。"""
        status = 200
        if isinstance(res, dict):
            status = int(res.pop("http_status", 200) or 200)
        return self._json(res, status)

    def _static(self, path):
        rel = unquote(path).lstrip("/") or "index.html"
        target = os.path.abspath(os.path.join(WEB_DIR, rel))
        if os.path.commonpath([WEB_DIR, target]) != WEB_DIR or not os.path.isfile(target):
            self._json({"ok": False, "error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, filename):
        try:
            path = os.path.join(order_dir(name), safe_name(os.path.basename(unquote(filename))))
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
            return
        if not os.path.isfile(path):
            self._json({"ok": False, "error": "file not found"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method):
        path = urlparse(self.path).path
        try:
            if method == "GET":
                if path == "/api/meta":
                    return self._json({"ok": True, "meta": meta_payload()})
                if path == "/api/presets":
                    doc, presets = resolve_presets()
                    return self._json({"ok": True, "version": doc.get("version"),
                                       "presets": presets})
                if path == "/api/models":
                    return self._json({"ok": True, "models": model_options()})
                if path == "/api/orders":
                    return self._json({"ok": True, "orders": list_orders()})
                m = re.match(r"^/api/orders/([^/]+)/file/(.+)$", path)
                if m:
                    return self._file(unquote(m.group(1)), m.group(2))
                m = re.match(r"^/api/orders/([^/]+)$", path)
                if m:
                    return self._json({"ok": True, "detail": order_detail(unquote(m.group(1)))})
                return self._static(path)

            payload = self._read_json()
            if path == "/api/generate":
                character = safe_name((payload or {}).get("name") or default_order_name())
                with locked(os.path.join(orders_root(), character), (payload or {}).get("channel")):
                    result = simple_generate(payload)
                return self._result(result)
            if path == "/api/orders":
                character = order_character(payload)
                with locked(os.path.join(orders_root(), character), payload.get("channel")):
                    result = create_order(payload)
                return self._result(result)
            m = re.match(r"^/api/orders/([^/]+)/upload-reference$", path)
            if m:
                return self._json(upload_reference(unquote(m.group(1)), payload))
            m = re.match(r"^/api/orders/([^/]+)/confirm$", path)
            if m:
                name = unquote(m.group(1))
                path_ = order_dir(name)
                with locked(path_, order_channel(path_)):
                    result = confirm_order(name, payload)
                return self._result(result)
            m = re.match(r"^/api/orders/([^/]+)/generate$", path)
            if m:
                name = unquote(m.group(1))
                path_ = order_dir(name)
                with locked(path_, order_channel(path_)):
                    result = generate_order(name, payload)
                return self._result(result)
            return self._json({"ok": False, "error": "not found"}, 404)
        except orderlock.OrderBusy as exc:
            return self._result(api_errors.busy(exc))
        except ValueError as exc:
            return self._json({"ok": False, "code": "bad_input", "error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._json({"ok": False, "error": repr(exc)}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def main():
    host = CONFIG.get("host") or "127.0.0.1"
    port = int(CONFIG.get("port") or 8765)
    url = "http://%s:%d/" % (host, port)
    if CONFIG.get("open_browser"):
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    server = ThreadingHTTPServer((host, port), Handler)
    print("sdz-ptah web console: " + url)
    print("orders root: " + orders_root())
    print("Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
