#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式确认入口：把 confirm 块写进 brief.json，作为「未确认不生成」的硬门。

用法：
  py -3 confirm_order.py --brief <brief.json> --confirm "确认生成" [--source card]
  py -3 confirm_order.py --brief <brief.json> --check
  py -3 confirm_order.py --brief <brief.json> \
        --reference "D:/path/ref.png" [--reference-role identity]
  # 后端适配器（generate.py）：分段配置、seed、参考图外发同意
  py -3 confirm_order.py --brief <brief.json> --confirm "确认生成" \
        --stages "identity:comfyui-qwen-edit-2511,style:comfyui-animagine-i2i@denoise=0.35" \
        [--seed 100] [--egress-consent]

- 参考图归档在订单目录内、完全平铺，命名 `<prefix>-ref-N.png`；
  brief.generation.reference_images 记录 `path` / `sha256` / `role`。
- 参考图模式是默认（backend=agnes-aigc-img2img）；text2img 必须显式传
  `--mode text2img --text2img-consent`，流水线会记 identity_unverified=true。
- `--stages` 写入 brief.generation.stages：逗号分隔的 `阶段:后端id[@键=值;键=值]`，
  阶段只允许 single / identity / identity+style；未给时沿用单段 `--backend`。
  键支持 denoise / steps / guidance / seed（数值）与 negative（文本）。
  generation.backend 同步为第一段后端，兼容只读 backend 的旧逻辑。
- 会把参考图发往远端服务的后端（注册表 data_egress=true，如 Agnes / ModelScope），
  generate.py 要求 generation.egress_consent=true；用 `--egress-consent` 记录用户同意。
- 确认词必须包含：确认 / 生成 / confirm（大小写不敏感），否则拒绝写入。
- --source card 表示确认卡片回传，记录 source=confirmation_card；默认 chat_message。
- 写入前会补齐默认值（画质 1024 / 张数 2 / 朝向按性别 / 预算 10）并校验。
- 写入 confirm.confirmed=true 后，avatar_pipeline.py 才允许评分与导出。
  params_hash 覆盖分段配置：改了后端/分段需要重新确认。
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avatar_pipeline as ap  # noqa: E402

CONFIRM_WORDS = ("确认", "生成", "confirm")
REF_EXTS = (".png", ".jpg", ".jpeg", ".webp")
STAGE_COMBOS = (["single"], ["identity"], ["identity", "style"])
STAGE_NUM_KEYS = ("denoise", "steps", "guidance", "seed")


def params_hash(norm):
    keys = ("gender", "gender_source", "angle", "angle_source",
            "count", "size", "budget", "style_id", "character_id")
    payload = {k: norm.get(k) for k in keys}
    gen = norm.get("generation") or {}
    stages = gen.get("stages")
    if stages:
        payload["stages"] = stages
        payload["seed"] = gen.get("seed")
        payload["egress_consent"] = gen.get("egress_consent") is True
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def parse_stages(raw):
    """`identity:qwen,style:animagine-i2i@denoise=0.35;steps=28` -> [{stage, backend, ...}]。"""
    stages, errors = [], []
    for chunk in [c.strip() for c in (raw or "").split(",") if c.strip()]:
        head, _, opts = chunk.partition("@")
        stage, sep, backend = head.partition(":")
        stage, backend = stage.strip(), backend.strip()
        if not sep or not stage or not backend:
            errors.append("分段写法应为 阶段:后端id，收到 %r" % chunk)
            continue
        entry = {"stage": stage, "backend": backend}
        for kv in [o.strip() for o in opts.split(";") if o.strip()]:
            k, eq, v = kv.partition("=")
            k, v = k.strip(), v.strip()
            if not eq or not k:
                errors.append("分段参数应为 键=值，收到 %r" % kv)
                continue
            if k in STAGE_NUM_KEYS:
                try:
                    entry[k] = int(v) if k in ("steps", "seed") else float(v)
                except ValueError:
                    errors.append("%s 必须是数字，收到 %r" % (k, v))
            elif k == "negative":
                entry[k] = v
            else:
                errors.append("不支持的分段参数 %r（允许 %s / negative）"
                              % (k, " / ".join(STAGE_NUM_KEYS)))
        stages.append(entry)
    if stages and [s["stage"] for s in stages] not in STAGE_COMBOS:
        errors.append("阶段组合 %s 不受支持（允许 single / identity / identity,style）"
                      % [s["stage"] for s in stages])
    return stages, errors


def check_stage_backends(stages, registry_path=None):
    """确认前核对分段后端已登记且 available；不调用生成。"""
    try:
        reg = ap.load_json(registry_path or ap.default_backends_path()) or {}
    except Exception as exc:  # 注册表读不到时不拦截确认，交给 generate.py
        return ["后端注册表无法读取：%s" % exc]
    known = {b.get("id"): b for b in reg.get("backends") or []}
    errors = []
    for s in stages:
        b = known.get(s["backend"])
        if b is None:
            errors.append("未知后端 %r：先在 references/backends.v2.json 登记" % s["backend"])
        elif b.get("available") is False:
            errors.append("后端 %r 未接入（available=false）" % s["backend"])
    return errors


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_prefix(order_dir, brief):
    prefix = brief.get("character_id")
    if prefix:
        return re.sub(r"[^0-9A-Za-z_-]+", "-", str(prefix)).strip("-") or "avatar"
    for name in sorted(os.listdir(order_dir)):
        m = re.match(r"(.+)-avatar-\d+(?:-src)?\.(?:png|jpe?g|webp)$", name, re.I)
        if m:
            return m.group(1)
    return "avatar"


def archive_references(brief, order_dir, sources, role, backend, mode, consent):
    """把参考图复制进订单目录（平铺），返回 (generation, errors)。"""
    errors = []
    prefix = detect_prefix(order_dir, brief)
    existing = ((brief.get("generation") or {}).get("reference_images") or [])
    recorded = []
    for it in existing:
        if isinstance(it, dict) and it.get("path"):
            recorded.append(dict(it))
    if mode == "reference" and not sources and not recorded:
        errors.append("参考图模式必须先归档参考图：--reference <路径>")
        return None, errors
    used = set()
    for it in recorded:
        m = re.match(r".+-ref-(\d+)\.[A-Za-z0-9]+$", os.path.basename(it["path"]))
        if m:
            used.add(int(m.group(1)))
    for name in os.listdir(order_dir):
        m = re.match(re.escape(prefix) + r"-ref-(\d+)\.[A-Za-z0-9]+$", name, re.I)
        if m:
            used.add(int(m.group(1)))
    by_sha = {it.get("sha256"): it for it in recorded if it.get("sha256")}

    for src in sources:
        src = os.path.abspath(os.path.expanduser(src))
        if not os.path.isfile(src):
            errors.append("参考图不存在：%s" % src)
            continue
        if not src.lower().endswith(REF_EXTS):
            errors.append("参考图格式不支持（只收 png/jpg/webp）：%s" % src)
            continue
        digest = sha256_file(src)
        if digest in by_sha:
            continue
        n = 1
        while n in used:
            n += 1
        ext = os.path.splitext(src)[1].lower()
        name = "%s-ref-%d%s" % (prefix, n, ext)
        dst = os.path.join(order_dir, name)
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
        else:
            digest = sha256_file(dst)
        used.add(n)
        entry = {"path": name, "sha256": digest, "role": role,
                 "bytes": os.path.getsize(dst),
                 "source": src, "archived_at": datetime.now().isoformat(timespec="seconds")}
        recorded.append(entry)
        by_sha[digest] = entry

    gen = dict(brief.get("generation") or {})
    gen["backend"] = backend or gen.get("backend") or "agnes-aigc-img2img"
    gen["mode"] = mode
    gen["reference_images"] = recorded
    gen["strength"] = None
    gen["fallback"] = gen.get("fallback") or "fail"
    gen["text2img_consent"] = bool(consent)
    return gen, errors


def main():
    parser = argparse.ArgumentParser(description="confirm an avatar order")
    parser.add_argument("--brief", required=True, help="brief.json 路径")
    parser.add_argument("--confirm", default=None, help="用户确认原文")
    parser.add_argument("--source", default="chat", choices=["chat", "card"],
                        help="chat=聊天文字确认；card=确认卡片回传")
    parser.add_argument("--gender", choices=list(ap.GENDERS), default=None,
                        help="可选：写入/覆盖性别")
    parser.add_argument("--angle", choices=list(ap.ANGLE_IDS), default=None,
                        help="可选：写入/覆盖朝向")
    parser.add_argument("--budget", type=int, default=10, help="预算上限（默认 10）")
    parser.add_argument("--reference", default=None,
                        help="参考图路径，逗号分隔；复制到订单目录并记账")
    parser.add_argument("--reference-role", default="identity",
                        choices=["identity", "style", "outfit"],
                        help="参考图角色（默认 identity）")
    parser.add_argument("--backend", default="agnes-aigc-img2img",
                        help="生成后端 id（默认 agnes-aigc-img2img）")
    parser.add_argument("--mode", default="reference",
                        choices=["reference", "text2img"],
                        help="生成模式（默认 reference；text2img 必须配 --text2img-consent）")
    parser.add_argument("--stages", default=None,
                        help="分段配置：阶段:后端id[@键=值;...]，逗号分隔；"
                             "例 identity:comfyui-qwen-edit-2511,style:comfyui-animagine-i2i@denoise=0.35")
    parser.add_argument("--seed", type=int, default=None,
                        help="基础 seed（订单内第 N 张用 seed+N-1，跨轮次不重复）；不给则 generate.py 随机并记录")
    parser.add_argument("--egress-consent", action="store_true",
                        help="用户同意把参考图发往远端服务（Agnes / ModelScope 等 data_egress 后端）")
    parser.add_argument("--backends", default=None,
                        help="后端注册表 JSON（默认 references/backends.v2.json，缺失时用 backends.json）")
    parser.add_argument("--text2img-consent", action="store_true",
                        help="用户显式同意关闭参考图（会记 identity_unverified=true）")
    parser.add_argument("--check", action="store_true", help="只检查是否已确认，不写入")
    args = parser.parse_args()

    if not os.path.isfile(args.brief):
        print("brief not found: %s" % args.brief)
        return 2
    with open(args.brief, "r", encoding="utf-8") as fh:
        brief = json.load(fh)

    if args.check:
        ok = (brief.get("confirm") or {}).get("confirmed") is True
        gen = brief.get("generation") or {}
        refs = gen.get("reference_images") or []
        print("confirmed=%s" % ("true" if ok else "false"))
        print("generation=%s mode=%s refs=%d" %
              (gen.get("backend"), gen.get("mode"), len(refs)))
        if gen.get("stages"):
            print("stages=%s seed=%s egress_consent=%s" %
                  (" -> ".join("%s:%s" % (s.get("stage"), s.get("backend")) for s in gen["stages"]),
                   gen.get("seed"), gen.get("egress_consent") is True))
        return 0 if ok else 4

    text = (args.confirm or "").strip()
    if not text or not any(w in text.lower() for w in CONFIRM_WORDS):
        print("confirmation rejected: 需要明确包含「确认 / 生成 / confirm」")
        return 2

    if args.gender:
        brief["gender"] = args.gender
        brief["gender_source"] = "user"
    if args.angle:
        brief["angle"] = args.angle
        brief["angle_source"] = "user"

    budget = args.budget
    if brief.get("budget") is not None:
        try:
            budget = min(int(brief["budget"]), budget)
        except (TypeError, ValueError):
            pass
    if args.mode == "text2img" and not args.text2img_consent:
        print("text2img 需要用户显式同意：加 --text2img-consent")
        return 2
    stages = None
    if args.stages:
        stages, stage_errors = parse_stages(args.stages)
        stage_errors += check_stage_backends(stages, args.backends) if stages else []
        if stage_errors:
            print("stages invalid: " + "; ".join(stage_errors))
            return 2
    backend = stages[0]["backend"] if stages else args.backend
    order_dir = os.path.dirname(os.path.abspath(args.brief))
    sources = [p.strip() for p in (args.reference or "").split(",") if p.strip()]
    gen, ref_errors = archive_references(brief, order_dir, sources,
                                        args.reference_role, backend,
                                        args.mode, args.text2img_consent)
    if ref_errors:
        print("reference invalid: " + "; ".join(ref_errors))
        return 2
    prev_gen = brief.get("generation") or {}
    if stages:
        gen["stages"] = stages
    elif args.backend != "agnes-aigc-img2img" or not prev_gen.get("stages"):
        # 显式换了单段后端，或之前就没有分段：清掉旧分段，避免与 backend 不一致
        gen.pop("stages", None)
    else:
        gen["stages"] = prev_gen["stages"]
    if args.seed is not None:
        gen["seed"] = args.seed
    elif prev_gen.get("seed") is not None:
        gen["seed"] = prev_gen["seed"]
    gen["egress_consent"] = bool(args.egress_consent or prev_gen.get("egress_consent") is True)
    if gen.get("stages"):
        gen["backend"] = gen["stages"][0]["backend"]
    brief["generation"] = gen
    for it in gen["reference_images"]:
        print("reference: %s  sha256=%s  role=%s" %
              (it["path"], it.get("sha256"), it.get("role")))
    norm, errors = ap.normalize_brief(brief, None, budget)
    if errors:
        print("brief invalid: " + "; ".join(errors))
        return 2

    src = "confirmation_card" if args.source == "card" else "chat_message"
    norm["confirm"] = {
        "confirmed": True,
        "at": datetime.now().isoformat(timespec="seconds"),
        "source": src,
        "text": text,
        "params_hash": params_hash(norm),
        "tool": "scripts/confirm_order.py",
    }
    with open(args.brief, "w", encoding="utf-8") as fh:
        json.dump(norm, fh, ensure_ascii=False, indent=2)
    print("confirmed: angle=%s gender=%s count=%d budget=%d source=%s mode=%s refs=%d" %
          (norm["angle"], norm["gender"], norm["count"], norm["budget"], src,
           norm["generation"]["mode"], len(norm["generation"]["reference_images"])))
    ng = norm["generation"]
    if ng.get("stages"):
        print("stages: %s  seed=%s  egress_consent=%s" %
              (" -> ".join("%s:%s" % (s["stage"], s["backend"]) for s in ng["stages"]),
               ng.get("seed"), ng.get("egress_consent") is True))
    if norm.get("needs_gender_confirmation"):
        print("note: 未指定性别，已按朝左；确认页应已提示用户")
    return 0


if __name__ == "__main__":
    sys.exit(main())
