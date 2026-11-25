#!/usr/bin/env python3
"""列出生成方案，并把方案解析成现有的 --stages 参数。

用法：
  py -3 list_presets.py
  py -3 list_presets.py --json
  py -3 list_presets.py --preset modelscope-qwen-2-1 --json

设计边界：
  - references/generation-presets.json 只存用户可理解的方案与推荐参数；
  - backends.v2.json 仍是能力、许可、外发、verified 的真相源；
  - 本脚本只做解析与校验，不调用任何生成后端。
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, SKILL_ROOT)
from backends import registry  # noqa: E402

DEFAULT_PRESETS = os.path.join(SKILL_ROOT, "references", "generation-presets.json")
STAGE_KEYS = ("denoise", "steps", "guidance", "seed", "negative")
SUPPORTED_ENGINES = ("host", "comfyui", "http_modelscope", "mock")


def load_json(path, fallback=None):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
    return fallback


def load_presets(path=None):
    doc = load_json(path or DEFAULT_PRESETS, {})
    presets = doc.get("presets") or []
    if not isinstance(presets, list):
        raise ValueError("generation-presets.json 的 presets 必须是数组")
    return doc, presets


def stages_arg(stages):
    chunks = []
    for s in stages:
        head = "%s:%s" % (s.get("stage"), s.get("backend"))
        opts = []
        for key in STAGE_KEYS:
            if key in (s.get("params") or {}):
                opts.append("%s=%s" % (key, s["params"][key]))
        chunks.append(head + ("@" + ";".join(opts) if opts else ""))
    return ",".join(chunks)


def contract_hash(preset):
    payload = {
        "id": preset.get("id"),
        "stages": preset.get("stages"),
        "mode": preset.get("mode"),
        "requires": preset.get("requires"),
        "cost": preset.get("cost"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _env_missing(names):
    return [n for n in (names or []) if not os.environ.get(n)]


def resolve_presets(registry_path=None, presets_path=None, items=None):
    """返回带注册表派生字段的方案列表。只读，不发请求。

    items：直接给出方案数组（如网页按单个模型临时构造的方案），不读 presets 文件。
    """
    reg = registry.load(registry_path)
    specs = {b.get("id"): b for b in reg.get("backends") or []}
    if items is not None:
        doc, presets = {"schema": "generation-presets/1.0", "version": "registry"}, list(items)
    else:
        doc, presets = load_presets(presets_path)
    result = []
    for p in presets:
        reasons, warnings = [], []
        backend_ids = []
        caps_first = {}
        for s in p.get("stages") or []:
            backend = s.get("backend")
            backend_ids.append(backend)
            spec = specs.get(backend)
            if spec is None:
                reasons.append("后端未登记：%s" % backend)
                continue
            caps = spec.get("capabilities") or {}
            if spec.get("available") is False:
                reasons.append("后端 %s 未接入（available=false）" % backend)
            if spec.get("engine") not in SUPPORTED_ENGINES:
                reasons.append("后端 %s 的引擎 %r 没有适配器" %
                               (backend, spec.get("engine")))
            if s.get("stage") not in (caps.get("stages") or []):
                reasons.append("后端 %s 不支持阶段 %s" % (backend, s.get("stage")))
            for key in STAGE_KEYS:
                if key in (s.get("params") or {}) and not caps.get(key, True):
                    reasons.append("后端 %s 不支持参数 %s" % (backend, key))
            if not caps_first:
                caps_first = dict(caps)

        licenses = [((specs.get(b) or {}).get("license") or {}) for b in backend_ids]
        flags = [x.get("commercial_use") for x in licenses]
        if any(f is False for f in flags):
            commercial = False
        elif any(f is None for f in flags):
            commercial = None
        else:
            commercial = True

        data_egress = any(bool((specs.get(b) or {}).get("data_egress"))
                          for b in backend_ids)
        verified = all(bool((specs.get(b) or {}).get("verified"))
                       for b in backend_ids)
        external = all(bool((specs.get(b) or {}).get("external_reproducible"))
                       for b in backend_ids)
        license_ids = [x.get("id") for x in licenses if x.get("id")]

        requires = dict(p.get("requires") or {})
        missing = _env_missing(requires.get("secrets"))
        if missing:
            reasons.append("未检测到环境变量 %s（token 不回显）" % " / ".join(missing))
        if requires.get("comfyui"):
            warnings.append("需要本机 ComfyUI 已启动且工作流已导出为 API 格式")
        if requires.get("host_tool"):
            warnings.append("宿主工具需要在 WorkBuddy 内执行；独立网页可能只产出调用单")
        if data_egress:
            warnings.append("参考图会发往远端服务，需要用户外发同意")
        if not verified:
            warnings.append("后端尚未真实出图验证（verified=false）")
        if commercial is not True:
            warnings.append("许可不允许按已确认可商用处理；仅作内部测试/需另行确认")

        item = {
            "id": p.get("id"),
            "label": p.get("label") or p.get("id"),
            "scene": p.get("scene") or "",
            "mode": p.get("mode") or "reference",
            "stages": p.get("stages") or [],
            "stages_arg": stages_arg(p.get("stages") or []),
            "backend_ids": backend_ids,
            "contract_sha256": contract_hash(p),
            "requires": requires,
            "cost": p.get("cost") or {},
            "recommended_for": p.get("recommended_for") or [],
            "notes": p.get("notes") or [],
            "reference_max": int(caps_first.get("reference_max") or 0),
            "supports_negative": bool(caps_first.get("negative_prompt")),
            "supports_seed": bool(caps_first.get("seed")),
            "supports_steps": bool(caps_first.get("steps")),
            "supports_guidance": bool(caps_first.get("guidance")),
            "policy": {
                "data_egress": data_egress,
                "commercial_use": commercial,
                "verified": verified,
                "external_reproducible": external,
                "license_ids": license_ids,
            },
            "warnings": warnings,
            "reasons": reasons,
            "selectable": not reasons,
        }
        result.append(item)
    return doc, result


def validate_selection(preset, refs=0, egress_consent=False,
                       text2img_consent=False, commercial=False):
    """确认前校验。返回 (errors, warnings)。"""
    errors = list(preset.get("reasons") or [])
    warnings = list(preset.get("warnings") or [])
    req = preset.get("requires") or {}
    ref_min = int(req.get("reference_min") or 0)
    ref_max = int(req.get("reference_max") or 0)
    if preset.get("mode") == "text2img":
        if not text2img_consent:
            errors.append("文生图方案需要显式同意关闭参考图")
        if refs:
            errors.append("文生图方案不接受参考图")
    else:
        if refs < ref_min:
            errors.append("参考图不足：至少 %d 张，当前 %d 张" % (ref_min, refs))
        if refs > ref_max:
            errors.append("参考图超限：最多 %d 张，当前 %d 张" % (ref_max, refs))
    if req.get("egress") and refs and not egress_consent:
        errors.append("该方案会把参考图发往远端，必须勾选外发同意")
    if commercial and (preset.get("policy") or {}).get("commercial_use") is not True:
        errors.append("该方案不能按可商用订单处理")
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="列出并解析生成方案")
    ap.add_argument("--preset", default=None, help="只看指定方案 id")
    ap.add_argument("--presets", default=None, help="方案 JSON 路径")
    ap.add_argument("--backends", default=None, help="后端注册表路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    try:
        doc, items = resolve_presets(args.backends, args.presets)
    except Exception as exc:  # noqa: BLE001
        print("presets load failed: %s" % exc)
        return 2
    if args.preset:
        items = [x for x in items if x.get("id") == args.preset]
        if not items:
            print("未知方案 %r" % args.preset)
            return 2

    if args.json:
        print(json.dumps({"schema": doc.get("schema"),
                          "version": doc.get("version"),
                          "presets": items}, ensure_ascii=False, indent=2))
        return 0

    print("方案版本 %s · 共 %d 个" % (doc.get("version"), len(items)))
    for x in items:
        flag = "可选" if x["selectable"] else "不可选"
        print("\n[%s] %s — %s" % (flag, x["id"], x["label"]))
        print("  场景：%s" % x["scene"])
        print("  分段：%s" % x["stages_arg"])
        print("  每张：%s 次调用" % (x.get("cost") or {}).get("calls_per_image"))
        for w in x["warnings"]:
            print("  提示：%s" % w)
        for r in x["reasons"]:
            print("  阻断：%s" % r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
