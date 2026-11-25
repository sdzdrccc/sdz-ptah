#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成入口：读 brief → 调后端适配器 → 原图落订单目录 → 记预算台账 → 写生成日志。

在确认门（confirm_order.py）之后、评分流水线（avatar_pipeline.py）之前运行；
不改评分规则，只负责「把原图按 <prefix>-avatar-<N>-src.<ext> 放进订单目录并如实记账」。

用法：
  py -3 generate.py --order <订单目录> --prompt-zh "<中文段>" --prompt-en "<英文段/tag>" \
        [--negative "<负向词>"] [--round 2] [--count 2]
  py -3 generate.py --order <订单目录> --prompt-file <prompts.next.json>
  py -3 generate.py --order <订单目录> --prompt-file <...> --directions composition,detail  # 交付后重出
  py -3 generate.py --order <订单目录> --dry-run        # 只校验并打印计划，不调用、不记账
  py -3 generate.py --order <订单目录> --resume         # 宿主执行完调用单 / 中断后继续
  py -3 generate.py --order <订单目录> --discard-pending  # 放弃未完成的生成

--prompt-file 的 JSON：
  {"prompt_zh": "...", "prompt_en": "...", "negative": "...",
   "stages": {"style": {"prompt_en": "...", "negative": "..."}}}   # stages 可选，按阶段覆盖

规则（与 SKILL.md 硬约束一致）：
- 没有 confirm.confirmed=true 不生成（退出码 4）。
- 后端能力不满足、未同意外发、阶段组合非法：退出码 4，不换后端、不退回文生图。
- 预算按「调用次数」预检：张数 × 段数 > 当前订单余额时退出码 5，先问用户；
  用户同意追加后加 --allow-over-budget（或先用 rounds.py 调大预算）。
- 宿主侧后端（Agnes / TRAE）：写出 <prefix>-call-<N>.json 后退出码 9；
  宿主按调用单生成并把图放回订单目录，再运行 --resume。
- 后端失败退出码 8；已花掉的调用数记在 generation-pending.json，
  --resume 续跑时不重复计数，--discard-pending 时补进 rounds.json 的 unaccounted。

写出：
- <prefix>-avatar-<N>-src.<ext>（两段式另有 <prefix>-avatar-<N>-s1.<ext> 身份步证据图）
- generation.json（每次生成一条 run：分段、seed、调用数、各后端结果与许可快照）
- rounds.json 新增一轮（count = 调用次数，另记 images / calls / stages）
- prompts.json 追加本轮提示词；status.json 更新 phase / budget / raw_count
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
for _p in (SKILL_ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rounds as rl  # noqa: E402
from path_names import safe_slug  # noqa: E402
from backends import registry, runner  # noqa: E402
from backends.base import (EXIT_BACKEND, EXIT_BUDGET, EXIT_CAPABILITY,  # noqa: E402
                           EXIT_NEEDS_AGENT, AdapterError, CapabilityError,
                           GenerationRequest, NeedsAgent, ReferenceImage)

EXIT_OK = 0
EXIT_INPUT = 2
PENDING = "generation-pending.json"
LOG = "generation.json"
LOG_SCHEMA = "sdz-generation-log/1.0"
_ANY_NUMBER = re.compile(r"^.+?-avatar-(\d+)(?:-[a-z0-9]+)?\.[A-Za-z0-9]+$", re.I)


# ---------------------------------------------------------------- 小工具
def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
        fh.write("\n")
    os.replace(tmp, path)


def echo(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def detect_prefix(order_dir, brief):
    """与 confirm_order.py 相同：character_id > 目录里已有前缀 > avatar。"""
    prefix = brief.get("character_id")
    if prefix:
        return safe_slug(prefix, fallback="avatar")
    for name in sorted(os.listdir(order_dir)):
        m = re.match(r"(.+)-avatar-\d+(?:-src)?\.(?:png|jpe?g|webp)$", name, re.I)
        if m:
            return m.group(1)
    return "avatar"


def next_index(order_dir):
    """订单目录内已用的最大编号 + 1（跨订单连续，与 avatar_pipeline.flat_max_number 一致）。"""
    mx = 0
    for name in os.listdir(order_dir):
        low = name.lower()
        if low == "contact_sheet.jpg" or low.endswith("-sheet.jpg"):
            continue
        m = _ANY_NUMBER.match(name)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


def active_references(gen):
    """只取 active=true 的参考图；没有显式 active 标记时全部参与（与流水线一致）。"""
    entries = [e for e in (gen.get("reference_images") or []) if isinstance(e, dict)]
    active = [e for e in entries if e.get("active") is True]
    return active if active else entries


def default_stages(gen):
    return [{"stage": "single", "backend": gen.get("backend") or "agnes-aigc-img2img"}]


def merge_stage_defaults(stages, doc, base_negative):
    """把注册表 defaults（steps / guidance / negative）补进各段；用户写了的不覆盖。"""
    specs = {b.get("id"): b for b in doc.get("backends") or []}
    out = []
    for s in stages:
        d = dict((specs.get(s.get("backend")) or {}).get("defaults") or {})
        merged = {k: d[k] for k in ("steps", "guidance") if k in d}
        if base_negative is None and d.get("negative") and "negative" not in s:
            merged["negative"] = d["negative"]
        merged.update(s)
        out.append(merged)
    return out


def resolve_dirs(raw):
    """改进方向：与 rounds.py --record 相同的解析与记录格式；未知 id 直接报错。"""
    ids = rl.parse_ids(raw)
    if not ids:
        return []
    catalog, available = rl.load_feedback(rl.FEEDBACK_DEFAULT)
    return rl.resolve_directions(ids, catalog, available)


def load_prompts(args):
    data = {}
    if args.prompt_file:
        data = read_json(args.prompt_file, {}) or {}
    for key, val in (("prompt_zh", args.prompt_zh), ("prompt_en", args.prompt_en),
                     ("negative", args.negative)):
        if val is not None:
            data[key] = val
    return data


# ---------------------------------------------------------------- 计划
# protocol 2.6.1：这些词只能靠联想，不得作为肤色主描述
BANNED_VAGUE = ("小麦色", "暖调小麦色", "暖调皮肤", "日晒感", "日晒黑", "健康肤色",
                "自然肤色", "古铜色", "蜜色", "肤色偏暖", "偏黄的皮肤")


def audit_prompts(prompts):
    """protocol 2.9：提示词必须完整、不含省略号截断、不含模糊色词。"""
    issues = []
    for key in ("prompt_zh", "prompt_en"):
        text = prompts.get(key) or ""
        if "…" in text or "..." in text:
            issues.append("%s 含省略号，视为被截断；prompts.json 必须存完整全文" % key)
        hits = [w for w in BANNED_VAGUE if w in text]
        if hits:
            issues.append("%s 含模糊色词 %s；改用 skin_spec 的 hex + 色相/饱和度/明度锚点"
                          % (key, "、".join(hits)))
    return issues


def prompt_digest(prompt_zh, prompt_en):
    return hashlib.sha256(("%s\n---\n%s" % (prompt_zh, prompt_en)).encode("utf-8")).hexdigest()


def parse_render_size(value):
    """brief.render_size（'1024x1536'）→ (宽, 高)；缺省 1024x1024。

    每边 512–2048 且为 8 的倍数，否则 ValueError（退出码 2）。
    1K/2K/4K 仍是交付规格，由 avatar_pipeline.py 裁切 / 放大导出，与这里无关。
    """
    if value in (None, ""):
        return 1024, 1024
    if isinstance(value, (list, tuple)) and len(value) == 2:
        raw = value
    else:
        m = re.match(r"^\s*(\d{3,4})\s*[xX×*]\s*(\d{3,4})\s*$", str(value))
        if not m:
            raise ValueError("render_size 格式应为 宽x高，例如 1024x1536，收到 %r" % (value,))
        raw = m.groups()
    w, h = int(raw[0]), int(raw[1])
    for v in (w, h):
        if v < 512 or v > 2048 or v % 8:
            raise ValueError("render_size 每边需在 512–2048 之间且为 8 的倍数，收到 %dx%d" % (w, h))
    return w, h


def build_plan(order_dir, brief, args, doc):
    gen = brief.get("generation") or {}
    mode = gen.get("mode") or "reference"
    stages = [dict(s) for s in (gen.get("stages") or default_stages(gen))]
    prompts = load_prompts(args)
    if not (prompts.get("prompt_zh") or prompts.get("prompt_en")):
        raise ValueError("缺少提示词：用 --prompt-zh / --prompt-en 或 --prompt-file 传入"
                         "（先用 scripts/compile_prompt.py 编译）")
    issues = audit_prompts(prompts)
    if issues:
        raise ValueError("提示词审计失败：\n- " + "\n- ".join(issues))
    for name, override in (prompts.get("stages") or {}).items():
        for s in stages:
            if s.get("stage") == name:
                s.update({k: v for k, v in override.items()
                          if k in ("prompt_zh", "prompt_en", "negative")})
    stages = merge_stage_defaults(stages, doc, prompts.get("negative"))

    refs = []
    if mode == "reference":
        for e in active_references(gen):
            refs.append({"path": os.path.join(order_dir, e["path"]),
                         "role": e.get("role") or "identity", "sha256": e.get("sha256")})
    elif gen.get("text2img_consent") is not True:
        raise CapabilityError("mode=text2img 需要用户显式同意（generation.text2img_consent=true）")

    # 默认 1024 方图；brief.render_size 可指定其他尺寸。1K/2K/4K 是交付规格，由 avatar_pipeline.py 裁切 / 放大导出
    width, height = parse_render_size(brief.get("render_size"))
    size = width
    count = int(args.count or brief.get("count") or 2)
    start_index = next_index(order_dir)
    if gen.get("seed") is not None:
        # 第 N 张用 brief.seed + (N-1)：跨轮次不重复，同一订单同一编号可复现
        seed = (int(gen["seed"]) + start_index - 1) % (2 ** 32)
    else:
        seed = random.randint(0, 2 ** 31 - 1)

    ledger = rl.load_ledger(order_dir, None)
    scoped = rl.scoped_rounds(ledger)
    round_no = args.round or (max([int(r.get("round") or 0) for r in scoped] + [0]) + 1)
    return {
        "schema": "sdz-generation-pending/1.0",
        "order_id": brief.get("order_id") or ledger.get("order_id"),
        "round": round_no,
        "prefix": detect_prefix(order_dir, brief),
        "start_index": start_index,
        "count": count,
        "size": size,
        "width": width,
        "height": height,
        "seed": seed,
        "seed_source": "brief" if gen.get("seed") is not None else "random",
        "mode": mode,
        "stages": stages,
        "prompt_zh": prompts.get("prompt_zh") or "",
        "prompt_en": prompts.get("prompt_en") or "",
        "negative": prompts.get("negative"),
        "prompt_version": prompts.get("prompt_version"),
        "prompt_template": prompts.get("prompt_template"),
        "prompt_sha256": prompts.get("prompt_sha256") or prompt_digest(
            prompts.get("prompt_zh") or "", prompts.get("prompt_en") or ""),
        "style_id": prompts.get("style_id"),
        "style_version": prompts.get("style_version"),
        "character_version": prompts.get("character_version"),
        "skin_anchor": prompts.get("skin_anchor"),
        "negative_mode": prompts.get("negative_mode") or (
            "separate" if prompts.get("negative") else "none"),
        "references": refs,
        "egress_consent": gen.get("egress_consent") is True,
        "calls_spent": 0,
        "directions": resolve_dirs(args.directions),
        "created_at": now_iso(),
        "run_id": "%s-r%s-%s" % (brief.get("order_id") or "order", round_no,
                                 datetime.now().strftime("%H%M%S")),
    }


def to_request(plan):
    return GenerationRequest(
        order_id=plan["order_id"], prefix=plan["prefix"], start_index=plan["start_index"],
        prompt_zh=plan["prompt_zh"], prompt_en=plan["prompt_en"], negative=plan["negative"],
        references=[ReferenceImage(r["path"], r["role"], r.get("sha256"))
                    for r in plan["references"]],
        seed=plan["seed"], width=plan.get("width", plan["size"]), height=plan.get("height", plan["size"]),
        count=plan["count"],
        egress_consent=plan["egress_consent"])


def dry_run(plan, doc):
    """逐段跑能力校验（不调用），返回错误列表。"""
    errors, warnings = [], []
    base = to_request(plan)
    for k, s in enumerate(plan["stages"]):
        try:
            adapter = registry.get(doc, s["backend"])
        except CapabilityError as e:
            errors.append(str(e))
            continue
        r = runner._stage_req(base, s, s.get("stage", "single"))
        if len(plan["stages"]) == 2 and k == 1:
            r.references, r.init_image = [], "<身份步输出>"
        e, w = adapter.validate(r)
        if r.init_image == "<身份步输出>":
            e = [x for x in e if "参考图不存在" not in x]
        errors += ["[%s:%s] %s" % (s.get("stage"), s["backend"], x) for x in e]
        warnings += ["[%s:%s] %s" % (s.get("stage"), s["backend"], x) for x in w]
    return errors, warnings


# ---------------------------------------------------------------- 落账
def update_status(order_dir, **fields):
    path = os.path.join(order_dir, "status.json")
    data = read_json(path, {}) or {}
    data.update(fields)
    data["updated_at"] = now_iso()
    write_json(path, data)


def append_log(order_dir, plan, results, calls, files):
    path = os.path.join(order_dir, LOG)
    doc = read_json(path, {"schema": LOG_SCHEMA, "runs": []}) or {"schema": LOG_SCHEMA, "runs": []}
    doc.setdefault("runs", []).append({
        "run_id": plan["run_id"], "order_id": plan["order_id"], "round": plan["round"],
        "at": now_iso(), "stages": plan["stages"], "seed": plan["seed"],
        "seed_source": plan["seed_source"], "count": plan["count"], "calls": calls,
        "files": files, "prompt_zh": plan["prompt_zh"], "prompt_en": plan["prompt_en"],
        "negative": plan["negative"],
        "prompt_version": plan.get("prompt_version"),
        "prompt_sha256": plan.get("prompt_sha256"),
        "style_version": plan.get("style_version"),
        "results": [r.to_manifest() for r in results],
    })
    write_json(path, doc)


def append_prompts(order_dir, plan, files):
    path = os.path.join(order_dir, "prompts.json")
    doc = read_json(path, None)
    if not isinstance(doc, dict):
        doc = {"order_id": plan["order_id"], "prompts": []}
    doc.setdefault("prompts", [])
    for k, name in enumerate(files):
        doc["prompts"].append({
            "id": os.path.splitext(name)[0].replace("-src", ""),
            "round": plan["round"], "run_id": plan["run_id"],
            "stages": ["%s:%s" % (s.get("stage"), s.get("backend")) for s in plan["stages"]],
            "seed": (plan["seed"] + k) % (2 ** 32),
            "prompt": plan["prompt_zh"] or plan["prompt_en"],
            "prompt_zh": plan["prompt_zh"],
            "prompt_en": plan["prompt_en"], "negative": plan["negative"],
            "negative_mode": plan.get("negative_mode"),
            "prompt_version": plan.get("prompt_version"),
            "prompt_template": plan.get("prompt_template"),
            "prompt_sha256": plan.get("prompt_sha256"),
            "style_id": plan.get("style_id"),
            "style_version": plan.get("style_version"),
            "character_version": plan.get("character_version"),
            "skin_anchor": plan.get("skin_anchor"),
            "kept": None, "reason": None,
        })
    write_json(path, doc)


def record_round(order_dir, plan, calls, files, allow_over):
    data = rl.load_ledger(order_dir, None)
    if plan["round"] in [int(r.get("round") or 0) for r in rl.scoped_rounds(data)]:
        raise ValueError("第 %d 轮已存在（当前订单 %s），历史轮次不覆盖；用 --round 指定新轮次"
                         % (plan["round"], data["order_id"]))
    used = data["budget_used"]
    over = used + calls > data["budget_limit"]
    entry = {
        "order_id": data["order_id"], "ordered_at": data.get("ordered_at"),
        "round": plan["round"], "count": calls, "directions": plan.get("directions") or [],
        "budget_used": used + calls, "generated_at": rl.now_text(),
        "satisfaction": "pending", "files": files,
        "images": len(files), "calls": calls,
        "stages": ["%s:%s" % (s.get("stage"), s.get("backend")) for s in plan["stages"]],
        "generator": "scripts/generate.py", "run_id": plan["run_id"],
    }
    if over:
        entry["over_budget"] = True
        entry["over_budget_approved"] = bool(allow_over)
    data["rounds"].append(entry)
    rl.save_ledger(order_dir, data)
    return rl.load_ledger(order_dir, None), entry


def record_unaccounted(order_dir, plan, reason):
    data = rl.load_ledger(order_dir, None)
    data.setdefault("unaccounted", []).append({
        "order_id": data["order_id"], "count": int(plan.get("calls_spent") or 0),
        "nns": [], "files": [], "reason": reason, "run_id": plan.get("run_id"),
        "at": rl.now_text(),
    })
    rl.save_ledger(order_dir, data)


def expected_files(order_dir, plan):
    files = []
    for k in range(plan["count"]):
        p = runner.find_output(order_dir, plan["prefix"], plan["start_index"] + k, "src")
        if p:
            files.append(os.path.basename(p))
    return files


# ---------------------------------------------------------------- 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="call a generation backend for an avatar order")
    ap.add_argument("--order", required=True, help="订单目录（含 brief.json）")
    ap.add_argument("--prompt-zh", default=None, help="中文段（original_prompt）")
    ap.add_argument("--prompt-en", default=None, help="英文段 / tag 串（enhanced_prompt）")
    ap.add_argument("--negative", default=None, help="负向词（后端不支持时只警告）")
    ap.add_argument("--prompt-file", default=None, help="提示词 JSON（见文件头说明）")
    ap.add_argument("--round", type=int, default=None, help="轮次（默认当前订单最大轮次 + 1）")
    ap.add_argument("--count", type=int, default=None, help="张数（默认 brief.count）")
    ap.add_argument("--directions", default="",
                    help="本轮改进方向 id，逗号分隔（来自 references/feedback.json，交付后重出时用）")
    ap.add_argument("--backends", default=None, help="后端注册表（默认 backends.v2.json）")
    ap.add_argument("--allow-over-budget", action="store_true",
                    help="用户已确认追加预算时才加")
    ap.add_argument("--dry-run", action="store_true", help="只校验并打印计划")
    ap.add_argument("--resume", action="store_true", help="按 generation-pending.json 续跑")
    ap.add_argument("--discard-pending", action="store_true",
                    help="放弃未完成的生成；已花调用数补进 rounds.json unaccounted")
    args = ap.parse_args(argv)

    order_dir = os.path.abspath(args.order)
    brief_path = os.path.join(order_dir, "brief.json")
    pending_path = os.path.join(order_dir, PENDING)
    if not os.path.isfile(brief_path):
        print("brief not found: %s" % brief_path)
        return EXIT_INPUT
    brief = read_json(brief_path, {}) or {}
    if (brief.get("confirm") or {}).get("confirmed") is not True:
        print("未确认：brief.json 缺少 confirm.confirmed=true，先运行 confirm_order.py")
        return EXIT_CAPABILITY
    doc = registry.load(args.backends)
    pending = read_json(pending_path, None)

    if args.discard_pending:
        if not pending:
            print("没有未完成的生成")
            return EXIT_OK
        if int(pending.get("calls_spent") or 0) > 0:
            record_unaccounted(order_dir, pending, "generate_aborted")
        os.remove(pending_path)
        update_status(order_dir, state="interrupted", phase="generate",
                      last_error="用户放弃未完成的生成 %s" % pending.get("run_id"))
        echo({"discarded": pending.get("run_id"), "calls_spent": pending.get("calls_spent")})
        return EXIT_OK

    if pending and not args.resume:
        print("有未完成的生成（%s，第 %s 轮，编号 %s 起 %s 张）：用 --resume 继续，"
              "或 --discard-pending 放弃。" % (pending.get("run_id"), pending.get("round"),
                                              pending.get("start_index"), pending.get("count")))
        return EXIT_INPUT
    if args.resume and not pending:
        print("没有可续跑的生成（缺 %s）" % PENDING)
        return EXIT_INPUT

    if pending:
        plan = pending
    else:
        try:
            plan = build_plan(order_dir, brief, args, doc)
        except CapabilityError as e:
            print("generation capability: %s" % e)
            return EXIT_CAPABILITY
        except (ValueError, OSError) as e:
            print("输入错误：%s" % e)
            return EXIT_INPUT

    errors, warnings = dry_run(plan, doc)
    need = plan["count"] * len(plan["stages"])
    ledger = rl.load_ledger(order_dir, None)
    remaining = ledger["budget_limit"] - ledger["budget_used"]
    if args.dry_run or errors:
        echo({"plan": {k: plan[k] for k in ("order_id", "round", "prefix", "start_index",
                                            "count", "size", "seed", "stages")},
              "render_size": "%sx%s" % (plan.get("width", plan["size"]),
                                        plan.get("height", plan["size"])),
              "calls_needed": need, "budget_remaining": remaining,
              "errors": errors, "warnings": warnings})
        if errors:
            print("已停止：生成能力不满足，不换后端、不退回文生图；按上面的错误修改 brief 后重新确认。")
            return EXIT_CAPABILITY
        return EXIT_OK
    for w in warnings:
        print("WARNING: " + w)

    if not pending:
        if need > remaining and not args.allow_over_budget:
            print("预算不足：本次需要 %d 次调用（%d 张 × %d 段），当前订单剩余 %d / 上限 %d。"
                  % (need, plan["count"], len(plan["stages"]), remaining, ledger["budget_limit"]))
            print("必须先询问用户是否追加；用户确认后加 --allow-over-budget 重跑。")
            return EXIT_BUDGET
        plan["allow_over_budget"] = bool(args.allow_over_budget)
        write_json(pending_path, plan)

    update_status(order_dir, state="running", phase="generate",
                  budget={"limit": ledger["budget_limit"], "used": ledger["budget_used"],
                          "remaining": max(0, remaining)}, last_error=None)
    try:
        results, calls = runner.run(to_request(plan), plan["stages"], order_dir, doc)
    except NeedsAgent as e:
        plan["calls_spent"] = int(plan.get("calls_spent") or 0) + int(e.calls_used or 0)
        write_json(pending_path, plan)
        update_status(order_dir, state="awaiting_agent", phase="generate",
                      pending_calls=[os.path.basename(p) for p in e.call_files])
        echo({"needs_agent": [os.path.basename(p) for p in e.call_files],
              "next": "宿主按调用单执行并把图放回订单目录后运行：generate.py --order <订单目录> --resume"})
        return EXIT_NEEDS_AGENT
    except CapabilityError as e:
        plan["calls_spent"] = int(plan.get("calls_spent") or 0) + int(e.calls_used or 0)
        if plan["calls_spent"] == 0:
            os.remove(pending_path)
        else:
            write_json(pending_path, plan)
        update_status(order_dir, state="failed", phase="generate", last_error=str(e))
        print("generation capability: %s" % e)
        return EXIT_CAPABILITY
    except AdapterError as e:
        plan["calls_spent"] = int(plan.get("calls_spent") or 0) + int(e.calls_used or 0)
        write_json(pending_path, plan)
        update_status(order_dir, state="failed", phase="generate", last_error=str(e))
        print("后端调用失败：%s" % e)
        print("已花 %d 次调用记在 %s；修复后 --resume 续跑，或 --discard-pending 放弃。"
              % (plan["calls_spent"], PENDING))
        return EXIT_BACKEND

    calls_total = calls + int(plan.get("calls_spent") or 0)
    files = expected_files(order_dir, plan)
    try:
        ledger, entry = record_round(order_dir, plan, calls_total, files,
                                     plan.get("allow_over_budget"))
    except ValueError as e:
        print("记账失败：%s" % e)
        return EXIT_INPUT
    append_log(order_dir, plan, results, calls_total, files)
    append_prompts(order_dir, plan, files)
    os.remove(pending_path)
    raw_count = len([n for n in os.listdir(order_dir)
                     if re.match(r".+-avatar-\d+-src\.(?:png|jpe?g|webp)$", n, re.I)])
    update_status(order_dir, state="generated", phase="generate", raw_count=raw_count,
                  budget={"limit": ledger["budget_limit"], "used": ledger["budget_used"],
                          "remaining": max(0, ledger["remaining"])},
                  last_error=None, pending_calls=[])
    echo({"run_id": plan["run_id"], "round": plan["round"], "files": files,
          "calls": calls_total, "images": len(files),
          "reproducible": all(r.reproducible for r in results) if results else False,
          "budget": {"limit": ledger["budget_limit"], "used": ledger["budget_used"],
                     "remaining": max(0, ledger["remaining"])},
          "over_budget": entry.get("over_budget", False),
          "next": "运行 avatar_pipeline.py 评分（--raw <订单目录> --brief <订单目录>\\brief.json ...）"})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
