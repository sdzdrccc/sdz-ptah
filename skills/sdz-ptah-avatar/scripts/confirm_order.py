#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式确认入口：把 confirm 块写进 brief.json，作为「未确认不生成」的硬门。

用法：
  py -3 confirm_order.py --brief <brief.json> --confirm "确认生成" [--source card]
  py -3 confirm_order.py --brief <brief.json> --check
  py -3 confirm_order.py --brief <brief.json> \
        --reference "D:/path/ref.png" [--reference-role identity]

- 参考图归档在订单目录内、完全平铺，命名 `<prefix>-ref-N.png`；
  brief.generation.reference_images 记录 `path` / `sha256` / `role`。
- 参考图模式是默认（backend=agnes-aigc-img2img）；text2img 必须显式传
  `--mode text2img --text2img-consent`，流水线会记 identity_unverified=true。
- 确认词必须包含：确认 / 生成 / confirm（大小写不敏感），否则拒绝写入。
- --source card 表示确认卡片回传，记录 source=confirmation_card；默认 chat_message。
- 写入前会补齐默认值（画质 1024 / 张数 2 / 朝向按性别 / 预算 10）并校验。
- 写入 confirm.confirmed=true 后，avatar_pipeline.py 才允许评分与导出。
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


def params_hash(norm):
    keys = ("gender", "gender_source", "angle", "angle_source",
            "count", "size", "budget", "style_id", "character_id")
    payload = {k: norm.get(k) for k in keys}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


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
    order_dir = os.path.dirname(os.path.abspath(args.brief))
    sources = [p.strip() for p in (args.reference or "").split(",") if p.strip()]
    gen, ref_errors = archive_references(brief, order_dir, sources,
                                        args.reference_role, args.backend,
                                        args.mode, args.text2img_consent)
    if ref_errors:
        print("reference invalid: " + "; ".join(ref_errors))
        return 2
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
    if norm.get("needs_gender_confirmation"):
        print("note: 未指定性别，已按朝左；确认页应已提示用户")
    return 0


if __name__ == "__main__":
    sys.exit(main())
