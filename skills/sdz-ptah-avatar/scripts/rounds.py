#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轮次与预算台账：交付后满意度回访、改进方向解析、按轮元数据记录。

改进方向目录来自 ../references/feedback.json，脚本内不硬编码方向。
轮次只记元数据，不创建子目录；图片在订单根平铺。
一个角色一个目录：订单目录固定为 <root>/<角色 slug>，不再加 -01/-02 序号；
每次下单只换 order_id（YYYYMMDD-HHMMSS），旧 brief 归档、旧轮次保留。

用法：
  # 新建/切换订单（目录固定为 <root>/<角色 slug>）
  py -3 rounds.py --new-order --root <订单根> --character satoshi

  # 查看当前订单预算余额（余额 <=0 退出码 5）
  py -3 rounds.py --order <订单目录> --check

  # 解析改进方向，输出落到 prompt 的措辞（不生成、不记账）
  py -3 rounds.py --order <订单目录> --preview --directions composition,detail

  # 记录一轮（只写 rounds.json，不建目录）
  py -3 rounds.py --order <订单目录> --record --round 2 --count 2 \
        --directions composition,detail --satisfaction unsatisfied \
        --files satoshi-avatar-3.png,satoshi-avatar-4.png

  # 回填满意度
  py -3 rounds.py --order <订单目录> --set-satisfaction --round 1 \
        --satisfaction satisfied

  # 对账：把盘上已有但没记账的图补进预算台账（防止实际 8 张、台账 2 张）
  py -3 rounds.py --order <订单目录> --reconcile

预算按轮累计：累计超过 budget_limit 且未加 --allow-over-budget 时直接拒绝（退出码 5）。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

SCHEMA = "avatar-rounds/1.0"
DEFAULT_BUDGET = 10
SATISFACTIONS = ("satisfied", "unsatisfied")

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_BUDGET = 5
EXIT_DUPLICATE = 6
EXIT_PENDING = 7          # 有轮次满意度未回填：下次交互必须提醒

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


def order_dir_for(root, character):
    """一个角色一个目录：只用角色 slug，不加序号、不加时间戳。"""
    os.makedirs(root, exist_ok=True)
    slug = re.sub(r"[^0-9A-Za-z_-]+", "-", character or "order").strip("-").lower()
    return os.path.join(root, slug or "order")


def default_order_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def current_order_id(data):
    """当前订单：顶层 order_id > orders 最后一项 > None（旧台账）。"""
    if data.get("order_id"):
        return data["order_id"]
    orders = data.get("orders") or []
    if orders:
        return orders[-1].get("order_id")
    return None


def order_of_round(entry, data):
    """轮次归属订单；旧轮次没有 order_id 时归给首个/唯一订单。"""
    oid = entry.get("order_id")
    if oid:
        return oid
    orders = data.get("orders") or []
    if len(orders) <= 1:
        return current_order_id(data)
    return orders[0].get("order_id")


def scoped_rounds(data):
    """只取当前订单的轮次：历史订单的轮次不参与预算与重复判定。"""
    cur = current_order_id(data)
    rounds = data.get("rounds") or []
    if cur is None:
        return list(rounds)
    return [r for r in rounds if order_of_round(r, data) == cur]


def scoped_unaccounted(data):
    """当前订单里「盘上有图但没记进轮次」的补账记录。"""
    cur = current_order_id(data)
    items = data.get("unaccounted") or []
    if cur is None:
        return list(items)
    return [u for u in items if order_of_round(u, data) == cur]


def counted_total(data):
    return (sum(int(r.get("count") or 0) for r in scoped_rounds(data))
            + sum(int(u.get("count") or 0) for u in scoped_unaccounted(data)))


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
    if not data.get("order_id"):
        orders = data.get("orders") or []
        if orders:
            data["order_id"] = orders[-1].get("order_id")
            data.setdefault("ordered_at", orders[-1].get("ordered_at"))
        else:
            # 旧台账：目录名就是订单标识，轮次全部归当前订单
            data["order_id"] = os.path.basename(os.path.normpath(order_dir))
    data.setdefault("ordered_at", now_text())
    data.setdefault("budget_used", 0)
    rounds = data.setdefault("rounds", [])
    data["budget_used"] = counted_total(data)
    limit = bud if bud is not None else data.get("budget_limit")
    data["budget_limit"] = max(1, int(limit if limit is not None
                                      else DEFAULT_BUDGET))
    data["remaining"] = data["budget_limit"] - data["budget_used"]
    # 满意度未回填的轮次标 pending：三轮全 null 不能算完，
    # 下次 --check / --record 都要再次提醒。只看当前订单。
    scoped = scoped_rounds(data)
    for item in rounds:
        if item.get("satisfaction") not in SATISFACTIONS:
            item["satisfaction"] = "pending"
    data["pending_rounds"] = [int(r.get("round") or 0) for r in scoped
                              if r.get("satisfaction") == "pending"]
    if not scoped:
        data["satisfaction"] = None
    elif data["pending_rounds"]:
        data["satisfaction"] = "pending"
    else:
        data["satisfaction"] = scoped[-1].get("satisfaction")
    data.setdefault("updated_at", now_text())
    return data


def save_ledger(order_dir, data):
    data["budget_used"] = counted_total(data)
    data["remaining"] = data["budget_limit"] - data["budget_used"]
    rounds = data.get("rounds", [])
    scoped = scoped_rounds(data)
    for item in rounds:
        if item.get("satisfaction") not in SATISFACTIONS:
            item["satisfaction"] = "pending"
    data["pending_rounds"] = [int(r.get("round") or 0) for r in scoped
                              if r.get("satisfaction") == "pending"]
    if not scoped:
        data["satisfaction"] = None
    elif data["pending_rounds"]:
        data["satisfaction"] = "pending"
    else:
        data["satisfaction"] = scoped[-1].get("satisfaction")
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
    pending = data.get("pending_rounds") or []
    payload = {"order_id": data["order_id"],
               "ordered_at": data.get("ordered_at"),
               "order_dir": os.path.basename(os.path.normpath(order_dir)),
               "orders": len(data.get("orders") or []),
               "budget_limit": data["budget_limit"],
               "budget_used": data["budget_used"],
               "remaining": max(0, data["remaining"]),
               "exhausted": exhausted,
               "rounds": len(scoped_rounds(data)),
               "unaccounted": len(scoped_unaccounted(data)),
               "satisfaction": data.get("satisfaction"),
               "pending_rounds": pending}
    if pending:
        payload["reminder"] = ("第 %s 轮满意度未回填（pending）；"
                               "交付后必须问一次「这版满意吗」并回填。"
                               % "、".join(str(n) for n in pending))
    echo(payload)
    if exhausted:
        print("预算用尽：已用 %d / 上限 %d。追加预算需用户再次确认。"
              % (data["budget_used"], data["budget_limit"]))
        return EXIT_BUDGET
    if pending:
        print(payload["reminder"])
        return EXIT_PENDING
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
        payload["order_id"] = data["order_id"]
        payload["ordered_at"] = data.get("ordered_at")
        payload["budget"] = {"limit": data["budget_limit"],
                             "used": data["budget_used"],
                             "remaining": max(0, data["remaining"])}
    echo(payload)
    return EXIT_OK


def do_record(order_dir, budget, round_no, count, ids, catalog, available,
              satisfaction, allow_over, files=None):
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

    file_list = [os.path.basename(f) for f in (files or [])]
    if file_list:
        missing = [f for f in file_list
                   if not os.path.isfile(os.path.join(order_dir, f))]
        if missing:
            print("--files 里有文件不在订单目录：%s" % ", ".join(missing))
            return EXIT_INPUT
        if len(file_list) > count:
            print("--count=%d 小于 --files 张数 %d：本轮会漏记预算。"
                  "请把 --count 改成实际生成张数。" % (count, len(file_list)))
            return EXIT_INPUT

    data = load_ledger(order_dir, budget)
    used = data["budget_used"]
    current = scoped_rounds(data)
    if round_no in [int(r.get("round") or 0) for r in current]:
        print("第 %d 轮已存在（当前订单 %s），历史轮次不覆盖。"
              % (round_no, data["order_id"]))
        return EXIT_DUPLICATE
    if used + count > data["budget_limit"] and not allow_over:
        print("预算用尽：当前订单已用 %d / 上限 %d，本轮再要 %d 张会超额。"
              % (used, data["budget_limit"], count))
        print("必须先询问用户是否追加预算；用户确认后再加 "
              "--allow-over-budget（并可用 --budget 调整上限）。")
        return EXIT_BUDGET

    entry = {
        "order_id": data["order_id"],
        "ordered_at": data.get("ordered_at"),
        "round": round_no,
        "count": count,
        "directions": directions,
        "budget_used": used + count,
        "generated_at": now_text(),
        "satisfaction": satisfaction or "pending",
        "files": files or [],
    }
    data["rounds"].append(entry)
    prior_pending = [int(r.get("round") or 0) for r in data["rounds"]
                     if order_of_round(r, data) == data["order_id"]
                     and int(r.get("round") or 0) != round_no
                     and r.get("satisfaction") == "pending"]
    save_ledger(order_dir, data)
    payload = {"recorded": entry,
               "budget": {"limit": data["budget_limit"],
                          "used": data["budget_used"],
                          "remaining": max(0, data["remaining"])},
               "satisfaction": data.get("satisfaction"),
               "pending_rounds": data.get("pending_rounds")}
    if prior_pending:
        payload["reminder"] = ("第 %s 轮满意度仍未回填（pending），"
                               "下次交互需继续提醒。"
                               % "、".join(str(n) for n in prior_pending))
    echo(payload)
    return EXIT_OK


def do_set_satisfaction(order_dir, budget, round_no, satisfaction):
    if satisfaction not in SATISFACTIONS:
        print("--satisfaction 只能是 %s" % " / ".join(SATISFACTIONS))
        return EXIT_INPUT
    data = load_ledger(order_dir, budget)
    hit = None
    for item in scoped_rounds(data):
        if int(item.get("round") or 0) == round_no:
            hit = item
            break
    if hit is None:
        print("当前订单 %s 找不到第 %d 轮" % (data["order_id"], round_no))
        return EXIT_INPUT
    hit["satisfaction"] = satisfaction
    save_ledger(order_dir, data)
    echo({"order_id": data["order_id"], "round": round_no,
          "satisfaction": satisfaction,
          "order_satisfaction": data.get("satisfaction"),
          "pending_rounds": data.get("pending_rounds")})
    return EXIT_OK


def disk_images(order_dir):
    """订单根平铺的生成图：按 `<prefix>-avatar-N[-src]` 聚合编号。"""
    nums = {}
    for name in os.listdir(order_dir):
        m = re.match(r".+-avatar-(\d+)(?:-src)?\.(?:png|jpe?g|webp)$", name, re.I)
        if m:
            nums.setdefault(int(m.group(1)), []).append(name)
    return nums


def account_numbers(entries):
    out = set()
    for item in entries:
        for name in item.get("files") or []:
            m = re.match(r".+-avatar-(\d+)(?:-src)?\.", os.path.basename(name), re.I)
            if m:
                out.add(int(m.group(1)))
    return out


def do_reconcile(order_dir, budget, allow_over=False):
    """把盘上已有、但没记进轮次的图补进台账，避免「实际 8 张、台账 2 张」。

    以盘上张数与台账累计的差值为准（改名/换前缀不会误判）：
    只要 `盘上张数 > 台账累计`，差额就补进 `unaccounted`；不减少历史累计。
    """
    if not os.path.isdir(order_dir):
        print("订单目录不存在: %s" % order_dir)
        return EXIT_INPUT
    data = load_ledger(order_dir, budget)
    nums = disk_images(order_dir)
    accounted = (account_numbers(scoped_rounds(data))
                 | account_numbers(scoped_unaccounted(data)))
    missing = sorted(nn for nn in nums if nn not in accounted)
    extra = max(0, len(nums) - counted_total(data))
    if extra:
        files = [f for nn in missing for f in sorted(nums[nn]) if "-src" in f.lower()]
        if not files:
            files = [f for nn in missing for f in sorted(nums[nn])]
        data.setdefault("unaccounted", []).append({
            "order_id": data["order_id"],
            "count": extra,
            "nns": missing,
            "files": files,
            "reason": "disk_scan_not_recorded",
            "at": now_text(),
        })
        save_ledger(order_dir, data)
    echo({"order_id": data["order_id"],
          "disk_images": len(nums),
          "accounted_nns": sorted(accounted),
          "unrecorded": missing if extra else [],
          "budget_limit": data["budget_limit"],
          "budget_used": data["budget_used"],
          "remaining": max(0, data["remaining"]),
          "exhausted": data["remaining"] <= 0})
    if data["remaining"] <= 0 and not allow_over:
        print("预算已用尽：盘上 %d 张 / 上限 %d。追加预算需用户再次确认。"
              % (data["budget_used"], data["budget_limit"]))
        return EXIT_BUDGET
    return EXIT_OK


def do_new_order(root, character, order_id=None):
    """一角色一目录：复用 <root>/<角色 slug>，只切换 order_id。

    旧 brief 归档为 brief-<旧 order_id>.json，旧轮次保留在 rounds.json；
    新订单从 order_id / ordered_at 开始，预算按当前订单重新计算。
    """
    path = order_dir_for(root, character)
    os.makedirs(path, exist_ok=True)
    data = load_ledger(path, None)
    old_oid = current_order_id(data)
    if not data.get("orders") and data.get("rounds"):
        # 旧台账：没有 order_id 的轮次固定归旧标识，之后不再算进新单
        old_oid = old_oid or os.path.basename(os.path.normpath(path))
        for item in data["rounds"]:
            item.setdefault("order_id", old_oid)
    ordered_at = now_text()
    oid = order_id or default_order_id()
    used = {o.get("order_id") for o in data.get("orders") or []}
    if oid in used:
        k = 2
        while "%s-%d" % (oid, k) in used:
            k += 1
        oid = "%s-%d" % (oid, k)

    archived = None
    brief_path = os.path.join(path, "brief.json")
    if old_oid and os.path.isfile(brief_path):
        archived = "brief-%s.json" % old_oid
        target = os.path.join(path, archived)
        k = 2
        while os.path.exists(target):
            archived = "brief-%s-%d.json" % (old_oid, k)
            target = os.path.join(path, archived)
            k += 1
        os.replace(brief_path, target)
        for entry in data.get("orders") or []:
            if entry.get("order_id") == old_oid:
                entry["brief"] = archived

    limit = default_budget(path)
    order_entry = {"order_id": oid, "ordered_at": ordered_at,
                   "brief": "brief.json", "budget_limit": limit}
    data.setdefault("orders", []).append(order_entry)
    data["order_id"] = oid
    data["ordered_at"] = ordered_at
    data["character"] = os.path.basename(os.path.normpath(path))
    data["budget_limit"] = limit
    save_ledger(path, data)
    echo({"order_dir": path,
          "name": os.path.basename(path),
          "order_id": oid,
          "ordered_at": ordered_at,
          "brief": "brief.json",
          "archived_brief": archived,
          "budget_limit": limit,
          "orders": len(data["orders"])})
    return EXIT_OK


def main():
    ap = argparse.ArgumentParser(description="头像订单轮次与预算台账")
    ap.add_argument("--order", default=None, help="订单目录")
    ap.add_argument("--new-order", action="store_true",
                    help="切换订单：目录固定 <root>/<角色 slug>，只换 order_id")
    ap.add_argument("--root", default=None, help="--new-order 的订单根目录")
    ap.add_argument("--character", default=None,
                    help="--new-order 的角色 id（目录名取该 slug）")
    ap.add_argument("--order-id", default=None,
                    help="--new-order 显式指定 order_id（默认 YYYYMMDD-HHMMSS）")
    ap.add_argument("--check", action="store_true", help="查看预算余额")
    ap.add_argument("--preview", action="store_true",
                    help="解析改进方向并输出 prompt 措辞")
    ap.add_argument("--record", action="store_true", help="记录一轮生成")
    ap.add_argument("--set-satisfaction", action="store_true",
                    help="回填某轮满意度")
    ap.add_argument("--reconcile", action="store_true",
                    help="扫描订单目录，把已生成但没记账的图补进预算台账")
    ap.add_argument("--round", type=int, help="轮次编号（从 1 开始）")
    ap.add_argument("--count", type=int, help="本轮生成张数")
    ap.add_argument("--directions", default="",
                    help="改进方向 id，逗号分隔")
    ap.add_argument("--files", default="",
                    help="本轮文件名，逗号分隔（可选，便于回查）")
    ap.add_argument("--satisfaction", choices=SATISFACTIONS,
                    help="satisfied / unsatisfied")
    ap.add_argument("--budget", type=int, help="覆盖预算上限（默认读 brief/台账）")
    ap.add_argument("--allow-over-budget", action="store_true",
                    help="用户已确认追加预算时放行")
    ap.add_argument("--feedback", default=FEEDBACK_DEFAULT,
                    help="改进方向目录 JSON")
    args = ap.parse_args()

    if args.new_order:
        if not args.root or not args.character:
            print("--new-order 需要 --root 与 --character")
            return EXIT_INPUT
        return do_new_order(args.root, args.character, args.order_id)

    if not args.order:
        print("必须指定 --order（或 --new-order）")
        return EXIT_INPUT
    if not (args.check or args.preview or args.record
            or args.set_satisfaction or args.reconcile):
        print("必须指定 --check / --preview / --record / --reconcile / --set-satisfaction")
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
        if args.reconcile:
            return do_reconcile(args.order, args.budget, args.allow_over_budget)
        if args.record:
            if args.round is None:
                print("--record 需要 --round")
                return EXIT_INPUT
            count = args.count if args.count is not None else 2
            file_list = [f.strip() for f in args.files.split(",") if f.strip()]
            return do_record(args.order, args.budget, args.round, count, ids,
                             catalog, available, args.satisfaction,
                             args.allow_over_budget, file_list)
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
