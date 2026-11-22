#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动漫头像流水线：人脸定位 -> 裁切补偿 -> 裁切后评分 -> 硬门槛 -> 多格式导出。

只用 Pillow（本机已装），不依赖 numpy / opencv / 任何模型。
规则、阈值、原因码与放大说明见 ../references/protocol.md。

用法：
  py -3 avatar_pipeline.py --raw <原始图目录> --out <输出目录> \
        [--brief brief.json] [--formats 1024,2k,4k] [--jpg] [--jpg-quality 88]
"""

import argparse
import json
import math
import os
import sys
from collections import deque
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageStat

VERSION = "1.2.0"

# ---------------------------------------------------------------- 判别阈值
# 肤色：动漫皮肤偏浅，实测 Cr-Cb 为 +3~+8、Cr 中位 133~138；
# 白发/白衣服/浅背景的 Cr-Cb 为负，可据此分开。
# 小麦色/暖色皮肤 Cb 会低到 90 左右，旧下限 110 会把整张脸判成非皮肤，
# 使单眼侧脸彻底失去回退定位；下限放到 85，白色（Cb≈Cr）仍被 DELTA 挡住。
SKIN_CR = (126, 170)
SKIN_CB = (85, 146)
SKIN_DELTA = 2          # Cr - Cb 下限
SKIN_Y_MIN = 100

# 眼睛：虹膜是高饱和色块，用「高饱和连通块配对」做锚点
SAT_MIN = 60            # 虹膜饱和度高（实测 73~108），背景渐变/手部约 45~52
VAL_MIN = 70
EYE_AREA_MIN = 6        # 采样网格块数下限（约 0.003% 画面）
EYE_AREA_MAX = 0.015    # 占采样画面比例上限，排除大块背景/衣物

# 构图 / 评分（与方案文档 1.3、3.4 一致）
TARGET_RATIOS = [0.56, 0.62, 0.68]   # 三档候选裁切的目标脸占比
EYE_TARGET = 0.26                    # 眼睛在裁切图中的目标纵向位置
MARGIN_MIN = 0.025                   # 硬门槛：正样本顶部发丝贴边约 2.9%，故由 3% 放宽到 2.5%
MARGIN_BUILD = 0.05                  # 裁切时预留的余量（略高于门槛）
CHIN_MIN = 0.05
CHIN_BUILD = 0.06

FACE_RATIO_MIN, FACE_RATIO_MAX = 0.34, 0.82
EYE_MIN, EYE_MAX = 0.12, 0.36
# 侧脸单眼走「头部包围盒」定位，箱体含头发/帽子，眼位天然比正脸低。
# 以两张人工认可的正样本校准：眼位 0.391 / 0.409，共同区间取 0.32~0.46，
# 目标 0.40。旧档 0.28~0.50 过宽、目标 0.44 偏下，会把「眼位偏低」的图放过。
PROFILE_EYE_MIN, PROFILE_EYE_MAX = 0.32, 0.46
PROFILE_EYE_TARGET = 0.40
CONF_MIN = 0.40
THUMB_MIN = 0.35
BLUR_MIN = 8.0                       # 面部锐度下限（LOG 前的原始 stddev）

# 评分权重（校准后）：构图占 60、面部质量 40；构图内部以眼位为主，
# 脸占比只作弱约束（侧脸头框本身含头发，面积噪声大）。
W_FACE_QUALITY = 40
W_COMPOSITION = 60
W_RATIO, W_EYE, W_MARGIN = 0.10, 0.65, 0.25
RATIO_TARGET = 0.62
RATIO_TOL = 0.20
EYE_TOL = 0.14                       # 正脸眼位容差
PROFILE_EYE_TOL = 0.08               # 侧脸眼位是主判据，收得更紧
MARGIN_TOL = 0.04
COST_TOL = 0.10
COST_PENALTY = 10

# 导出档位：均在 1920 原生之上按需放大
SIZE_TIERS = {"1024": 1024, "2k": 2048, "4k": 4096}
NATIVE_LIMIT = 1920

REASON_LABEL = {
    "no_face": "未检出人脸",
    "multi_face": "多人/多脸",
    "low_conf": "人脸可信度低",
    "composition_bad:face_ratio": "脸占比越界",
    "composition_bad:eye_y": "眼睛纵向位置越界",
    "composition_bad:edge_margin": "关键部位贴边",
    "thumb_unreadable": "缩图不可读",
    "duplicate": "重复图",
    "face_blurry": "面部发糊",
}


# ---------------------------------------------------------------- 基础工具

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def resample():
    return getattr(Image, "Resampling", Image).LANCZOS


def is_skin(Y, Cb, Cr):
    return (Y >= SKIN_Y_MIN and SKIN_CR[0] <= Cr <= SKIN_CR[1]
            and SKIN_CB[0] <= Cb <= SKIN_CB[1] and (Cr - Cb) >= SKIN_DELTA)


def _grid_components(marked, cw, ch):
    """8 连通域标记，返回 cells 列表（每个元素是 [(gx,gy), ...]）。"""
    seen = [[False] * cw for _ in range(ch)]
    comps = []
    for y in range(ch):
        for x in range(cw):
            if not marked[y][x] or seen[y][x]:
                continue
            q = deque([(x, y)])
            seen[y][x] = True
            cells = []
            while q:
                cx, cy = q.popleft()
                cells.append((cx, cy))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < cw and 0 <= ny < ch and marked[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            q.append((nx, ny))
            comps.append(cells)
    return comps


# ---------------------------------------------------------------- 人脸定位

def _skin_map(img, work_w=240, cell=4, ratio=0.35):
    """返回 (grid, cw, ch, comps, scale)，scale = 全分辨率 / 采样坐标。"""
    W, H = img.size
    w = min(work_w, W)
    h = int(round(H * w / float(W)))
    small = img.convert("YCbCr").resize((w, h), Image.BILINEAR)
    px = small.load()
    cw, ch = w // cell, h // cell
    grid = [[0.0] * cw for _ in range(ch)]
    for gy in range(ch):
        for gx in range(cw):
            hit = tot = 0
            for y in range(gy * cell, (gy + 1) * cell):
                for x in range(gx * cell, (gx + 1) * cell):
                    Y, Cb, Cr = px[x, y]
                    tot += 1
                    if is_skin(Y, Cb, Cr):
                        hit += 1
            grid[gy][gx] = hit / float(tot) if tot else 0.0
    marked = [[grid[y][x] >= ratio for x in range(cw)] for y in range(ch)]
    comps = _grid_components(marked, cw, ch)
    comps.sort(key=len, reverse=True)
    return grid, cw, ch, comps, W / float(w)


def _face_skin_component(comps):
    """在肤色连通块里选「脸块」：面积达到最大块 45% 以上且最靠上的一块。

    侧身/半身头像里，肩颈与面部常被同一肤色阈值判成相邻大块；
    只取最大块会把脸框落到肩颈上（眼线随之下移），因此加入「靠上」规则。
    """
    if not comps:
        return None
    max_area = len(comps[0])
    cands = [c for c in comps if len(c) >= 0.45 * max_area]
    return min(cands, key=lambda cells: min(y for _, y in cells))


def _skin_fallback(img):
    """皮肤区域回退定位：取最大皮肤块的上部主体作为脸框。"""
    grid, cw, ch, comps, scale = _skin_map(img)
    cells = _face_skin_component(comps)
    if cells is None:
        return None
    cell = 4
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    bx0, bx1 = min(xs) * cell, (max(xs) + 1) * cell
    by0, by1 = min(ys) * cell, (max(ys) + 1) * cell

    # 逐行统计皮肤跨度，取最宽行定脸宽（脖子/手比脸窄）
    rows = {}
    for (gx, gy) in cells:
        lo, hi = rows.get(gy, (10 ** 9, -1))
        rows[gy] = (min(lo, gx), max(hi, gx))
    best_row, best_span = by0, 0
    for gy, (lo, hi) in rows.items():
        if gy > by0 + (by1 - by0) * 0.65:
            continue
        span = hi - lo
        if span > best_span:
            best_span, best_row = span, gy

    face_w = max(best_span * cell, (bx1 - bx0) * 0.55)
    face_h = face_w * 1.32
    W, H = img.size
    # 头像场景里脸不可能只占画面一小条；过小说明选错了色块
    if face_w * scale < 0.12 * min(W, H):
        return None
    cx = (bx0 + bx1) * 0.5
    top = min(by0, best_row * cell - 0.10 * face_h)
    box = (clamp((cx - face_w / 2) * scale, 0, W - 4),
           clamp(top * scale, 0, H - 4),
           clamp(face_w * scale, 4, W),
           clamp(face_h * scale, 4, H))
    eye_y = box[1] + 0.47 * box[3]
    return {"box": box, "eye_y": eye_y, "eyes": [], "conf": 0.45,
            "candidates": 1, "method": "skin-fallback", "eye_source": "estimated"}


def _sclera_eye(img, skin_box):
    """单眼侧脸：在肤色框上部找「白色眼白 + 深色瞳孔」块，返回眼中心。"""
    W, H = img.size
    work_w = 480
    w = min(work_w, W)
    h = int(round(H * w / float(W)))
    small = img.convert("RGB").resize((w, h), Image.BILINEAR)
    px = small.load()
    cell = 4
    cw, ch = w // cell, h // cell
    sx = w / float(W)
    bx0, by0, bx1, by1 = [v * sx for v in skin_box]
    bw_box, bh_box = max(1.0, bx1 - bx0), max(1.0, by1 - by0)

    marked = [[False] * cw for _ in range(ch)]
    for gy in range(ch):
        for gx in range(cw):
            hit = tot = 0
            for y in range(gy * cell, (gy + 1) * cell):
                for x in range(gx * cell, (gx + 1) * cell):
                    r, g, b = px[x, y]
                    tot += 1
                    if min(r, g, b) >= 185 and max(r, g, b) - min(r, g, b) <= 28:
                        hit += 1
            if tot and hit / float(tot) >= 0.5:
                marked[gy][gx] = True

    best = None
    for cells in _grid_components(marked, cw, ch):
        if not (12 <= len(cells) <= 0.05 * cw * ch):
            continue
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        x0, x1 = min(xs) * cell, (max(xs) + 1) * cell
        y0, y1 = min(ys) * cell, (max(ys) + 1) * cell
        bw, bh = float(x1 - x0), float(y1 - y0)
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if not (0.55 <= bw / max(1.0, bh) <= 2.6):
            continue
        rel_x = (cx - bx0) / bw_box
        if not (0.08 <= rel_x <= 0.82):
            continue
        # 眼白在肤色框上部；放宽到 60% 以覆盖四分之三侧脸，
        # 再往下多半是牙齿或衣领
        rel_y = (cy - by0) / bh_box
        if not (0.0 <= rel_y <= 0.60):
            continue
        if not (0.07 * bw_box <= bw <= 0.30 * bw_box):
            continue
        # 眼白周围必须有深色瞳孔/描线，排除高光与白帽檐
        ex0, ey0 = int(max(0, x0 - bw * 0.3)), int(max(0, y0 - bh * 0.3))
        ex1, ey1 = int(min(w, x1 + bw * 0.3)), int(min(h, y1 + bh * 0.3))
        dark = tot = 0
        dark_x = dark_y = 0.0
        for yy in range(ey0, ey1):
            for xx in range(ex0, ex1):
                r, g, b = px[xx, yy]
                tot += 1
                if 0.299 * r + 0.587 * g + 0.114 * b < 95:
                    dark += 1
                    dark_x += xx
                    dark_y += yy
        if tot and dark / float(tot) < 0.05:
            continue
        if best is None or len(cells) > best["area"]:
            # 眼白框中心会被下眼睑高光拉低；有足够深色像素时用瞳孔/描线重心
            if dark >= 30:
                pcx, pcy = dark_x / dark, dark_y / dark
            else:
                pcx, pcy = cx, cy
            best = {"area": len(cells), "cx": pcx / sx, "cy": pcy / sx,
                    "w": bw / sx, "h": bh / sx}

    if not best:
        return None
    return {"center": (round(best["cx"], 1), round(best["cy"], 1)),
            "size": (round(best["w"], 1), round(best["h"], 1)),
            "area": best["area"]}


def _profile_geometry(img, cells, scale, eye):
    """由单眼 + 肤色轮廓 + 非背景内容，求头部包围盒与下巴线。"""
    W, H = img.size
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    skin_box = (min(xs) * 4 * scale, min(ys) * 4 * scale,
                (max(xs) + 1) * 4 * scale, (max(ys) + 1) * 4 * scale)
    skin_h = max(1.0, skin_box[3] - skin_box[1])

    work_w = 240
    w = min(work_w, W)
    h = int(round(H * w / float(W)))
    small = img.convert("RGB").resize((w, h), Image.BILINEAR)
    px = small.load()
    corners = [(3, 3), (w - 4, 3), (3, h - 4), (w - 4, h - 4)]
    bg = [sum(px[x, y][i] for x, y in corners) / 4.0 for i in range(3)]
    mask = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            d = ((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2) ** 0.5
            if d > 42:
                mask[y][x] = True

    sx = W / float(w)
    ex = int(clamp(eye[0] / sx, 0, w - 1))
    # 眼睛本身是白色，扫描行落在眼白上会与背景连成一片；
    # 下移 10% 肤色高度到脸颊取头宽。
    probe = int(clamp((eye[1] + 0.10 * skin_h) / sx, 0, h - 1))

    def run_at(y):
        if y < 0 or y >= h or not mask[y][ex]:
            return None
        lo = ex
        while lo - 1 >= 0 and mask[y][lo - 1]:
            lo -= 1
        hi = ex
        while hi + 1 < w and mask[y][hi + 1]:
            hi += 1
        return lo, hi

    best_run = None
    for dy in (0, 2, 4, 6, 8, 10, -2, -4):
        r = run_at(probe + dy)
        if r and (best_run is None or (r[1] - r[0]) > (best_run[1] - best_run[0])):
            best_run = r
    if best_run is None:
        return None

    left, right = best_run[0] * sx, (best_run[1] + 1) * sx
    width = right - left
    if width <= 0:
        return None

    band0 = int(clamp((eye[0] - 0.5 * width) / sx, 0, w - 1))
    band1 = int(clamp((eye[0] + 0.5 * width) / sx, 1, w))
    top = None
    for y in range(0, probe + 1):
        if any(mask[y][x] for x in range(band0, band1)):
            top = y * sx
            break
    if top is None:
        top = skin_box[1]

    rows = {}
    for (gx, gy) in cells:
        lo, hi = rows.get(gy, (10 ** 9, -1))
        rows[gy] = (min(lo, gx), max(hi, gx))
    spans = {gy: (hi - lo + 1) * 4 * scale for gy, (lo, hi) in rows.items()}
    max_span = max(spans.values()) if spans else width
    chin = None
    for gy in sorted(spans):
        y = gy * 4 * scale
        if y > eye[1] + 0.15 * width and spans[gy] < 0.55 * max_span:
            chin = y
            break
    if chin is None:
        chin = eye[1] + 0.62 * width

    return left, right, top, chin


def _content_top(img, cx, head_w):
    """在头部横向范围内，返回非背景内容的最上沿 y（全分辨率）。"""
    W, H = img.size
    work_w = 240
    w = min(work_w, W)
    h = int(round(H * w / float(W)))
    small = img.convert("RGB").resize((w, h), Image.BILINEAR)
    px = small.load()
    corners = [(3, 3), (w - 4, 3), (3, h - 4), (w - 4, h - 4)]
    bg = [sum(px[x, y][i] for x, y in corners) / 4.0 for i in range(3)]
    sx = W / float(w)
    x0 = int(clamp((cx - 0.55 * head_w) / sx, 0, w - 1))
    x1 = int(clamp((cx + 0.55 * head_w) / sx, 1, w))
    for y in range(h):
        for x in range(x0, x1):
            r, g, b = px[x, y]
            d = ((r - bg[0]) ** 2 + (g - bg[1]) ** 2 + (b - bg[2]) ** 2) ** 0.5
            if d > 42:
                return y * sx
    return None


def _profile_head(img, cells, scale):
    """无眼白锚点时：肤色块上部定头宽，内容上沿定头顶，按比例估眼线。"""
    W, H = img.size
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    bx0 = min(xs) * 4 * scale
    by0 = min(ys) * 4 * scale
    comp_h = (max(ys) - min(ys) + 1) * 4 * scale

    rows = {}
    for (gx, gy) in cells:
        lo, hi = rows.get(gy, (10 ** 9, -1))
        rows[gy] = (min(lo, gx), max(hi, gx))
    top_limit = by0 + 0.55 * comp_h
    spans = [(gy, lo, hi) for gy, (lo, hi) in rows.items()
             if gy * 4 * scale <= top_limit]
    if not spans:
        return None
    _, lo, hi = max(spans, key=lambda t: t[2] - t[1])
    head_w = (hi - lo + 1) * 4 * scale
    cx = (lo + hi + 1) * 2 * scale
    if head_w < 0.18 * min(W, H):
        return None

    head_h = head_w * 1.02
    top = by0
    ct = _content_top(img, cx, head_w)
    if ct is not None:
        top = min(top, max(ct, by0 - 0.06 * H))
    eye_y = top + 0.55 * head_h
    box = (clamp(cx - head_w / 2.0, 0, W - 4), clamp(top, 0, H - 4),
           clamp(head_w, 4, W), clamp(head_h, 4, H))
    return {"box": box, "eye_y": eye_y, "eyes": [], "conf": 0.42,
            "candidates": 1, "method": "profile-head",
            "eye_source": "estimated", "profile": True}


def _profile_fallback(img):
    """单眼侧脸回退：肤色块 + 眼白锚点 + 头部内容包围盒。"""
    W, H = img.size
    grid, cw, ch, comps, scale = _skin_map(img)
    cells = _face_skin_component(comps)
    if cells is None:
        return None
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    bx0, bx1 = min(xs) * 4, (max(xs) + 1) * 4
    by0, by1 = min(ys) * 4, (max(ys) + 1) * 4

    rows = {}
    for (gx, gy) in cells:
        lo, hi = rows.get(gy, (10 ** 9, -1))
        rows[gy] = (min(lo, gx), max(hi, gx))
    best_row, best_span = by0, 0
    for gy, (lo, hi) in rows.items():
        if gy > by0 + (by1 - by0) * 0.65:
            continue
        span = hi - lo
        if span > best_span:
            best_span, best_row = span, gy

    face_w = max(best_span * 4, (bx1 - bx0) * 0.55)
    if face_w * scale < 0.12 * min(W, H):
        return None

    skin_box = (bx0 * scale, by0 * scale, bx1 * scale, by1 * scale)
    eye = _sclera_eye(img, skin_box)
    if not eye:
        # 白色皮肤的角色（如浅色侧脸）眼白与面部同色，找不到眼白锚点；
        # 改用头部轮廓按比例估眼线，仍走侧脸闸门，不伪装成高可信度。
        return _profile_head(img, cells, scale)

    geo = _profile_geometry(img, cells, scale, eye["center"])
    if not geo:
        return None
    left, right, top, chin = geo
    box = (clamp(left, 0, W - 4), clamp(top, 0, H - 4),
           clamp(right - left, 4, W), clamp(chin - top, 4, H))
    if box[2] < 0.18 * W or box[3] < 0.18 * H:
        return None

    return {"box": box, "eye_y": eye["center"][1], "eyes": [eye["center"]],
            "conf": 0.52, "candidates": 1, "method": "profile-sclera",
            "eye_source": "sclera-single", "profile": True}


def _sat_components(img, work_w=480, cell=4, ratio=0.25):
    """高饱和色块连通域，用于找虹膜。返回 [(bbox, cells, mean_sat)]（采样坐标）。"""
    W, H = img.size
    w = min(work_w, W)
    h = int(round(H * w / float(W)))
    small = img.convert("RGB").resize((w, h), Image.BILINEAR)
    px = small.load()
    cw, ch = w // cell, h // cell
    marked = [[False] * cw for _ in range(ch)]
    sat_sum = {}
    for gy in range(ch):
        for gx in range(cw):
            hit = tot = 0
            acc = 0
            for y in range(gy * cell, (gy + 1) * cell):
                for x in range(gx * cell, (gx + 1) * cell):
                    r, g, b = px[x, y]
                    mx, mn = max(r, g, b), min(r, g, b)
                    sat = mx - mn
                    tot += 1
                    if sat >= SAT_MIN and mx >= VAL_MIN:
                        hit += 1
                        acc += sat
            if tot and hit / float(tot) >= ratio:
                marked[gy][gx] = True
                sat_sum[(gx, gy)] = acc / float(hit)
    comps = []
    for cells in _grid_components(marked, cw, ch):
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        bw = (max(xs) - min(xs) + 1) * cell
        bh = (max(ys) - min(ys) + 1) * cell
        if len(cells) < 3 or bw < cell * 1.5 or bh < cell * 1.5:
            continue
        # 背景渐变/衣物常连到画面边缘，虹膜不会贴边
        if min(xs) <= 0 or min(ys) <= 0 or max(xs) >= cw - 1 or max(ys) >= ch - 1:
            continue
        if len(cells) > EYE_AREA_MAX * cw * ch:
            continue
        mean_sat = sum(sat_sum.get(c, 0) for c in cells) / float(len(cells))
        comps.append({"cells": cells, "bbox": (min(xs) * cell, min(ys) * cell, bw, bh),
                      "cx": (min(xs) * cell + bw / 2.0), "cy": (min(ys) * cell + bh / 2.0),
                      "area": len(cells), "sat": mean_sat})
    return comps, W / float(w), w, h


def _refine_eye(img, box, pad=0.5):
    """在全分辨率上，对给定窗口内的饱和像素求质心，得到虹膜中心。"""
    W, H = img.size
    x, y, w, h = box
    px0 = int(clamp(x - w * pad, 0, W - 1))
    py0 = int(clamp(y - h * pad, 0, H - 1))
    px1 = int(clamp(x + w * (1 + pad), 1, W))
    py1 = int(clamp(y + h * (1 + pad), 1, H))
    rgb = img.crop((px0, py0, px1, py1)).convert("RGB").load()
    rw, rh = px1 - px0, py1 - py0
    step = max(1, rw // 120)
    sx = sy = sw = 0.0
    for yy in range(0, rh, step):
        for xx in range(0, rw, step):
            r, g, b = rgb[xx, yy]
            mx, mn = max(r, g, b), min(r, g, b)
            sat = mx - mn
            if sat >= SAT_MIN and mx >= VAL_MIN:
                sx += (px0 + xx) * sat
                sy += (py0 + yy) * sat
                sw += sat
    if sw <= 0:
        return None
    return sx / sw, sy / sw


def detect_face(img):
    """返回 {box, eye_y, eyes, conf, candidates, method} 或 None。"""
    W, H = img.size
    detected = None
    comps, scale, sw, sh = _sat_components(img)

    # 配对：两个高饱和块，水平间距与高度差符合双眼几何
    best = None
    cand = [c for c in comps if c["cy"] < 0.80 * sh and c["area"] >= EYE_AREA_MIN]
    cand.sort(key=lambda c: -c["sat"])
    cand = cand[:12]
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            a, b = cand[i], cand[j]
            dx = abs(a["cx"] - b["cx"])
            dy = abs(a["cy"] - b["cy"])
            if not (0.05 * sw <= dx <= 0.42 * sw):
                continue
            if dy > 0.09 * sh:
                continue
            amin, amax = min(a["area"], b["area"]), max(a["area"], b["area"])
            if amin / float(amax) < 0.20:
                continue
            pair_y = (a["cy"] + b["cy"]) / 2.0 / sh
            if not (0.10 <= pair_y <= 0.62):
                continue
            score = (a["sat"] + b["sat"]) * math.sqrt(amin * amax)
            score *= 1.0 - abs(pair_y - 0.40) * 0.8
            score /= (1.0 + dy * 30.0)
            if best is None or score > best[0]:
                best = (score, a, b)

    if best:
        _, a, b = best
        pa = _refine_eye(img, [v * scale for v in a["bbox"]])
        pb = _refine_eye(img, [v * scale for v in b["bbox"]])
        eyes = [e for e in (pa, pb) if e]
        if len(eyes) == 2:
            d = abs(eyes[0][0] - eyes[1][0])
            if d >= W * 0.08:
                eye_y = (eyes[0][1] + eyes[1][1]) / 2.0
                cx = (eyes[0][0] + eyes[1][0]) / 2.0
                fw = 2.00 * d
                fh = 1.65 * d
                raw_top = eye_y - 0.42 * fh
                # 合理性校验：头顶被推出画面或脸框高过整幅 85%，
                # 说明配到的不是双眼（常见于把头发/耳饰高光配成一对），
                # 放弃双眼锚点，让侧脸/肤色回退接手。
                if raw_top >= -0.02 * H and fh <= 0.85 * H:
                    top = raw_top
                    box = (clamp(cx - fw / 2.0, 0, W - 4), clamp(top, 0, H - 4),
                           clamp(fw, 4, W), clamp(fh, 4, H))
                    detected = {"box": box, "eye_y": eye_y, "eyes": eyes,
                                "conf": 0.55, "method": "eye-pair",
                                "eye_source": "detected", "profile": False}

    skin = _skin_fallback(img)
    multi = _count_faces(img)

    if detected is None:
        # 单眼侧脸配不出双眼，改走「眼白 + 头部轮廓」定位；
        # 失败才退回纯肤色块估算。
        prof = _profile_fallback(img)
        if prof:
            prof["candidates"] = multi if multi > 1 else 1
            prof["skin_coverage"] = round(skin_coverage(img, prof["box"]), 3)
            return prof
        if skin is None:
            return None
        skin["candidates"] = multi if multi > 1 else 1
        return skin

    # 用皮肤覆盖率给眼睛锚定结果打分；覆盖率过低说明配错了对象
    cov = skin_coverage(img, detected["box"])
    sat_mean = 0.0
    if detected["eyes"]:
        sat_mean = 110.0
    conf = 0.55 + 0.25 * min(1.0, sat_mean / 110.0) + 0.20 * min(1.0, cov / 0.35)
    detected["conf"] = round(clamp(conf, 0.0, 0.97), 3)
    detected["skin_coverage"] = round(cov, 3)
    detected["candidates"] = multi if multi > 1 else 1

    # 眼睛锚点明显不合理时，退回皮肤定位
    if cov < 0.06:
        skin = skin or _skin_fallback(img)
        if skin:
            return skin
    return detected


def skin_coverage(img, box):
    """脸框内皮肤像素占比（抽样）。"""
    W, H = img.size
    x, y, w, h = [int(v) for v in box]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    region = img.crop((x0, y0, x1, y1)).convert("YCbCr")
    step = max(1, min(region.size) // 60)
    px = region.load()
    hit = tot = 0
    for yy in range(0, region.size[1], step):
        for xx in range(0, region.size[0], step):
            Y, Cb, Cr = px[xx, yy]
            tot += 1
            if is_skin(Y, Cb, Cr):
                hit += 1
    return hit / float(tot) if tot else 0.0


def _count_faces(img):
    """统计「脸大小级别」的皮肤连通块，且要求成对、横向错开、纵向齐平，
    避免把背景色块或衣领误判成第二张脸。"""
    grid, cw, ch, comps, scale = _skin_map(img)
    if not comps:
        return 0
    work_area = float(cw * ch * 16)   # 采样像素面积
    faces = []
    for cells in comps:
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        bw = (max(xs) - min(xs) + 1) * 4
        bh = (max(ys) - min(ys) + 1) * 4
        cx = (min(xs) * 4 + bw / 2.0)
        cy = (min(ys) * 4 + bh / 2.0)
        aspect = bw / float(bh) if bh else 0
        if bw * bh < 0.02 * work_area:
            continue
        if not (0.5 <= aspect <= 2.0):
            continue
        if cy > 0.65 * (ch * 4):
            continue
        faces.append((cx, cy))
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            if abs(faces[i][1] - faces[j][1]) < 0.25 * (ch * 4) and \
                    abs(faces[i][0] - faces[j][0]) > 0.20 * (cw * 4):
                return 2
    return 1 if faces else 0


# ---------------------------------------------------------------- 裁切与评分

def build_crops(img, face):
    W, H = img.size
    fx, fy, fw, fh = face["box"]
    eye_y = face["eye_y"]
    face_area = float(fw * fh)
    eye_target = PROFILE_EYE_TARGET if face.get("profile") else EYE_TARGET
    out = []
    for target in TARGET_RATIOS:
        s = math.sqrt(face_area / (0.785 * target))
        s = min(s, min(W, H))
        ideal_left = fx + fw / 2.0 - s / 2.0
        ideal_top = eye_y - eye_target * s
        left, top = ideal_left, ideal_top
        # 头顶留白（略高于硬门槛）
        top = min(top, fy - MARGIN_BUILD * s)
        # 下巴留白
        top = max(top, (fy + fh) - s + CHIN_BUILD * s)
        left = clamp(left, 0, W - s)
        top = clamp(top, 0, H - s)
        # 裁切补偿代价：被约束推离理想位置的程度，用来区分「原生构图好坏」
        cost = (abs(left - ideal_left) + abs(top - ideal_top)) / s
        out.append({"box": (left, top, left + s, top + s), "target": target,
                    "cost": round(cost, 4)})

    # 全幅候选：已经紧构图的原图不应被二次放大裁切。
    # 代价按同一套理想位置计算，避免「保留原图」变成无条件满分。
    s = float(min(W, H))
    ideal_left = fx + fw / 2.0 - s / 2.0
    ideal_top = eye_y - eye_target * s
    left = clamp(ideal_left, 0, W - s)
    top = clamp(ideal_top, 0, H - s)
    cost = (abs(left - ideal_left) + abs(top - ideal_top)) / s
    out.append({"box": (left, top, left + s, top + s), "target": "full",
                "cost": round(cost, 4)})
    return out


def sharpness(img, box):
    x, y, w, h = [int(v) for v in box]
    region = img.crop((max(0, x), max(0, y), max(1, x + w), max(1, y + h))).convert("L")
    if region.width > 320:
        region = region.resize((320, max(1, int(320 * region.height / region.width))),
                               Image.BILINEAR)
    edges = region.filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).stddev[0]


def blown_ratio(img, box):
    """脸框内接近纯白的像素占比（过曝检测）。"""
    W, H = img.size
    x, y, w, h = [int(v) for v in box]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    region = img.crop((x0, y0, x1, y1)).convert("L")
    step = max(1, min(region.size) // 50)
    px = region.load()
    hit = tot = 0
    for yy in range(0, region.size[1], step):
        for xx in range(0, region.size[0], step):
            tot += 1
            if px[xx, yy] >= 245:
                hit += 1
    return hit / float(tot) if tot else 0.0


def score_crop(img, crop, face):
    left, top, right, bottom = crop["box"]
    s = right - left
    fx, fy, fw, fh = face["box"]
    eye_y = face["eye_y"]
    face_ratio = (fw * fh) / (0.785 * s * s)
    eye_center_y = (eye_y - top) / s
    margins = [fx - left, right - (fx + fw), fy - top, bottom - (fy + fh)]
    margin = min(margins) / s

    ratio_score = clamp(1 - abs(face_ratio - RATIO_TARGET) / RATIO_TOL, 0, 1)
    eye_target = PROFILE_EYE_TARGET if face.get("profile") else EYE_TARGET
    eye_tol = PROFILE_EYE_TOL if face.get("profile") else EYE_TOL
    eye_score = clamp(1 - abs(eye_center_y - eye_target) / eye_tol, 0, 1)
    margin_score = clamp(margin / MARGIN_TOL, 0, 1)
    cost = crop.get("cost", 0.0)
    composition = 100 * (W_RATIO * ratio_score + W_EYE * eye_score + W_MARGIN * margin_score)
    composition -= COST_PENALTY * clamp(cost / COST_TOL, 0, 1)   # 补偿代价扣分
    composition = clamp(composition, 0, 100)

    sharp = sharpness(img, face["box"])
    # 实测锐度 50~65（清晰头像），10 以下为明显发糊；对数映射拉开差距
    sharp_score = clamp(100 * (math.log(sharp + 1) - math.log(11.0)) /
                        (math.log(101.0) - math.log(11.0)), 0, 100)

    # 五官对称性：双眼高度差相对眼距，越小越正
    eyes = face.get("eyes") or []
    if len(eyes) == 2:
        d = abs(eyes[0][0] - eyes[1][0]) or 1.0
        tilt = abs(eyes[0][1] - eyes[1][1]) / d
        sym_score = 100 * (1 - clamp(tilt / 0.18, 0, 1))
        tilt_metric = round(tilt, 3)
    else:
        # 侧脸只有一只眼，对称性没有比较对象，记中性分而不是扣分
        sym_score, tilt_metric = (70.0 if face.get("profile") else 60.0), None

    # 曝光：过曝（贴白）面积过大说明细节被吃掉
    blown = blown_ratio(img, face["box"])
    exposure_score = 100 * (1 - clamp((blown - 0.25) / 0.50, 0, 1))
    face_quality = clamp(0.70 * sharp_score + 0.15 * sym_score + 0.15 * exposure_score, 0, 100)

    reasons = []
    if not (FACE_RATIO_MIN <= face_ratio <= FACE_RATIO_MAX):
        reasons.append("composition_bad:face_ratio")
    eye_min, eye_max = (PROFILE_EYE_MIN, PROFILE_EYE_MAX) if face.get("profile") \
        else (EYE_MIN, EYE_MAX)
    if not (eye_min <= eye_center_y <= eye_max):
        reasons.append("composition_bad:eye_y")
    if margin < MARGIN_MIN:
        reasons.append("composition_bad:edge_margin")
    if sharp < BLUR_MIN:
        reasons.append("face_blurry")

    total = (W_FACE_QUALITY * face_quality + W_COMPOSITION * composition) / \
            float(W_FACE_QUALITY + W_COMPOSITION)

    return {
        "box": [round(v, 1) for v in crop["box"]],
        "native_size": int(round(s)),
        "metrics": {
            "face_ratio": round(face_ratio, 3),
            "eye_center_y": round(eye_center_y, 3),
            "edge_margin": round(margin, 3),
            "sharpness": round(sharp, 2),
            "crop_cost": round(cost, 3),
            "eye_tilt": tilt_metric,
            "blown": round(blown, 3),
        },
        "scores": {
            "face_quality": round(face_quality, 1),
            "face_quality_parts": {
                "sharpness": round(sharp_score, 1),
                "symmetry": round(sym_score, 1),
                "exposure": round(exposure_score, 1),
            },
            "composition": round(composition, 1),
            "style": None,
            "emotion": None,
            "anatomy": None,
        },
        "total": round(total, 1),
        "reasons": reasons,
    }


def thumb_readability(crop_img):
    """96px 缩图可读性：灰度对比 + 边缘能量，归一化到 0~1。"""
    small = crop_img.convert("L").resize((96, 96), resample())
    contrast = ImageStat.Stat(small).stddev[0]
    edges = small.filter(ImageFilter.FIND_EDGES)
    edge = ImageStat.Stat(edges).stddev[0]
    val = 0.6 * clamp((edge - 2.0) / 12.0, 0, 1) + 0.4 * clamp((contrast - 20.0) / 45.0, 0, 1)
    return round(val, 3), round(edge, 2), round(contrast, 2)


def ahash(img, size=8):
    g = img.convert("L").resize((size, size), Image.BILINEAR)
    data = g.tobytes()
    avg = sum(data) / float(len(data))
    bits = 0
    for i, v in enumerate(data):
        if v > avg:
            bits |= (1 << i)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def draw_sheet(items, out_path, cell=300, label_h=34, cols=4):
    rows = int(math.ceil(len(items) / float(cols))) or 1
    sheet = Image.new("RGB", (cols * cell, rows * (cell + label_h)), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    for i, it in enumerate(items):
        cx = (i % cols) * cell
        cy = (i // cols) * (cell + label_h)
        thumb = it["image"].copy()
        thumb.thumbnail((cell, cell), resample())
        ox = cx + (cell - thumb.width) // 2
        oy = cy + (cell - thumb.height) // 2
        sheet.paste(thumb, (ox, oy))
        color = (60, 200, 120) if it["verdict"] == "PASS" else (230, 90, 90)
        draw.rectangle([cx + 1, cy + 1, cx + cell - 2, cy + cell + label_h - 2],
                       outline=color, width=2)
        tag = it.get("tag", "")
        label = "#%d %s %.1f %s" % (i + 1, tag, it["total"], it["verdict"])
        draw.text((cx + 6, cy + cell + 6), label[:34], fill=color)
        reason = it.get("reason", "")
        if reason:
            draw.text((cx + 6, cy + cell + 18), reason[:34], fill=(190, 190, 190))
    sheet.save(out_path, quality=92)
    return out_path


# ---------------------------------------------------------------- 导出

def upscale(img, size):
    """Lanczos 单次放大。不新增细节，只做像素插值。"""
    return img.resize((size, size), resample())


def export_image(master, native_crop, item_id, out_dir, formats, want_jpg, jpg_q):
    """写出各档 PNG（与可选 JPG），返回 outputs 字典与 upscale 说明。"""
    outputs = {}
    upscale_info = {"applied": False, "method": "none", "tiers": {}}

    png_master = os.path.join(out_dir, "crop", item_id + "_1024.png")
    master.save(png_master)
    outputs["png_1024"] = rel(png_master, out_dir)

    png_native = os.path.join(out_dir, "native", "%s_native_%d.png" % (item_id, native_crop.width))
    native_crop.save(png_native)
    outputs["png_native"] = rel(png_native, out_dir)

    for tier in formats:
        if tier == "1024":
            continue
        size = SIZE_TIERS[tier]
        factor = size / float(native_crop.width)
        big = upscale(native_crop, size)
        png_dir = os.path.join(out_dir, "export", "png", tier)
        os.makedirs(png_dir, exist_ok=True)
        p = os.path.join(png_dir, "%s_%s.png" % (item_id, tier))
        big.save(p)
        outputs["png_" + tier] = rel(p, out_dir)
        upscale_info["applied"] = True
        upscale_info["method"] = "Pillow LANCZOS (single-pass)"
        upscale_info["tiers"][tier] = {"size": size, "factor": round(factor, 2),
                                       "detail_added": False}
        if want_jpg:
            jp = os.path.join(out_dir, "export", "jpg", "%s_%s_q%d.jpg" % (item_id, tier, jpg_q))
            os.makedirs(os.path.dirname(jp), exist_ok=True)
            big.convert("RGB").save(jp, quality=jpg_q, optimize=True, progressive=True)
            outputs["jpg_%s_q%d" % (tier, jpg_q)] = rel(jp, out_dir)

    if want_jpg and "1024" in formats:
        jp = os.path.join(out_dir, "export", "jpg", "%s_1024_q%d.jpg" % (item_id, jpg_q))
        os.makedirs(os.path.dirname(jp), exist_ok=True)
        master.convert("RGB").save(jp, quality=jpg_q, optimize=True, progressive=True)
        outputs["jpg_1024_q%d" % jpg_q] = rel(jp, out_dir)

    return outputs, upscale_info


def rel(path, base):
    return os.path.relpath(path, base).replace("\\", "/")


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="anime avatar crop + score + export pipeline")
    ap.add_argument("--raw", required=True, help="原始生成图目录")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--brief", default=None, help="brief.json（可选，用于报告头）")
    ap.add_argument("--formats", default="1024", help="逗号分隔：1024,2k,4k")
    ap.add_argument("--jpg", action="store_true", help="额外导出 JPG 压缩版")
    ap.add_argument("--jpg-quality", type=int, default=88, help="JPG 质量（默认 88）")
    args = ap.parse_args()

    raw_dir, out_dir = args.raw, args.out
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    for f in formats:
        if f not in SIZE_TIERS:
            print("unknown format tier: %s" % f)
            return 2
    for sub in ("crop", "native", "preview"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    files = [f for f in sorted(os.listdir(raw_dir))
             if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    if not files:
        print("no images in %s" % raw_dir)
        return 2

    results = []
    for name in files:
        item_id = os.path.splitext(name)[0]
        path = os.path.join(raw_dir, name)
        try:
            img = Image.open(path)
            img.load()
            img = img.convert("RGB")
        except Exception as exc:
            results.append({"id": item_id, "file": name, "error": "open_failed:%s" % exc,
                            "verdict": "ERROR", "total": 0.0, "reasons": ["open_failed"]})
            continue

        face = detect_face(img)
        if not face:
            results.append({
                "id": item_id, "file": name, "verdict": "REJECT", "total": 0.0,
                "metrics": {}, "scores": {}, "gates": {"face": False},
                "reasons": ["no_face"], "outputs": {}, "detect": None})
            continue

        best = None
        best_any = None
        for cand in build_crops(img, face):
            sc = score_crop(img, cand, face)
            if best_any is None or sc["total"] > best_any["total"]:
                best_any = sc
            # 优先交付「构图闸门全过」的候选；已紧构图的原图用全幅候选，
            # 不再为了凑目标脸占比二次放大裁切。
            if not sc["reasons"] and (best is None or sc["total"] > best["total"]):
                best = sc
        if best is None:
            best = best_any

        left, top, right, bottom = [int(round(v)) for v in best["box"]]
        s = right - left
        native_crop = img.crop((left, top, left + s, top + s))
        master = native_crop.resize((1024, 1024), resample())

        tid, tedge, tcontrast = thumb_readability(master)
        reasons = list(best["reasons"])
        if tid < THUMB_MIN:
            reasons.append("thumb_unreadable")
        if face.get("candidates", 1) > 1:
            reasons.append("multi_face")
        if face["conf"] < CONF_MIN:
            reasons.append("low_conf")
        verdict = "PASS" if not reasons else "REJECT"

        # 圆形预览
        mask = Image.new("L", (256, 256), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 255, 255), fill=255)
        prev = master.resize((256, 256), resample())
        prev.putalpha(mask)
        prev_path = os.path.join(out_dir, "preview", item_id + "_circle.png")
        prev.save(prev_path)

        outputs, upinfo = export_image(master, native_crop, item_id, out_dir,
                                       formats, args.jpg, args.jpg_quality)
        outputs["preview"] = rel(prev_path, out_dir)

        # 放大后复检：构图比值与尺度无关；可读性/清晰度需复测
        recheck = None
        if upinfo["applied"]:
            big_tier = max(upinfo["tiers"].items(), key=lambda kv: kv[1]["factor"])
            tier, info = big_tier
            big = upscale(native_crop, info["size"])
            small = big.resize((480, 480), resample())
            eye_found = detect_face(small) is not None
            big_sharp = sharpness(big, [0, 0, big.width, big.height])
            recheck = {
                "tier": tier,
                "composition_unchanged": True,
                "composition_note": "构图指标为比例量，放大后不变，无需重算",
                "eye_recheck_passed": eye_found,
                "sharpness_full_frame": round(big_sharp, 2),
                "sharpness_note": "Lanczos 不产生新细节，锐度按像素统计仅作参考",
            }

        results.append({
            "id": item_id,
            "file": name,
            "verdict": verdict,
            "total": best["total"],
            "metrics": best["metrics"],
            "scores": best["scores"],
            "native_size": best["native_size"],
            "gates": {
                "face": True,
                "composition": not any(r.startswith("composition_bad") for r in reasons),
                "thumb": tid >= THUMB_MIN,
                "text": None,
                "nsfw": None,
                "emotion": None,
                "style": None,
                "anatomy": None,
            },
            "thumb": {"score": tid, "edge": tedge, "contrast": tcontrast},
            "reasons": reasons,
            "detect": {"method": face["method"], "conf": face["conf"],
                       "eye_source": face.get("eye_source"),
                       "profile": bool(face.get("profile")),
                       "eye_y": round(face["eye_y"], 1),
                       "face_box": [round(v, 1) for v in face["box"]],
                       "skin_coverage": face.get("skin_coverage")},
            "outputs": outputs,
            "upscale": upinfo,
            "recheck": recheck,
            "image": master,
            "ahash": ahash(master),
        })

    # 去重
    for i in range(len(results)):
        if "ahash" not in results[i]:
            continue
        for j in range(i + 1, len(results)):
            if "ahash" not in results[j]:
                continue
            if hamming(results[i]["ahash"], results[j]["ahash"]) <= 3:
                loser = j if results[i]["total"] >= results[j]["total"] else i
                results[loser]["verdict"] = "REJECT"
                if "duplicate" not in results[loser]["reasons"]:
                    results[loser]["reasons"].append("duplicate")

    passed = [r for r in results if r.get("verdict") == "PASS"]
    ranked = sorted(results, key=lambda r: r.get("total", 0), reverse=True)

    sheet_items = [{"image": r["image"], "total": r["total"], "verdict": r["verdict"],
                    "tag": r["id"], "reason": (r["reasons"][0] if r["reasons"] else "")}
                   for r in ranked if "image" in r]
    sheet_path = os.path.join(out_dir, "contact_sheet.jpg")
    if sheet_items:
        draw_sheet(sheet_items, sheet_path)

    brief = None
    brief_path = args.brief
    if brief_path and not os.path.isabs(brief_path):
        brief_path = os.path.join(raw_dir, brief_path)
    if brief_path and os.path.exists(brief_path):
        with open(brief_path, "r", encoding="utf-8") as fh:
            brief = json.load(fh)

    manifest = {
        "meta": {
            "tool": "anime-avatar/avatar_pipeline.py",
            "version": VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "agent-side",
            "reproducible": False,
            "native_limit": NATIVE_LIMIT,
            "formats": formats,
            "jpg": {"enabled": args.jpg, "quality": args.jpg_quality},
            "upscale": {
                "method": "Pillow LANCZOS (single-pass)",
                "available": True,
                "detail_added": False,
                "note": "2k/4k 均为插值放大，不新增细节；4k 的实际有效细节上限仍为原生裁切像素",
            },
        },
        "brief": brief,
        "capability": {
            "available": ["face_locate_eye_anchor", "face_locate_skin_fallback",
                          "face_locate_profile_single_eye", "crop_compensation",
                          "sharpness", "thumb_readability", "dedupe"],
            "unavailable": ["ocr_text", "nsfw", "emotion", "style_embedding", "anatomy",
                            "real_esrgan_superres"],
            "note": "无 OCR/NSFW/情绪/风格/解剖/超分模型，对应闸门记为 null，不参与判定；"
                    "分数按可用维度(face_quality 40 + composition 60)归一化",
        },
        "summary": {
            "total": len(results),
            "pass": len(passed),
            "reject": len(results) - len(passed),
            "best_total": ranked[0]["total"] if ranked else 0,
            "contact_sheet": "contact_sheet.jpg" if sheet_items else None,
        },
        "items": [{k: v for k, v in r.items() if k not in ("image", "ahash")} for r in ranked],
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    lines = ["# 头像候选报告", ""]
    if brief:
        lines += ["| 项 | 值 |", "|-|-|"]
        for k, v in brief.items():
            if isinstance(v, (str, int, float)):
                lines.append("| %s | %s |" % (k, v))
        lines.append("")
    lines += ["共 %d 张，通过 %d 张，驳回 %d 张；最高分 %.1f" %
              (len(results), len(passed), len(results) - len(passed),
               ranked[0]["total"] if ranked else 0), "",
              "| # | 图 | 总分 | 脸占比 | 眼睛位置 | 留边 | 锐度 | 缩图 | 结论 | 原因 |",
              "|-|-|-|-|-|-|-|-|-|-|"]
    for i, r in enumerate(ranked, 1):
        m = r.get("metrics", {})
        lines.append("| %d | %s | %.1f | %s | %s | %s | %s | %s | %s | %s |" % (
            i, r["id"], r.get("total", 0),
            m.get("face_ratio", "-"), m.get("eye_center_y", "-"),
            m.get("edge_margin", "-"), m.get("sharpness", "-"),
            r.get("thumb", {}).get("score", "-"),
            r.get("verdict", "ERROR"),
            ", ".join(r.get("reasons", [])) or "-"))
    lines += ["", "原因码释义：" + "；".join("%s=%s" % (k, v) for k, v in REASON_LABEL.items()),
              "",
              "> 已紧构图的原图会选中「全幅」候选，不再为凑目标脸占比二次放大裁切；",
              "> 排序以眼位为主（构图 60%、其中眼位 65%），脸占比只作弱约束。",
              "> 风格、情绪、解剖三项无本地模型，记 null，不参与总分。"]
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print("total=%d pass=%d best=%.1f sheet=%s" %
          (len(results), len(passed), ranked[0]["total"] if ranked else 0,
           sheet_path if sheet_items else "-"))
    for r in ranked:
        print("  %-20s %5.1f  %-6s %s" % (r["id"], r.get("total", 0),
                                           r.get("verdict", "ERROR"),
                                           ",".join(r.get("reasons", []))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
