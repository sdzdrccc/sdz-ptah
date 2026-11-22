#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""显式确认入口：把 confirm 块写进 brief.json，作为「未确认不生成」的硬门。

用法：
  py -3 confirm_order.py --brief <brief.json> --confirm "确认生成" [--source card]
  py -3 confirm_order.py --brief <brief.json> --check

- 确认词必须包含：确认 / 生成 / confirm（大小写不敏感），否则拒绝写入。
- --source card 表示确认卡片回传，记录 source=confirmation_card；默认 chat_message。
- 写入前会补齐默认值（画质 1024 / 张数 2 / 朝向按性别 / 预算 10）并校验。
- 写入 confirm.confirmed=true 后，avatar_pipeline.py 才允许评分与导出。
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import avatar_pipeline as ap  # noqa: E402

CONFIRM_WORDS = ("确认", "生成", "confirm")


def params_hash(norm):
    keys = ("gender", "gender_source", "angle", "angle_source",
            "count", "size", "budget", "style_id", "character_id")
    payload = {k: norm.get(k) for k in keys}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


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
    parser.add_argument("--check", action="store_true", help="只检查是否已确认，不写入")
    args = parser.parse_args()

    if not os.path.isfile(args.brief):
        print("brief not found: %s" % args.brief)
        return 2
    with open(args.brief, "r", encoding="utf-8") as fh:
        brief = json.load(fh)

    if args.check:
        ok = (brief.get("confirm") or {}).get("confirmed") is True
        print("confirmed=%s" % ("true" if ok else "false"))
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
    print("confirmed: angle=%s gender=%s count=%d budget=%d source=%s" %
          (norm["angle"], norm["gender"], norm["count"], norm["budget"], src))
    if norm.get("needs_gender_confirmation"):
        print("note: 未指定性别，已按朝左；确认页应已提示用户")
    return 0


if __name__ == "__main__":
    sys.exit(main())
