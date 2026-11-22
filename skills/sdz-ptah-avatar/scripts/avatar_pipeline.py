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

VERSION = "1.5.0"

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
MARGIN_BUILD = 0.05                  # 裁切时预留的余量（只影响构建，不作硬门槛）
CHIN_MIN = 0.05
CHIN_BUILD = 0.06

# 满出血构图：不再用「留边距离」一刀切。主体贴边但轮廓闭合则放行；
# 只有贴边处的连续段宽到像被画面切断，且向内仍延续主体色，才判截断。
CUT_RUN_MIN = 0.10                   # 贴边连续主体段 ≥10% 边长才可能是截断
CUT_DEPTH = 0.06                     # 向内检查 6% 处是否仍为主体
CUT_DEPTH_SHARE = 0.55               # 内层同一跨度上主体占比 ≥55% 视为延续
CUT_HARD_EDGES = ("top", "left", "right")   # 底部为胸像正常裁切，只提示不判死
CUT_TOL = 50                         # 与背景色的 RGB 曼哈顿距离阈值

# 边框/装饰圈：大跨度、低填充率的深色连通域贴着四边，判为画框嫌疑
FRAME_DARK = 90
FRAME_SPAN = 0.65
FRAME_FILL_MAX = 0.35
FRAME_NEAR = 0.12
FRAME_SIDES_MIN = 3
# 圆形徽章框：亮底上环绕主体的一圈细暗线。沿径向射线找最暗谷，
# 绝大多数角度都能在贴近画布外缘处找到「暗线 + 两侧亮底」即判为圆环。
# 固定圆心 + 固定半径的旧做法会漏掉偏移或贴边被裁的圆环（实测朝右样本漏检）。
RING_RAYS = 720
RING_R_MIN = 0.40
RING_R_MAX = 0.505
RING_DIP_MAX = 175               # 谷底亮度上限
RING_DIP_DROP = 28               # 谷底要比两侧亮底暗这么多
RING_BASE_MIN = 180              # 两侧底色亮度下限（排除暗背景）
RING_COVER_MIN = 0.55            # 命中射线的角度覆盖率（圆环可能被肩线遮住底部）
RING_GAP_MAX_DEG = 85.0          # 最大连续空档（圆环要连续成圈）
RING_RADIUS_MIN = 0.46           # 环半径中位数下限（徽章圆环贴近画布外缘）
RING_RADIUS_DEV_MAX = 0.03       # 半径离散度上限（同一圈环）
CUT_BG_SHARE_MIN = 0.05     # 背景占比太低说明不是平涂底，截断判定不可靠

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
    "composition_bad:subject_cut": "主体被画面边缘截断",
    "canvas_bad:square": "画布不是方形",
    "canvas_bad:frame": "画面有边框或装饰圈",
    "thumb_unreadable": "缩图不可读",
    "duplicate": "重复图",
    "face_blurry": "面部发糊",
    "style_mismatch": "画风不在同一体系",
    "character_missing": "角色关键特征缺失",
    "angle_mismatch": "角度与目标不符",
}


# ---------------------------------------------------------------- 基础工具

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def resample():
    return getattr(Image, "Resampling", Image).LANCZOS


def is_skin(Y, Cb, Cr):
    return (Y >= SKIN_Y_MIN and SKIN_CR[0] <= Cr <= SKIN_CR[1]
            and SKIN_CB[0] <= Cb <= SKIN_CB[1] and (Cr - Cb) >= SKIN_DELTA)


def _rgb_to_ycbcr(r, g, b):
    Y = 0.299 * r + 0.587 * g + 0.114 * b
    Cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    Cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return Y, Cb, Cr


def _bg_ycbcr(img, ring=0.05, step=None):
    """外圈主色（量化到 16 级）作为背景估计，用于把米白/浅底排除出肤色。"""
    W, H = img.size
    r = max(1, int(min(W, H) * ring))
    step = step or max(2, min(W, H) // 400)
    px = img.convert("RGB").load()
    buckets = {}
    for y in range(0, H, step):
        for x in range(0, W, step):
            if not (x < r or x >= W - r or y < r or y >= H - r):
                continue
            rr, gg, bb = px[x, y]
            key = (rr // 16, gg // 16, bb // 16)
            buckets[key] = buckets.get(key, 0) + 1
    if not buckets:
        return None
    key = max(buckets, key=buckets.get)
    return _rgb_to_ycbcr(key[0] * 16 + 8, key[1] * 16 + 8, key[2] * 16 + 8)


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
    bg = _bg_ycbcr(img)
    cw, ch = w // cell, h // cell
    grid = [[0.0] * cw for _ in range(ch)]
    for gy in range(ch):
        for gx in range(cw):
            hit = tot = 0
            for y in range(gy * cell, (gy + 1) * cell):
                for x in range(gx * cell, (gx + 1) * cell):
                    Y, Cb, Cr = px[x, y]
                    tot += 1
                    if bg is not None and (abs(Y - bg[0]) + abs(Cb - bg[1])
                                           + abs(Cr - bg[2])) <= 45:
                        continue
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
                    # 眼白要接近纯白；米白背景（min≈222、max−min≈28）会被这条挡住
                    if min(r, g, b) >= 195 and max(r, g, b) - min(r, g, b) <= 22:
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


def _profile_geometry(img, cells, scale, eye, eye_h=0.0):
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
    # 眼睛本身是白色，扫描行落在眼白上会被当成背景；
    # 下移到眼白下沿之外（同时保底 10% 肤色高度）再取头宽。
    probe = int(clamp((eye[1] + max(0.10 * skin_h, 0.85 * eye_h)) / sx, 0, h - 1))

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

    geo = _profile_geometry(img, cells, scale, eye["center"],
                            eye.get("size", (0, 0))[1])
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
        pairs = [(a, pa), (b, pb)]
        pairs = [p for p in pairs if p[1]]
        pairs.sort(key=lambda p: p[1][0])          # 按横向排序，便于判断左右眼
        eyes = [p[1] for p in pairs]
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
                                "eye_widths": [p[0]["bbox"][2] * scale for p in pairs],
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


def _edge_cut(region):
    """满出血截断判定：贴边但轮廓闭合的放行，被画面切断的判截断。

    做法：估背景色 → 非背景掩码 → 沿四边找连续主体段。
    只有连续段够宽（≥10% 边长）且向内 6% 处同一跨度仍是主体，才算截断；
    发丝尖、装饰线这种窄接触不算。
    """
    S = 256
    small = region.convert("RGB").resize((S, S), Image.BILINEAR)
    bg = _corner_color(small, patch=0.06)
    px = small.load()
    mask = [[False] * S for _ in range(S)]
    bg_hits = 0
    if bg is not None:
        br, bgg, bb = bg
        for y in range(S):
            row = mask[y]
            for x in range(S):
                r, g, b = px[x, y]
                dist = abs(r - br) + abs(g - bgg) + abs(b - bb)
                if dist <= CUT_TOL:
                    bg_hits += 1
                else:
                    row[x] = True

    def flags(name):
        if name == "top":
            return [mask[0][x] for x in range(S)]
        if name == "bottom":
            return [mask[S - 1][x] for x in range(S)]
        if name == "left":
            return [mask[y][0] for y in range(S)]
        return [mask[y][S - 1] for y in range(S)]

    def inner(name, a, b, d):
        if name == "top":
            return [mask[d][x] for x in range(a, b)]
        if name == "bottom":
            return [mask[S - 1 - d][x] for x in range(a, b)]
        if name == "left":
            return [mask[y][d] for y in range(a, b)]
        return [mask[y][S - 1 - d] for y in range(a, b)]

    out = {}
    cut_edges = []
    for name in ("top", "bottom", "left", "right"):
        f = flags(name)
        contact = sum(f) / float(S)
        runs, start = [], None
        for i, v in enumerate(f + [False]):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if i - start >= 2:
                    runs.append((start, i))
                start = None
        max_run = max([(b - a) for a, b in runs] or [0]) / float(S)
        cut = False
        if max_run >= CUT_RUN_MIN:
            d = max(2, int(S * CUT_DEPTH))
            for a, b in runs:
                if (b - a) / float(S) < CUT_RUN_MIN:
                    continue
                vals = inner(name, a, b, d)
                if vals and sum(vals) / float(len(vals)) >= CUT_DEPTH_SHARE:
                    cut = True
                    break
        out[name] = {"contact": round(contact, 3), "max_run": round(max_run, 3),
                     "cut": cut}
        if cut:
            cut_edges.append(name)
    out["cut_edges"] = cut_edges
    bg_share = bg_hits / float(S * S) if bg is not None else 0.0
    out["bg_share"] = round(bg_share, 3)
    out["reliable"] = bg_share >= CUT_BG_SHARE_MIN
    if not out["reliable"]:
        # 背景估不出/不是平涂底时，四边都会被误当主体；截断判定整闸跳过，
        # 连同逐边 cut 标记一起清空，避免 score_crop 仍按不可靠结果判截断。
        for name in ("top", "bottom", "left", "right"):
            out[name]["cut"] = False
        out["cut_edges"] = []
    return out


def _circular_frame(g):
    """圆形徽章框粗检：亮底上环绕主体的一圈细暗线。

    允许圆环相对画布中心偏移、或贴边被裁掉一小段：逐条径向射线在
    [RING_R_MIN, RING_R_MAX]·S 内取最暗点，要求它明显暗于两侧亮底；
    再要求命中角度的覆盖率、半径离散度与连续空档都达标。
    实心暗背景不会命中（两侧底色达不到 RING_BASE_MIN）。
    """
    S = g.size[0]
    px = g.load()
    r0, r1 = int(RING_R_MIN * S), int(RING_R_MAX * S)
    hits = []
    for k in range(RING_RAYS):
        a = 2 * math.pi * k / RING_RAYS
        ca, sa = math.cos(a), math.sin(a)
        vals, radii = [], []
        for r in range(r0, r1 + 1):
            x = int(round(S / 2 + r * ca))
            y = int(round(S / 2 + r * sa))
            if 0 <= x < S and 0 <= y < S:
                vals.append(px[x, y])
                radii.append(r)
        if len(vals) < 10:
            continue
        mi = min(range(len(vals)), key=lambda i: vals[i])
        lo, hi = max(0, mi - 8), min(len(vals), mi + 9)
        neigh = [vals[i] for i in range(lo, hi) if abs(i - mi) >= 4]
        if not neigh:
            continue
        base = sum(neigh) / float(len(neigh))
        if (vals[mi] <= RING_DIP_MAX and vals[mi] <= base - RING_DIP_DROP
                and base >= RING_BASE_MIN):
            hits.append((k, radii[mi] / float(S), vals[mi], base))
    if not hits:
        return None
    frac = len(hits) / float(RING_RAYS)
    ks = sorted(h[0] for h in hits)
    gaps = [ks[i + 1] - ks[i] for i in range(len(ks) - 1)]
    gaps.append(ks[0] + RING_RAYS - ks[-1])
    max_gap_deg = max(gaps) * 360.0 / RING_RAYS
    rs = sorted(h[1] for h in hits)
    med = rs[len(rs) // 2]
    dev = sum(abs(r - med) for r in rs) / float(len(rs))
    luma = sorted(h[2] for h in hits)[len(hits) // 2]
    base = sorted(h[3] for h in hits)[len(hits) // 2]
    if not (frac >= RING_COVER_MIN and max_gap_deg <= RING_GAP_MAX_DEG
            and med >= RING_RADIUS_MIN and dev <= RING_RADIUS_DEV_MAX):
        return None
    return {"kind": "circular", "coverage": round(frac, 3),
            "radius": round(med, 3), "radius_dev": round(dev, 3),
            "max_gap_deg": round(max_gap_deg, 1),
            "ring_luma": round(luma, 1), "base_luma": round(base, 1)}


def _frame_metrics(img):
    """边框/装饰圈粗检：圆形徽章环，或大跨度、低填充率的深色连通域贴三边以上。"""
    R = 512
    ring = _circular_frame(img.convert("L").resize((R, R), Image.BILINEAR))
    if ring:
        return True, ring
    S = 256
    g = img.convert("L").resize((S, S), Image.BILINEAR)
    px = g.load()
    marked = [[px[x, y] < FRAME_DARK for x in range(S)] for y in range(S)]
    for cells in _grid_components(marked, S, S):
        if len(cells) < 0.01 * S * S:
            continue
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if bw < FRAME_SPAN * S or bh < FRAME_SPAN * S:
            continue
        fill = len(cells) / float(bw * bh)
        if fill > FRAME_FILL_MAX:
            continue
        near = ((x0 <= FRAME_NEAR * S) + (x1 >= (1 - FRAME_NEAR) * S)
                + (y0 <= FRAME_NEAR * S) + (y1 >= (1 - FRAME_NEAR) * S))
        if near >= FRAME_SIDES_MIN:
            return True, {"kind": "component",
                          "x": [round(x0 / float(S), 3), round(x1 / float(S), 3)],
                          "y": [round(y0 / float(S), 3), round(y1 / float(S), 3)],
                          "fill": round(fill, 3)}
    return False, None


def canvas_metrics(img):
    """方形 + 无边框粗检。裁切后画布本就方形，这里仍显式记录，防上游换图。"""
    w, h = img.size
    frame_like, bbox = _frame_metrics(img)
    return {"width": w, "height": h, "aspect": round(w / float(h), 3),
            "square": abs(w - h) <= 2, "frame_like": frame_like, "frame_bbox": bbox}


def check_canvas(metrics):
    checks = {"square": bool(metrics.get("square")),
              "no_frame": not metrics.get("frame_like")}
    reasons = []
    if not checks["square"]:
        reasons.append("canvas_bad:square")
    if not checks["no_frame"]:
        reasons.append("canvas_bad:frame")
    return {"passed": not reasons, "checks": checks, "reasons": reasons}


def score_crop(img, crop, face):
    left, top, right, bottom = crop["box"]
    s = right - left
    fx, fy, fw, fh = face["box"]
    eye_y = face["eye_y"]
    face_ratio = (fw * fh) / (0.785 * s * s)
    eye_center_y = (eye_y - top) / s
    margins = [fx - left, right - (fx + fw), fy - top, bottom - (fy + fh)]
    margin = min(margins) / s
    region = img.crop((int(round(left)), int(round(top)),
                       int(round(left + s)), int(round(top + s))))
    edge = _edge_cut(region)
    cut_hard = [e for e in CUT_HARD_EDGES if edge.get(e, {}).get("cut")]

    ratio_score = clamp(1 - abs(face_ratio - RATIO_TARGET) / RATIO_TOL, 0, 1)
    eye_target = PROFILE_EYE_TARGET if face.get("profile") else EYE_TARGET
    eye_tol = PROFILE_EYE_TOL if face.get("profile") else EYE_TOL
    eye_score = clamp(1 - abs(eye_center_y - eye_target) / eye_tol, 0, 1)
    # 满出血模式下不再奖励「离边远」：主体完整就得满分，被截断直接 0。
    integrity_score = 0.0 if cut_hard else 1.0
    cost = crop.get("cost", 0.0)
    composition = 100 * (W_RATIO * ratio_score + W_EYE * eye_score + W_MARGIN * integrity_score)
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
    if cut_hard:
        reasons.append("composition_bad:subject_cut")
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
            "cut_edges": cut_hard,
            "edge_contact": {k: edge[k]["contact"] for k in ("top", "bottom", "left", "right")},
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


# ---------------------------------------------------------------- 风格 / 角度 / 角色闸门

def load_json(path):
    """读 JSON；失败返回 None，调用方据此把对应闸门记为 null。"""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


def _corner_color(img, patch=0.04):
    """四角 patch 的中位色，作为平涂背景估计；估不出时返回 None。"""
    w, h = img.size
    px = img.convert("RGB").load()
    n = max(4, int(min(w, h) * patch))
    corners = []
    for x0, y0 in ((2, 2), (w - n - 2, 2), (2, h - n - 2), (w - n - 2, h - n - 2)):
        rs, gs, bs = [], [], []
        for y in range(y0, min(h, y0 + n)):
            for x in range(x0, min(w, x0 + n)):
                r, g, b = px[x, y]
                rs.append(r)
                gs.append(g)
                bs.append(b)
        if rs:
            rs.sort(); gs.sort(); bs.sort()
            corners.append((rs[len(rs) // 2], gs[len(gs) // 2], bs[len(bs) // 2]))
    if not corners:
        return None
    br = sum(c[0] for c in corners) / float(len(corners))
    bgc = sum(c[1] for c in corners) / float(len(corners))
    bb = sum(c[2] for c in corners) / float(len(corners))
    return br, bgc, bb


def _background_stats(img, tol=50):
    """用四角中位数估计背景色，再统计接近该色的像素占比与明度波动。

    比「外圈 stddev」稳：紧构图头像的边圈常是头发/衣服，外圈法会把角色误当背景。
    """
    w, h = img.size
    px = img.convert("RGB").load()
    bg = _corner_color(img)
    if bg is None:
        return 0.0, 0.0, "busy"
    br, bgc, bb = bg

    step = max(1, min(w, h) // 200)
    total = hit = 0
    lum, lum2 = 0.0, 0.0
    for y in range(0, h, step):
        for x in range(0, w, step):
            r, g, b = px[x, y]
            total += 1
            if abs(r - br) + abs(g - bgc) + abs(b - bb) <= tol:
                hit += 1
                L = 0.299 * r + 0.587 * g + 0.114 * b
                lum += L
                lum2 += L * L
    if not total or not hit:
        return 0.0, 0.0, "busy"
    share = hit / float(total)
    mean = lum / hit
    std = max(0.0, lum2 / hit - mean * mean) ** 0.5
    if share >= 0.20 and std <= 10:
        mode = "flat"
    elif share >= 0.10 and std <= 18:
        mode = "simple"
    else:
        mode = "busy"
    return share, std, mode


def style_metrics(img):
    """赛璐璐粗指标：主色板数量、饱和度、边缘密度、背景平涂度。

    只做「是否明显异体系」的粗筛，不是风格 embedding，不能证明画风精细一致。
    """
    w, h = img.size
    total = float(w * h)
    q = img.convert("RGB").quantize(colors=12, method=Image.MEDIANCUT)
    counts = q.getcolors(w * h) or []
    pal = q.getpalette() or []
    shares = sorted(((c / total, idx) for c, idx in counts), reverse=True)
    # 12 色量化 + 占比 ≥3.5% 计为主色：参考图实测 7–12，纯平涂头像约 6–9
    main_colors, top = 0, []
    for share, idx in shares:
        if share >= 0.035:
            main_colors += 1
        if len(top) < 6:
            r, g, b = pal[idx * 3:idx * 3 + 3]
            top.append({"hex": "#%02x%02x%02x" % (r, g, b), "share": round(share, 3)})

    hsv = img.convert("HSV")
    step = max(1, min(w, h) // 160)
    px = hsv.load()
    s_sum = s_cnt = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            _, s, v = px[x, y]
            if v >= 50:
                s_sum += s
                s_cnt += 1
    saturation = round(s_sum / (255.0 * s_cnt), 3) if s_cnt else 0.0

    g = img.convert("L").resize((512, 512), Image.BILINEAR)
    edge = g.filter(ImageFilter.FIND_EDGES)
    ep = edge.load()
    hit = 0
    for y in range(512):
        for x in range(512):
            if ep[x, y] >= 40:
                hit += 1
    edge_density = round(hit / (512.0 * 512.0), 3)

    bg_share, bg_std, bg_mode = _background_stats(img)
    return {
        "palette_colors": main_colors,
        "palette_top": top,
        "saturation": saturation,
        "edge_density": edge_density,
        "background_share": round(bg_share, 3),
        "background_stddev": round(bg_std, 1),
        "background_mode": bg_mode,
    }


def check_style(metrics, profile):
    """按风格档案 gate 判定；没有档案时返回 None（不冒充通过）。"""
    if not profile:
        return None
    gate = profile.get("gate") or {}
    checks = {}
    lo, hi = gate.get("palette_colors", [0, 999])
    checks["palette_colors"] = lo <= metrics["palette_colors"] <= hi
    lo, hi = gate.get("saturation", [0.0, 1.0])
    checks["saturation"] = lo <= metrics["saturation"] <= hi
    lo, hi = gate.get("edge_density", [0.0, 1.0])
    checks["edge_density"] = lo <= metrics["edge_density"] <= hi
    checks["background"] = (metrics["background_stddev"] <= gate.get("background_stddev_max", 999)
                            and metrics["background_mode"] in gate.get("background_modes",
                                                                       ["flat", "simple", "busy"]))
    reasons = ["style_mismatch:" + k for k, ok in checks.items() if not ok]
    return {"style_id": profile.get("style_id"), "passed": not reasons,
            "checks": checks, "reasons": reasons}


def resolve_angle(name, matrix, fallback):
    """订单期望角度优先（brief.angle 或 --expected-angle）；都没有时才按文件名前缀推断。

    确认过的朝向是订单级属性，文件名只是标识：文件标签与期望角度冲突时，
    按期望角度判，避免「brief 说朝左、文件却命名朝右」被静默放行。
    """
    if fallback:
        return fallback
    ids = sorted([a["id"] for a in (matrix or {}).get("angles", [])],
                 key=len, reverse=True)
    for aid in ids:
        if name.startswith(aid):
            return aid
    return fallback


def _skin_centroid(img, box):
    """头部框内肤色重心的横向占比，用来辅助判断鼻尖朝向。

    侧脸朝左时脸/鼻在头框左半，后脑与头发在右半；朝右相反。
    与瞳孔位置交叉校验，避免把方向写反。
    """
    W, H = img.size
    x, y, w, h = [float(v) for v in box]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(W, int(x + w)), min(H, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = img.crop((x0, y0, x1, y1)).convert("YCbCr")
    cw = 200
    ch = max(1, int(round(crop.height * cw / float(crop.width))))
    crop = crop.resize((cw, ch), Image.BILINEAR)
    px = crop.load()
    bg = _bg_ycbcr(img)
    sx = cnt = tot = 0
    for yy in range(0, ch, 2):
        for xx in range(0, cw, 2):
            Y, Cb, Cr = px[xx, yy]
            tot += 1
            if bg is not None and (abs(Y - bg[0]) + abs(Cb - bg[1])
                                   + abs(Cr - bg[2])) <= 45:
                continue
            if is_skin(Y, Cb, Cr):
                sx += xx
                cnt += 1
    if not tot or cnt < max(20, 0.01 * tot):
        return None
    return round(sx / float(cnt * cw), 3)


def detect_angle(img, face, metrics):
    """判侧脸朝向：单眼 + 瞳孔横向位置 + 鼻尖侧（左/右双向）。

    不做正脸 / 3-4 分支；方向判不出时 facing=None，由 check_angle 判
    angle_undetermined。
    """
    eyes = face.get("eyes") or []
    visible = 2 if len(eyes) >= 2 else (1 if (face.get("profile") or len(eyes) == 1) else None)
    pupil_edge = None
    if len(eyes) == 1:
        fx, fy, fw, fh = face["box"]
        pupil_edge = round((eyes[0][0] - fx) / float(max(1.0, fw)), 3)

    skin_cx = _skin_centroid(img, face["box"]) if face.get("profile") else None
    nose_side = None
    if skin_cx is not None:
        if skin_cx <= 0.44:
            nose_side = "left"
        elif skin_cx >= 0.56:
            nose_side = "right"

    facing = None
    confidence = "low"
    if pupil_edge is not None:
        pupil_side = "left" if pupil_edge < 0.5 else "right"
        if nose_side is None or nose_side == pupil_side:
            facing = pupil_side
            confidence = "medium" if nose_side is None else "high"
    elif nose_side is not None:
        facing = nose_side
        confidence = "medium"

    return {
        "visible_eyes": visible,
        "pupil_edge": pupil_edge,
        "skin_centroid": skin_cx,
        "nose_side": nose_side,
        "facing": facing,
        "is_profile": bool(visible == 1),
        "eye_center_y": metrics.get("eye_center_y"),
        "confidence": confidence,
    }


def check_angle(expected_id, detected, metrics, matrix):
    """校验侧脸朝向（profile_left / profile_right 双向）。

    未启用角度直接 FAIL；方向判不出（瞳孔或鼻尖侧缺失/冲突）返回
    angle_undetermined，交人工确认，不自动猜测。
    """
    if not expected_id:
        return None
    angle = None
    for a in (matrix or {}).get("angles", []):
        if a["id"] == expected_id:
            angle = a
            break
    if angle is None or not angle.get("enabled", False):
        return {"expected": expected_id, "enabled": False, "passed": False,
                "detected": detected, "checks": {},
                "reason": "angle_mismatch",
                "note": "该角度未启用，禁止产出"}

    checks = {"single_eye": detected["visible_eyes"] == 1,
              "is_profile": detected["is_profile"]}
    lo, hi = angle.get("pupil_edge", [0.0, 1.0])
    pe = detected.get("pupil_edge")
    checks["pupil_in_range"] = None if pe is None else (lo <= pe <= hi)
    facing = detected.get("facing")
    checks["facing"] = None if facing is None else (facing == angle.get("yaw"))
    want_nose = angle.get("nose_side")
    nose = detected.get("nose_side")
    checks["nose_side"] = None if (not want_nose or nose is None) else (nose == want_nose)
    ey = metrics.get("eye_center_y")
    if ey is not None:
        elo, ehi = angle.get("eye_y", [0.0, 1.0])
        checks["eye_y"] = elo <= ey <= ehi
    # 方向必须能判出来：瞳孔/鼻尖侧缺失或冲突时不算通过
    passed = all(v is True for v in checks.values())
    direction_known = (pe is not None and facing in ("left", "right")
                       and nose in ("left", "right"))
    if passed:
        reason = None
    elif not direction_known:
        reason = "angle_undetermined"
    else:
        reason = "angle_mismatch"
    return {"expected": expected_id, "label": angle.get("label"),
            "enabled": True, "detected": detected, "passed": passed,
            "checks": checks, "reason": reason,
            "direction_known": direction_known,
            "side_check_note": "双向判据：朝左 pupil_edge ≤0.42 + 鼻尖侧左；"
                               "朝右 pupil_edge ≥0.58 + 鼻尖侧右；"
                               "实测朝左 0.178、朝右 0.789"} 


GENDERS = ("male", "female", "unspecified")
ANGLE_IDS = ("profile_left", "profile_right")
DEFAULT_ANGLE_BY_GENDER = {"male": "profile_left", "female": "profile_right",
                           "unspecified": "profile_left"}


def normalize_brief(brief, card, budget):
    """补默认值并校验 brief；返回 (normalized, errors)。

    默认：画质 1024 / 张数 1 / 预算 6；朝向按性别（男左、女右，未指定默认左），
    用户显式 angle 覆盖。未指定性别时 needs_gender_confirmation=True。
    """
    errors = []
    if not brief:
        return None, errors
    out = dict(brief)
    card = card or {}
    if out.get("gender"):
        gender = out["gender"]
        gender_source = out.get("gender_source") or "user"
    elif card.get("gender"):
        gender = card["gender"]
        gender_source = "card"
    else:
        gender = "unspecified"
        gender_source = "unspecified"
    if gender not in GENDERS:
        errors.append("gender 必须是 %s" % "/".join(GENDERS))
    out["gender"] = gender
    out["gender_source"] = gender_source

    explicit_angle = out.get("angle")
    if explicit_angle:
        angle = explicit_angle
        angle_source = out.get("angle_source") or "user"
    else:
        angle = DEFAULT_ANGLE_BY_GENDER.get(gender, "profile_left")
        angle_source = "default"
    if angle not in ANGLE_IDS:
        errors.append("angle 必须是 %s" % "/".join(ANGLE_IDS))
    out["angle"] = angle
    out["angle_source"] = angle_source
    out["needs_gender_confirmation"] = (gender == "unspecified")

    out["size"] = str(out.get("size") or "1024")
    out["formats"] = out.get("formats") or [out["size"]]
    try:
        count = int(out.get("count")) if out.get("count") is not None else 1
    except (TypeError, ValueError):
        count = 1
    count = max(1, count)
    limit = max(1, int(budget))
    out["count_requested"] = count
    out["count"] = min(count, limit)
    out["count_capped"] = out["count"] < out["count_requested"]
    out["budget"] = limit
    return out, errors


def check_confirmation(brief):
    """brief 存在时，confirm.confirmed 必须为 True；否则拒绝执行。"""
    if brief is None:
        return None
    c = brief.get("confirm") or {}
    return {"confirmed": c.get("confirmed") is True,
            "at": c.get("at"), "source": c.get("source"),
            "text": c.get("text"), "params_hash": c.get("params_hash")}


def check_character(checklist, card):
    """人工核对清单：必填项未勾选即不得放行；未提供清单记 null。"""
    if not checklist or not card:
        return None
    items = checklist.get("items") or {}
    results, missing = [], []
    for item in card.get("checklist", []):
        key = item.get("key")
        ok = bool((items.get(key) or {}).get("ok"))
        results.append({"key": key, "label": item.get("label"),
                        "required": bool(item.get("required")), "ok": ok,
                        "note": (items.get(key) or {}).get("note")})
        if item.get("required") and not ok:
            missing.append(key)
    return {"character_id": card.get("character_id"), "passed": not missing,
            "items": results, "missing": missing,
            "reasons": ["character_missing:" + k for k in missing]}


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
    ap.add_argument("--style", default=None, help="风格档案 JSON（styles/<style-id>.json）")
    ap.add_argument("--character", default=None, help="角色卡 JSON（characters/<character-id>.json）")
    ap.add_argument("--angles", default=None, help="角度矩阵 JSON（默认 references/angles.json）")
    ap.add_argument("--checklist", default=None, help="人工角色核对清单 JSON（缺省则闸门为 null）")
    ap.add_argument("--expected-angle", default=None, help="文件名无角度前缀时的期望角度 id")
    ap.add_argument("--budget", type=int, default=6,
                    help="本单生成预算（默认 6，保守模式；量产需显式调大）")
    ap.add_argument("--allow-over-budget", action="store_true",
                    help="超预算仍继续（必须先取得用户确认）")
    args = ap.parse_args()

    raw_dir, out_dir = args.raw, args.out
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    for f in formats:
        if f not in SIZE_TIERS:
            print("unknown format tier: %s" % f)
            return 2

    # 确认门（硬性）：带 brief 时必须有 confirm.confirmed=true，否则拒绝执行
    brief_path = args.brief
    if brief_path and not os.path.isabs(brief_path):
        brief_path = os.path.join(raw_dir, brief_path)
    brief = load_json(brief_path)
    confirm = check_confirmation(brief)
    if confirm is not None and not confirm["confirmed"]:
        print("order not confirmed: brief 缺少 confirm.confirmed=true。"
              "请先运行 scripts/confirm_order.py --brief <brief> "
              "--confirm \"确认生成\" [--source card]；未确认不得评分/导出，更不得生成。")
        return 4
    if brief and brief.get("budget") is not None:
        try:
            args.budget = min(int(brief["budget"]), args.budget)
        except (TypeError, ValueError):
            pass

    for sub in ("crop", "native", "preview"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    files = [f for f in sorted(os.listdir(raw_dir))
             if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    if not files:
        print("no images in %s" % raw_dir)
        return 2

    used = len(files)
    budget = {"limit": args.budget, "used": used, "remaining": max(0, args.budget - used),
              "exceeded": used > args.budget, "source": "raw_count"}
    with open(os.path.join(out_dir, "budget.json"), "w", encoding="utf-8") as fh:
        json.dump(budget, fh, ensure_ascii=False, indent=2)
    if budget["exceeded"] and not args.allow_over_budget:
        print("budget exceeded: raw=%d budget=%d，已停止评分。"
              "请确认是否继续，确认后加 --allow-over-budget。" % (used, args.budget))
        return 3

    profile = load_json(args.style)
    card = load_json(args.character)
    angle_matrix = load_json(args.angles)
    checklist = load_json(args.checklist)
    char_gate = check_character(checklist, card)
    norm, brief_errors = normalize_brief(brief, card, args.budget)
    if brief_errors:
        print("brief invalid: " + "; ".join(brief_errors))
        return 4
    expected_default = args.expected_angle or (norm or {}).get("angle")

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

        smetrics = style_metrics(master)
        style_gate = check_style(smetrics, profile)
        cmetrics = canvas_metrics(master)
        canvas_gate = check_canvas(cmetrics)
        expected_angle = resolve_angle(name, angle_matrix, expected_default)
        detected_angle = detect_angle(img, face, best["metrics"])
        angle_gate = check_angle(expected_angle, detected_angle, best["metrics"], angle_matrix)

        tid, tedge, tcontrast = thumb_readability(master)
        reasons = list(best["reasons"])
        if tid < THUMB_MIN:
            reasons.append("thumb_unreadable")
        if face.get("candidates", 1) > 1:
            reasons.append("multi_face")
        if face["conf"] < CONF_MIN:
            reasons.append("low_conf")
        if style_gate and not style_gate["passed"]:
            reasons.extend(style_gate["reasons"])
        if char_gate and not char_gate["passed"]:
            reasons.extend(char_gate["reasons"])
        if not canvas_gate["passed"]:
            reasons.extend(canvas_gate["reasons"])
        if angle_gate and not angle_gate["passed"]:
            reasons.append(angle_gate["reason"])
        verdict = "PASS" if not reasons else "REJECT"

        # 方形预览（不再生成圆形遮罩产物）
        prev = master.resize((256, 256), resample())
        prev_path = os.path.join(out_dir, "preview", item_id + "_preview.png")
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
                "style": None if style_gate is None else style_gate["passed"],
                "character": None if char_gate is None else char_gate["passed"],
                "canvas": canvas_gate["passed"],
                "angle": None if angle_gate is None else angle_gate["passed"],
                "anatomy": None,
            },
            "style": smetrics,
            "style_check": style_gate,
            "canvas": cmetrics,
            "canvas_check": canvas_gate,
            "angle_check": angle_gate,
            "character_check": char_gate,
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
            "budget": budget,
            "style_profile": {"style_id": (profile or {}).get("style_id"),
                              "path": args.style},
            "character": {"character_id": (card or {}).get("character_id"),
                          "path": args.character},
            "angle_matrix": {"path": args.angles,
                             "angles": [a["id"] for a in (angle_matrix or {}).get("angles", [])]},
            "checklist": {"path": args.checklist, "provided": checklist is not None},
            "order": {
                "confirmed": bool(confirm and confirm["confirmed"]),
                "confirmation_source": (confirm or {}).get("source"),
                "gender": (norm or {}).get("gender"),
                "gender_source": (norm or {}).get("gender_source"),
                "angle": (norm or {}).get("angle"),
                "angle_source": (norm or {}).get("angle_source"),
                "needs_gender_confirmation": bool((norm or {}).get("needs_gender_confirmation")),
                "count": (norm or {}).get("count"),
                "count_capped": bool((norm or {}).get("count_capped")),
            },
        },
        "brief": brief,
        "brief_effective": norm,
        "capability": {
            "available": ["face_locate_eye_anchor", "face_locate_skin_fallback",
                          "face_locate_profile_single_eye", "crop_compensation",
                          "sharpness", "thumb_readability", "dedupe",
                          "style_coarse_metrics", "angle_coarse_check",
                          "bidirectional_angle_check", "confirm_gate",
                          "subject_cut_check", "canvas_frame_check",
                          "character_checklist_gate"],
            "unavailable": ["ocr_text", "nsfw", "emotion", "style_embedding", "anatomy",
                            "real_esrgan_superres"],
            "note": "风格闸门是粗指标（主色数/饱和度/边缘密度/背景平涂度），只能拦明显异体系，"
                    "不等于风格 embedding；截断与边框闸门也是像素启发式，不是语义分割；"
                    "角色闸门依赖人工清单；无 OCR/NSFW/情绪/解剖/超分模型，"
                    "对应闸门记为 null。分数按可用维度(face_quality 40 + composition 60)归一化",
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
    if norm or brief:
        lines += ["| 项 | 值 |", "|-|-|"]
        for k, v in (norm or brief).items():
            if isinstance(v, (str, int, float)):
                lines.append("| %s | %s |" % (k, v))
        lines.append("")
    lines += ["共 %d 张，通过 %d 张，驳回 %d 张；最高分 %.1f" %
              (len(results), len(passed), len(results) - len(passed),
               ranked[0]["total"] if ranked else 0),
              "预算 %d 张，实际 raw %d 张，剩余 %d 张%s" %
              (budget["limit"], budget["used"], budget["remaining"],
               "（已超预算，经确认继续）" if budget["exceeded"] else ""), "",
              "| # | 图 | 角度 | 总分 | 脸占比 | 眼睛位置 | 截断 | 画布 | 风格 | 角色 | 角度判定 | 结论 | 原因 |",
              "|-|-|-|-|-|-|-|-|-|-|-|-|-|"]
    for i, r in enumerate(ranked, 1):
        m = r.get("metrics", {})
        ac = r.get("angle_check") or {}
        det = ac.get("detected") or {}
        if det:
            face_txt = {"left": "朝左", "right": "朝右"}.get(det.get("facing"), "方向未定")
            angle_txt = "%s/%s眼" % (face_txt, det.get("visible_eyes", "-"))
        else:
            angle_txt = "-"
        style_ok = r.get("gates", {}).get("style")
        char_ok = r.get("gates", {}).get("character")
        canvas_ok = r.get("gates", {}).get("canvas")
        cut_edges = m.get("cut_edges") or []
        lines.append("| %d | %s | %s | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, r["id"], ac.get("expected", "-"), r.get("total", 0),
            m.get("face_ratio", "-"), m.get("eye_center_y", "-"),
            "无" if not cut_edges else ",".join(cut_edges),
            "-" if canvas_ok is None else ("PASS" if canvas_ok else "FAIL"),
            "-" if style_ok is None else ("PASS" if style_ok else "FAIL"),
            "-" if char_ok is None else ("PASS" if char_ok else "FAIL"),
            angle_txt, r.get("verdict", "ERROR"),
            ", ".join(r.get("reasons", [])) or "-"))
    lines += ["", "| 图 | 主色数 | 饱和度 | 边缘密度 | 背景占比 | 背景 stddev | 背景 |",
              "|-|-|-|-|-|-|-|"]
    for r in ranked:
        s = r.get("style") or {}
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], s.get("palette_colors", "-"), s.get("saturation", "-"),
            s.get("edge_density", "-"), s.get("background_share", "-"),
            s.get("background_stddev", "-"), s.get("background_mode", "-")))
    lines += ["", "原因码释义：" + "；".join("%s=%s" % (k, v) for k, v in REASON_LABEL.items()),
              "",
              "> 已紧构图的原图会选中「全幅」候选，不再为凑目标脸占比二次放大裁切；",
              "> 排序以眼位为主（构图 60%、其中眼位 65%），脸占比只作弱约束；",
              "> 满出血模式下不再按留边距离一刀切：贴边但主体完整放行，只有贴边连续段过宽且向内延续才判截断；",
              "> 画布闸门检查方形与边框/装饰圈；带圆环的画框会记 canvas_bad:frame；",
              "> 风格闸门是粗指标，只拦明显异体系；角色闸门依赖人工清单，缺必填项不放行；",
              "> 角度判定覆盖侧脸双向：朝左 pupil_edge ≤0.42 + 鼻尖侧左；朝右 ≥0.58 + 鼻尖侧右；"
              "方向判不出记 angle_undetermined，交人工确认；",
              "> 默认朝向按性别：男左、女右、未指定默认左；用户可覆盖；带 brief 时必须有 confirm.confirmed，否则拒绝执行；",
              "> 单眼判定仍可能把远眼未检出的 3/4 脸当成侧脸，800px 人工复查不可省。",
              "> 情绪、解剖、风格 embedding 无本地模型，记 null，不参与总分。"]
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
