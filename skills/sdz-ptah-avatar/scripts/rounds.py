#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轮次与预算台账：交付后满意度回访、改进方向解析、按轮产物目录。

改进方向目录来自 ../references/feedback.json，脚本内不硬编码方向。

用法：
  # 查看预算余额（余额 <=0 退出码 5）
  py -3 rounds.py --order <订单目录> --check

  # 解析改进方向，输出落到 prompt 的措辞（不生成、不记账）
  py -3 rounds.py --order <订单目录> --preview --directions composition,detail

  # 记录一轮（自动创建 raw/round_XX 与 out/round_XX）
  py -3 rounds.py --order <订单目录> --record --round 2 --count 2 \
        --directions composition,detail --satisfaction unsatisfied

  # 回填满意度
  py -3 rounds.py --order <订单目录> --set-satisfaction --round 1 \
        --satisfaction satisfied

预算按轮累计：累计超过 budget_limit 且未加 --allow-over-budget 时直接拒绝（退出码 5）。
"""

import argparse
import json
import os
import sys
from datetime import datetime

SCHEMA = "avatar-rounds/1.0"
DEFAULT_BUDGET = 6
SATISFACTIONS = ("satisfied", "unsatisfied")

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_BUDGET = 5
EXIT_DUPLICATE = 6

HERE = os.path.dirname(os.path.abspath(__file__))
FEEDBACK_DEFAULT = os.path.normpath(
    os.path.join(HERE, "..", "references", "feedback.json"))


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_feedback(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    catalog = {}
    order = []
    for item in data.get("directions") or []:
        did = item.get("id")
        if not did:
            continue
        catalog[did] = item
        order.append(did)
    return catalog, order


def parse_ids(raw):
    if not raw:
        return []
    parts = []
    for chunk in str(raw).replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    out = []
    for pid in parts:
        if pid not in out:
            out.append(pid)
    return out


def resolve_directions(ids, catalog, available):
    unknown = [i for i in ids if i not in catalog]
    if unknown:
        raise ValueError("未知改进方向: %s（可选: %s）"
                         % ("/".join(unknown), "/".join(available)))
    resolved = []
    for did in ids:
        item = catalog[did]
        resolved.append({
            "id": did,
            "label": item.get("label"),
            "target": item.get("target"),
            "prompt_clause": item.get("prompt_clause"),
            "angle_flip": bool(item.get("angle_flip")),
        })
    return resolved


def build_appendix(directions):
    return "；".join(d["prompt_clause"] for d in directions
                    if d.get("prompt_clause"))


def rounds_path(order_dir):
    return os.path.join(order_dir, "rounds.json")


def default_budget(order_dir):
    brief_path = os.path.join(order_dir, "brief.json")
    if os.path.isfile(brief_path):
        try:
            with open(brief_path, "r", encoding="utf-8") as fh:
                brief = json.load(fh)
            value = brief.get("budget")
            if value is None:
                value = (brief.get("brief_effective") or {}).get("budget")
            if value is not None:
                return max(1, int(value))
        except (OSError, ValueError):
            pass
    return DEFAULT_BUDGET


def load_ledger(order_dir, bud=None):
    path = rounds_path(order_dir)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError("rounds.json 无法解析: %s" % exc)
    else:
        data = {"schema": SCHEMA, "budget_limit": default_budget(order_dir)}
    if not data.get("schema"):
        data["schema"] = SCHEMA
    data.setdefault("order_id", os.path.basename(os.path.normpath(order_dir)))
    data.setdefault("budget_used", 0)
    rounds = data.setdefault("rounds", [])
    data["budget_used"] = sum(int(r.get("count") or 0) for r in rounds)
    limit = bud if bud is not None else data.get("budget_limit")
    data["budget_limit"] = max(1, int(limit if limit is not None
                                      else DEFAULT_BUDGET))
    data["remaining"] = data["budget_limit"] - data["budget_used"]
    data.setdefault("satisfaction", None)
    data.setdefault("updated_at", now_text())
    return data


def save_ledger(order_dir, data):
    data["budget_used"] = sum(int(r.get("count") or 0)
                              for r in data.get("rounds", []))
    data["remaining"] = data["budget_limit"] - data["budget_used"]
    data["updated_at"] = now_text()
    path = rounds_path(order_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def echo(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def do_check(order_dir, budget):
    data = load_ledger(order_dir, budget)
    exhausted = data["remaining"] <= 0
    echo({"order_id": data["order_id"],
          "budget_limit": data["budget_limit"],
          "budget_used": data["budget_used"],
          "remaining": max(0, data["remaining"]),
          "exhausted": exhausted,
          "rounds": len(data.get("rounds", []))})
    if exhausted:
        print("预算用尽：已用 %d / 上限 %d。追加预算需用户再次确认。"
              % (data["budget_used"], data["budget_limit"]))
        return EXIT_BUDGET
    return EXIT_OK


def do_preview(order_dir, budget, ids, catalog, available):
    directions = resolve_directions(ids, catalog, available)
    payload = {
        "order_id": os.path.basename(os.path.normpath(order_dir)),
        "directions": directions,
        "prompt_appendix": build_appendix(directions),
        "angle_flip": any(d.get("angle_flip") for d in directions),
    }
    if os.path.isdir(order_dir):
        data = load_ledger(order_dir, budget)
        payload["budget"] = {"limit": data["budget_limit"],
                             "used": data["budget_used"],
                             "remaining": max(0, data["remaining"])}
    echo(payload)
    return EXIT_OK


def do_record(order_dir, budget, round_no, count, ids, catalog, available,
              satisfaction, allow_over):
    if not os.path.isdir(order_dir):
        print("订单目录不存在: %s" % order_dir)
        return EXIT_INPUT
    if round_no < 1:
        print("--round 必须 >= 1")
        return EXIT_INPUT
    if count < 1:
        print("--count 必须 >= 1")
        return EXIT_INPUT
    if satisfaction is not None and satisfaction not in SATISFACTIONS:
        print("--satisfaction 只能是 %s" % " / ".join(SATISFACTIONS))
        return EXIT_INPUT
    directions = resolve_directions(ids, catalog, available)

    data = load_ledger(order_dir, budget)
    used = data["budget_used"]
    if round_no in [int(r.get("round") or 0) for r in data["rounds"]]:
        print("第 %d 轮已存在，历史轮次不覆盖。" % round_no)
        return EXIT_DUPLICATE
    if used + count > data["budget_limit"] and not allow_over:
        print("预算用尽：已用 %d / 上限 %d，本轮再要 %d 张会超额。"
              % (used, data["budget_limit"], count))
        print("必须先询问用户是否追加预算；用户确认后再加 "
              "--allow-over-budget（并可用 --budget 调整上限）。")
        return EXIT_BUDGET

    tag = "round_%02d" % round_no
    raw_dir = os.path.join(order_dir, "raw", tag)
    out_dir = os.path.join(order_dir, "out", tag)
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    entry = {
        "round": round_no,
        "count": count,
        "directions": directions,
        "budget_used": used + count,
        "generated_at": now_text(),
        "satisfaction": satisfaction,
        "raw_dir": os.path.relpath(raw_dir, order_dir).replace("\\", "/"),
        "out_dir": os.path.relpath(out_dir, order_dir).replace("\\", "/"),
    }
    data["rounds"].append(entry)
    if satisfaction is not None:
        data["satisfaction"] = satisfaction
    save_ledger(order_dir, data)
    echo({"recorded": entry,
          "budget": {"limit": data["budget_limit"],
                     "used": data["budget_used"],
                     "remaining": max(0, data["remaining"])}})
    return EXIT_OK


def do_set_satisfaction(order_dir, budget, round_no, satisfaction):
    if satisfaction not in SATISFACTIONS:
        print("--satisfaction 只能是 %s" % " / ".join(SATISFACTIONS))
        return EXIT_INPUT
    data = load_ledger(order_dir, budget)
    hit = None
    for item in data["rounds"]:
        if int(item.get("round") or 0) == round_no:
            hit = item
            break
    if hit is None:
        print("找不到第 %d 轮" % round_no)
        return EXIT_INPUT
    hit["satisfaction"] = satisfaction
    data["satisfaction"] = satisfaction
    save_ledger(order_dir, data)
    echo({"round": round_no, "satisfaction": satisfaction})
    return EXIT_OK


def main():
    ap = argparse.ArgumentParser(description="头像订单轮次与预算台账")
    ap.add_argument("--order", required=True, help="订单目录")
    ap.add_argument("--check", action="store_true", help="查看预算余额")
    ap.add_argument("--preview", action="store_true",
                    help="解析改进方向并输出 prompt 措辞")
    ap.add_argument("--record", action="store_true", help="记录一轮生成")
    ap.add_argument("--set-satisfaction", action="store_true",
                    help="回填某轮满意度")
    ap.add_argument("--round", type=int, help="轮次编号（从 1 开始）")
    ap.add_argument("--count", type=int, help="本轮生成张数")
    ap.add_argument("--directions", default="",
                    help="改进方向 id，逗号分隔")
    ap.add_argument("--satisfaction", choices=SATISFACTIONS,
                    help="satisfied / unsatisfied")
    ap.add_argument("--budget", type=int, help="覆盖预算上限（默认读 brief/台账）")
    ap.add_argument("--allow-over-budget", action="store_true",
                    help="用户已确认追加预算时放行")
    ap.add_argument("--feedback", default=FEEDBACK_DEFAULT,
                    help="改进方向目录 JSON")
    args = ap.parse_args()

    if not (args.check or args.preview or args.record
            or args.set_satisfaction):
        print("必须指定 --check / --preview / --record / --set-satisfaction")
        return EXIT_INPUT

    try:
        catalog, available = load_feedback(args.feedback)
    except (OSError, ValueError) as exc:
        print("改进方向目录读取失败: %s" % exc)
        return EXIT_INPUT

    try:
        ids = parse_ids(args.directions)
        if args.check:
            return do_check(args.order, args.budget)
        if args.preview:
            return do_preview(args.order, args.budget, ids, catalog, available)
        if args.record:
            if args.round is None:
                print("--record 需要 --round")
                return EXIT_INPUT
            count = args.count if args.count is not None else 2
            return do_record(args.order, args.budget, args.round, count, ids,
                             catalog, available, args.satisfaction,
                             args.allow_over_budget)
        if args.round is None:
            print("--set-satisfaction 需要 --round")
            return EXIT_INPUT
        return do_set_satisfaction(args.order, args.budget, args.round,
                                   args.satisfaction)
    except ValueError as exc:
        print(str(exc))
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
