# -*- coding: utf-8 -*-
"""出图入口 /api/generate 的输入校验（无第三方依赖，便于单测）。

网页字段：名称、正向 / 反向提示词、模型、参考图开关与参考图、尺寸、数量、
随机种子、采样步数、提示词引导系数（CFG）、外发同意。
模型不支持的参数由服务端按注册表能力丢弃，这里只做格式与范围校验。
"""
import re

MAX_COUNT = 10            # 与默认预算 10 次调用对齐
COUNT_CHOICES = (1, 2, 4)
MAX_PROMPT_CHARS = 4000
MIN_EDGE, MAX_EDGE, STEP = 512, 2048, 8
STEPS_RANGE = (1, 100)
GUIDANCE_RANGE = (0.0, 20.0)
SEED_MAX = 2 ** 32 - 1

# 预置画幅；实际像素由"长边"决定（见 ratio_size）
RATIOS = [
    {"id": "1:1", "w": 1, "h": 1, "label": "正方形"},
    {"id": "3:4", "w": 3, "h": 4, "label": "纵向"},
    {"id": "2:3", "w": 2, "h": 3, "label": "纵向"},
    {"id": "9:16", "w": 9, "h": 16, "label": "纵向"},
    {"id": "4:3", "w": 4, "h": 3, "label": "横向"},
    {"id": "3:2", "w": 3, "h": 2, "label": "横向"},
    {"id": "16:9", "w": 16, "h": 9, "label": "横向"},
]
VERIFIED_SIZES = ("1024x1024",)   # 有真实出图记录的尺寸

# 旧版页面用的尺寸下拉（保留兼容）
SIZE_CHOICES = [
    {"id": "1024x1024", "label": "1024×1024 方图", "verified": True},
    {"id": "1536x1536", "label": "1536×1536 方图（未验证）", "verified": False},
    {"id": "1024x1536", "label": "1024×1536 竖图（未验证）", "verified": False},
    {"id": "1536x1024", "label": "1536×1024 横图（未验证）", "verified": False},
]

_SIZE_RE = re.compile(r"^\s*(\d{3,4})\s*[xX×*]\s*(\d{3,4})\s*$")


def _round8(v):
    # 与前端 Math.round 一致（四舍五入，不用银行家舍入）
    return max(MIN_EDGE, min(MAX_EDGE, int(v / float(STEP) + 0.5) * STEP))


def ratio_size(ratio_id, long_edge):
    """('3:4', 1328) → (996, 1328)；长边固定，短边按比例取 8 的倍数。"""
    r = next((x for x in RATIOS if x["id"] == ratio_id), None)
    if r is None:
        raise ValueError("未知画幅：%s" % ratio_id)
    long_edge = int(long_edge)
    if r["w"] >= r["h"]:
        return _round8(long_edge), _round8(long_edge * r["h"] / float(r["w"]))
    return _round8(long_edge * r["w"] / float(r["h"])), _round8(long_edge)


def parse_size(value, default=(1024, 1024)):
    """'1024x1536' → (1024, 1536)。范围 512–2048 且为 8 的倍数，否则 ValueError。"""
    if value in (None, ""):
        return default
    if isinstance(value, (list, tuple)) and len(value) == 2:
        raw = value
    else:
        m = _SIZE_RE.match(str(value))
        if not m:
            raise ValueError("尺寸格式应为 宽x高，例如 1024x1024")
        raw = m.groups()
    try:
        w, h = int(raw[0]), int(raw[1])
    except (TypeError, ValueError):
        raise ValueError("尺寸格式应为 宽x高，例如 1024x1024")
    for v in (w, h):
        if v < MIN_EDGE or v > MAX_EDGE or v % STEP:
            raise ValueError("尺寸每边需在 %d–%d 之间且为 %d 的倍数，收到 %dx%d"
                             % (MIN_EDGE, MAX_EDGE, STEP, w, h))
    return w, h


def _text(value):
    return str(value if value is not None else "").strip()


def _opt_int(payload, key, lo, hi, label):
    raw = payload.get(key)
    if raw in (None, ""):
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ValueError("%s必须是整数" % label)
    if v < lo or v > hi:
        raise ValueError("%s需在 %d–%d 之间" % (label, lo, hi))
    return v


def _opt_float(payload, key, lo, hi, label):
    raw = payload.get(key)
    if raw in (None, ""):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError("%s必须是数字" % label)
    if v < lo or v > hi:
        raise ValueError("%s需在 %g–%g 之间" % (label, lo, hi))
    return round(v, 2)


def _seed(payload):
    raw = payload.get("seed")
    if raw in (None, ""):
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise ValueError("随机种子必须是整数（-1 表示随机）")
    if v == -1:
        return None
    if v < 0 or v > SEED_MAX:
        raise ValueError("随机种子需为 -1 或 0–%d" % SEED_MAX)
    return v


def clean(payload):
    """校验并规整请求体；不合法抛 ValueError（HTTP 400）。"""
    payload = payload or {}
    positive = _text(payload.get("positive") or payload.get("prompt"))
    negative = _text(payload.get("negative") or payload.get("negative_prompt"))
    if not positive:
        raise ValueError("正向提示词不能为空")
    # 网页端不再检查或改写提示词：原文原样保留，不做自动替换
    autofix = []
    for label, text in (("正向", positive), ("反向", negative)):
        if len(text) > MAX_PROMPT_CHARS:
            raise ValueError("%s提示词超过 %d 字" % (label, MAX_PROMPT_CHARS))

    raw_count = payload.get("count")
    try:
        count = 1 if raw_count in (None, "") else int(raw_count)
    except (TypeError, ValueError):
        raise ValueError("数量必须是整数")
    if count < 1 or count > MAX_COUNT:
        raise ValueError("数量必须在 1–%d 之间" % MAX_COUNT)

    width, height = parse_size(payload.get("size"))

    channel = _text(payload.get("channel") or "web").lower()
    if channel not in ("web", "agent"):
        raise ValueError("channel 只能是 web / agent")

    ref = payload.get("reference")
    if ref is not None and not isinstance(ref, dict):
        raise ValueError("reference 需要是 {name, data} 对象")
    if ref is not None and not _text(ref.get("data")):
        ref = None

    use_reference = payload.get("use_reference")
    use_reference = (ref is not None) if use_reference is None else bool(use_reference)
    if not use_reference:
        ref = None

    return {
        "autofix": autofix,
        "name": _text(payload.get("name") or payload.get("character_id")) or "avatar",
        "positive": positive,
        "negative": negative,
        "count": count,
        "width": width,
        "height": height,
        "channel": channel,
        "dry_run": bool(payload.get("dry_run")),
        "egress_consent": bool(payload.get("egress_consent")),
        "text2img_consent": bool(payload.get("text2img_consent")),
        "preset_id": _text(payload.get("preset_id")),
        "model": _text(payload.get("model")),
        "use_reference": use_reference,
        "seed": _seed(payload),
        "steps": _opt_int(payload, "steps", STEPS_RANGE[0], STEPS_RANGE[1], "采样步数"),
        "guidance": _opt_float(payload, "guidance", GUIDANCE_RANGE[0], GUIDANCE_RANGE[1],
                               "提示词引导系数"),
        "reference": ref,
    }
