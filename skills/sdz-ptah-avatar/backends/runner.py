# -*- coding: utf-8 -*-
"""阶段编排：single 或 identity → style 两段式，支持中断后续跑。

- 两段式时第一段产物改名为 `<prefix>-avatar-<N>-s1.<ext>` 保留作证据，
  第二段以它为 init_image，输出标准 `<prefix>-avatar-<N>-src.png` 交给评分流水线。
- 续跑：已存在的 `-s1` / `-src` 不重做、不重复计数；宿主侧缺图时把所有缺图的调用单
  一次性写出（NeedsAgent）。
- 失败时异常上带 `calls_used`（本次已花掉的调用数），由调用方记账，不丢预算。
"""

import copy
import os

from . import registry
from .base import (AdapterError, CapabilityError, GeneratedImage, NeedsAgent,
                   image_size, sha256_of)

EXTS = (".png", ".jpg", ".jpeg", ".webp")


def find_output(out_dir, prefix, index, tag="src"):
    for ext in EXTS:
        p = os.path.join(out_dir, "%s-avatar-%d-%s%s" % (prefix, index, tag, ext))
        if os.path.isfile(p):
            return p
    return None


def _seed_for(base_req, offset):
    return None if base_req.seed is None else (base_req.seed + offset) % (2 ** 32)


def _stage_req(base, stage_cfg, stage):
    r = copy.deepcopy(base)
    r.stage = stage
    for k in ("negative", "seed", "steps", "guidance", "denoise", "prompt_en", "prompt_zh"):
        if k in stage_cfg:
            setattr(r, k, stage_cfg[k])
    return r


def _runs(indices):
    """[3,4,6,7,8] -> [(3,2),(6,3)]：把缺图编号切成连续段。"""
    out = []
    for i in sorted(indices):
        if out and out[-1][0] + out[-1][1] == i:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((i, 1))
    return out


def _existing_image(path, index, seed):
    w, h = image_size(path)
    return GeneratedImage(path, index, seed, w, h, sha256_of(path))


def _run_stage(adapter, base_req, stage_cfg, name, todo, out_dir, references):
    """对 todo 编号逐个连续段调用；返回 (results, calls, {index: image})。"""
    results, calls, got, waiting = [], 0, {}, []
    for start, cnt in _runs(todo):
        r = _stage_req(base_req, stage_cfg, name)
        r.start_index, r.count, r.references = start, cnt, references
        if "seed" not in stage_cfg:
            r.seed = _seed_for(base_req, start - base_req.start_index)
        try:
            res = adapter.generate(r, out_dir)
        except NeedsAgent as e:
            waiting += e.call_files
            continue
        except AdapterError as e:
            e.calls_used = calls + getattr(e, "calls_used", 0)
            raise
        results.append(res)
        calls += res.calls
        for img in res.images:
            got[img.index] = img
    if waiting:
        e = NeedsAgent(waiting)
        e.calls_used = calls
        raise e
    return results, calls, got


def run(base_req, stages, out_dir, doc=None, budget_remaining=None):
    """stages: [{"stage": "single"|"identity"|"style", "backend": "<id>", ...}]

    返回 (results, calls_used)。预算按 calls 计；超预算直接拒绝，不静默截断。
    """
    doc = doc or registry.load()
    if not stages:
        raise CapabilityError("generation.stages 为空")
    names = [s.get("stage", "single") for s in stages]
    if names not in (["single"], ["identity"], ["identity", "style"]):
        raise CapabilityError("不支持的阶段组合 %s（允许 single / identity / identity→style）" % names)

    need = base_req.count * len(stages)
    if budget_remaining is not None and need > budget_remaining:
        raise CapabilityError("本次需要 %d 次调用，超过剩余预算 %d；请先确认是否追加"
                              % (need, budget_remaining))

    prefix = base_req.prefix
    indices = [base_req.start_index + k for k in range(base_req.count)]
    first = registry.get(doc, stages[0]["backend"])
    results, total = [], 0
    try:
        if len(stages) == 1:
            if first.kind == "agent_side":
                todo = indices                      # 宿主产物在 ingest 时计数
            else:
                todo = [i for i in indices if not find_output(out_dir, prefix, i, "src")]
            res, c, _ = _run_stage(first, base_req, stages[0], names[0], todo, out_dir,
                                   base_req.references)
            results += res
            total += c
            return results, total

        # 两段式 · 第一段（身份步）
        s1 = {i: find_output(out_dir, prefix, i, "s1") for i in indices}
        todo = [i for i in indices if not s1[i]]
        res, c, got = _run_stage(first, base_req, stages[0], names[0], todo, out_dir,
                                 base_req.references)
        results += res
        total += c
        stage1 = []
        for k, i in enumerate(indices):
            if i in got:
                img = got[i]
                ext = os.path.splitext(img.path)[1] or ".png"
                dst = os.path.join(out_dir, "%s-avatar-%d-s1%s" % (prefix, i, ext))
                os.replace(img.path, dst)
                img.path = dst
                stage1.append(img)
            else:
                stage1.append(_existing_image(s1[i], i, _seed_for(base_req, k)))

        # 两段式 · 第二段（画风步）
        second = registry.get(doc, stages[1]["backend"])
        for k, img in enumerate(stage1):
            if find_output(out_dir, prefix, img.index, "src"):
                continue                            # 续跑：该张已完成
            r2 = _stage_req(base_req, stages[1], "style")
            r2.references = []
            r2.init_image = img.path
            r2.count = 1
            r2.start_index = img.index
            if "seed" not in stages[1]:
                # 画风步默认沿用该图身份步的 seed（逐张不同）；显式指定时才覆盖
                r2.seed = img.seed if img.seed is not None else _seed_for(base_req, k)
            try:
                res2 = second.generate(r2, out_dir)
            except AdapterError as e:
                e.calls_used = getattr(e, "calls_used", 0)
                raise
            results.append(res2)
            total += res2.calls
        return results, total
    except AdapterError as e:
        e.calls_used = total + getattr(e, "calls_used", 0)
        raise
