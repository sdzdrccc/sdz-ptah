#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动漫头像流水线：人脸定位 -> 裁切补偿 -> 裁切后评分 -> 硬门槛 -> 多格式导出。

只用 Pillow（本机已装），不依赖 numpy / opencv / 任何模型。
规则、阈值、原因码与放大说明见 ../references/protocol.md。

用法：
  py -3 avatar_pipeline.py --raw <订单目录> [--prefix satoshi] [--order-id <订单id>] \
        [--brief brief.json] [--formats 1024,2k,4k] [--jpg] [--jpg-quality 88]
  旧订单复跑：--raw <订单>/raw --out <订单>/out --layout legacy
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import deque
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageStat

VERSION = "1.11.1"

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

# 内容框（非背景主体）兜底：只对可判定的平涂背景生效。
# 极狐那一单的问题不是「脸占比」，而是裁切把耳尖切平、上下各留 26% 白边，
# 因此除了人脸锚点，还必须用内容最高点约束裁切，并给成品加留白/主体占比闸门。
CONTENT_WORK = 320
CONTENT_TOL = 40                 # 与四角背景色的 RGB 曼哈顿距离
CONTENT_FLAT_STD = 6.0           # 四角补丁标准差 ≤ 该值视为平涂背景
CONTENT_BG_SHARE_MIN = 0.10      # 背景占比下限，太低说明不是平涂底/主体铺满
CONTENT_SHARE_MIN = 0.05         # 内容框面积占比下限，低于此视为空图
HEAD_SAFE_MARGIN = 0.06          # 裁切时头顶到画布上沿的安全边距
TOP_BLANK_MIN = 0.015            # 成品上留白下限（原图有空间时才检查）
TOP_BLANK_MAX = 0.16             # 成品上留白上限
BOTTOM_BLANK_MAX = 0.18          # 成品下留白上限（胸像可贴底）
SUBJECT_SHARE_MIN = 0.40         # 内容框面积 / 画布面积 下限
# 满出血头像的主体可以几乎铺满画布（实测 0.953 是正常紧构图，不是缺陷）。
# 上限只用来拦「整张都是主体、找不到背景」的失控图，放到 0.985。
SUBJECT_SHARE_MAX = 0.985
HEAD_CUT_RUN_RATIO = 0.22        # 内容最高行最长连续段 / 内容宽，超过视为平切头
# 无脸/皮套模式：不用眼部锚点，改用头部/头盔整体轮廓。
# 比例阈值以极狐 6 张（1024²，头宽约 0.30–0.32 画面宽）校准：
# 目标让头宽约占成品 0.50，头框面积占比约 0.35。
HEAD_RATIO_MIN, HEAD_RATIO_MAX = 0.18, 0.62
HEAD_TARGET = 0.35
HEAD_WIDTH_TARGET = 0.50
CENTER_OFFSET_MAX = 0.10
FACELESS_SAT_MIN = 20.0          # 面罩/高光饱和像素权重下限，低于此视为无信号
# 角度：实测极狐面罩/高光横向偏移 朝左 -0.22~-0.24、朝右 +0.22~+0.27、正面 +0.02
VISOR_SIDE_MIN = 0.08
VISOR_FRONT_MAX = 0.06
CONTOUR_SIDE_MIN = 0.05
FRONT_EYE_Y = [0.24, 0.38]       # 正脸眼位：verify/raw 8 张实测 0.303–0.316
FRONT_EYE_TILT_MAX = 0.07
FRONT_EYE_BALANCE_MAX = 0.08
FACE_RATIO_MIN, FACE_RATIO_MAX = 0.34, 0.82
EYE_MIN, EYE_MAX = 0.12, 0.36
# 侧脸单眼走「头部包围盒」定位，箱体含头发/帽子，眼位天然比正脸低。
# 以两张人工认可的正样本校准：眼位 0.391 / 0.409，共同区间取 0.32~0.46，
# 目标 0.40。旧档 0.28~0.50 过宽、目标 0.44 偏下，会把「眼位偏低」的图放过。
PROFILE_EYE_MIN, PROFILE_EYE_MAX = 0.32, 0.46
PROFILE_EYE_TARGET = 0.40
# 侧脸方向判定带：瞳孔落在左带/右带时方向已经确定，不再要求鼻尖侧
# （肤色重心落中线是常见情况，旧逻辑会把它误判成 angle_undetermined）。
PROFILE_LEFT_MAX = 0.42
PROFILE_RIGHT_MIN = 0.58
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
    "composition_bad:head_cut": "头顶被裁平（内容最高行连续段过宽）",
    "composition_bad:whitespace": "上下留白越界",
    "composition_bad:subject_share": "主体占画面比例越界",
    "composition_bad:head_ratio": "头部轮廓占比越界",
    "composition_bad:centering": "头部居中偏移越界",
    "locate_failed": "定位失败（非构图缺陷）",
    "reference_unsupported": "当前后端不支持参考图入参",
    "reference_style_low": "成品与参考图风格相似度过低",
    "style_unverified": "风格维度未验证（无档案或声明不适用），不计为通过",
    "style_not_applicable": "风格档案声明该维度不适用（记 null，不计为通过）",
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


def _content_analysis(img, work=CONTENT_WORK, tol=CONTENT_TOL):
    """非背景内容框：四角背景色 + 泛洪，返回留白、占比与顶部平切信号。

    只对平涂背景可靠（`reliable`）；非平涂底返回 applied=False，
    闸门记 null，不把「算不出」当成通过。背景用泛洪而不是全局色距，
    避免把被装甲包住的白色高光误判成背景。
    """
    W, H = img.size
    w = min(work, W)
    h = max(1, int(round(H * w / float(W))))
    if w < 8 or h < 8:
        return {"applied": False, "reliable": False, "empty": True,
                "note": "image too small"}
    small = img.convert("RGB").resize((w, h), Image.BILINEAR)
    px = small.load()
    patch = max(2, min(w, h) // 25)
    corners = []
    for x0, y0 in ((0, 0), (w - patch, 0), (0, h - patch),
                   (w - patch, h - patch)):
        vals = [px[x, y] for y in range(y0, y0 + patch)
                for x in range(x0, x0 + patch)]
        corners.append(vals)
    allv = [p for c in corners for p in c]
    bg = tuple(sorted(p[i] for p in allv)[len(allv) // 2] for i in range(3))
    cmean = [[sum(p[i] for p in c) / float(len(c)) for i in range(3)]
             for c in corners]           # TL, TR, BL, BR
    flat = True
    for c in corners:
        for i in range(3):
            mean = sum(p[i] for p in c) / float(len(c))
            var = sum((p[i] - mean) ** 2 for p in c) / float(len(c))
            if math.sqrt(var) > CONTENT_FLAT_STD:
                flat = False

    # 非平涂时用四角双线性插值估背景：轻渐变/柔和打光背景也能分离主体，
    # 避免 final gate 因为「背景不是平涂」直接被跳过（实测两张渐变底都没跑闸门）。
    def bg_at(x, y):
        u = x / float(max(1, w - 1))
        v = y / float(max(1, h - 1))
        top = [cmean[0][i] * (1 - u) + cmean[1][i] * u for i in range(3)]
        bot = [cmean[2][i] * (1 - u) + cmean[3][i] * u for i in range(3)]
        return [top[i] * (1 - v) + bot[i] * v for i in range(3)]

    def is_bg(c, x=0, y=0):
        ref = bg if flat else bg_at(x, y)
        return (abs(c[0] - ref[0]) + abs(c[1] - ref[1])
                + abs(c[2] - ref[2])) <= tol

    seen = [[False] * w for _ in range(h)]
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not seen[y][x] and is_bg(px[x, y], x, y):
                seen[y][x] = True
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not seen[y][x] and is_bg(px[x, y], x, y):
                seen[y][x] = True
                q.append((x, y))
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] \
                    and is_bg(px[nx, ny], nx, ny):
                seen[ny][nx] = True
                q.append((nx, ny))
    bg_hits = sum(1 for row in seen for v in row if v)
    bg_share = bg_hits / float(w * h)
    mask = [[not seen[y][x] for x in range(w)] for y in range(h)]
    xs = [x for y in range(h) for x in range(w) if mask[y][x]]
    ys = [y for y in range(h) for x in range(w) if mask[y][x]]
    if not xs:
        return {"applied": True, "reliable": False, "empty": True,
                "flat": flat, "bg_share": round(bg_share, 3),
                "note": "no content found"}
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    def longest_run(y):
        best = cur = 0
        for x in range(w):
            cur = cur + 1 if mask[y][x] else 0
            best = max(best, cur)
        return best

    content_w = max(1, x1 - x0 + 1)
    share = content_w * (y1 - y0 + 1) / float(w * h)
    sx, sy = W / float(w), H / float(h)
    reliable = bool(bg_share >= CONTENT_BG_SHARE_MIN
                    and share >= CONTENT_SHARE_MIN)
    return {
        "applied": True,
        "reliable": reliable,
        "empty": False,
        "flat": bool(flat),
        "bg_model": "flat" if flat else "gradient",
        "bg": list(bg),
        "bg_corners": cmean,
        "bg_share": round(bg_share, 3),
        "bbox": [round(x0 * sx, 1), round(y0 * sy, 1),
                 round((x1 + 1) * sx, 1), round((y1 + 1) * sy, 1)],
        "top_blank": round(y0 / float(h), 3),
        "bottom_blank": round((h - 1 - y1) / float(h), 3),
        "left_blank": round(x0 / float(w), 3),
        "right_blank": round((w - 1 - x1) / float(w), 3),
        "share": round(share, 3),
        "top_run_ratio": round(longest_run(y0) / float(content_w), 3),
        "note": None if reliable else "background not flat / content not separable",
    }


def _content_gate(content, raw_content=None):
    """成品内容闸门：留白、主体占比、头顶平切。不可靠时记 null。"""
    if not content or not content.get("applied") or not content.get("reliable"):
        return {"applied": False, "passed": None, "checks": {}, "reasons": [],
                "note": (content or {}).get("note") or "content metrics unavailable"}
    top_min = TOP_BLANK_MIN
    if raw_content and raw_content.get("reliable"):
        # 原图本身头顶贴边时，不额外要求成品留出上边距
        top_min = min(TOP_BLANK_MIN, raw_content.get("top_blank") or 0.0)
    checks = {
        "top_blank": top_min <= content["top_blank"] <= TOP_BLANK_MAX,
        "bottom_blank": content["bottom_blank"] <= BOTTOM_BLANK_MAX,
        "subject_share": SUBJECT_SHARE_MIN <= content["share"] <= SUBJECT_SHARE_MAX,
        "head_not_flat": content["top_run_ratio"] <= HEAD_CUT_RUN_RATIO,
    }
    reasons = []
    if not checks["top_blank"] or not checks["bottom_blank"]:
        reasons.append("composition_bad:whitespace")
    if not checks["subject_share"]:
        reasons.append("composition_bad:subject_share")
    if not checks["head_not_flat"]:
        reasons.append("composition_bad:head_cut")
    return {"applied": True, "passed": not reasons, "checks": checks,
            "reasons": reasons, "metric_set": "content_bbox",
            "thresholds": {"top_blank_min": top_min, "top_blank_max": TOP_BLANK_MAX,
                           "bottom_blank_max": BOTTOM_BLANK_MAX,
                           "subject_share": [SUBJECT_SHARE_MIN, SUBJECT_SHARE_MAX],
                           "top_run_ratio_max": HEAD_CUT_RUN_RATIO}}


def _content_head(img, content=None):
    """无肤色/无眼白时的头部轮廓回退：内容最高段定头顶，顶部 40% 最宽行定头宽。

    不依赖人脸模型，也不冒充「已检出脸」：`method=content-head`、
    `profile=False`、`eyes=[]`，交给角度闸门判方向；方向判不出记
    angle_undetermined，而不是直接 no_face。
    """
    info = content or _content_analysis(img)
    if not info.get("applied") or not info.get("reliable") or info.get("empty"):
        return None
    W, H = img.size
    bx0, by0, bx1, by1 = info["bbox"]
    top = max(0.0, by0)
    content_h = max(1.0, by1 - top)
    band_bottom = top + 0.40 * content_h

    work = min(CONTENT_WORK, W)
    h = max(1, int(round(H * work / float(W))))
    small = img.convert("RGB").resize((work, h), Image.BILINEAR)
    px = small.load()
    bg = tuple(info.get("bg") or (255, 255, 255))
    corners_mean = info.get("bg_corners")

    def is_bg(c, x=0, y=0):
        if corners_mean:
            u = x / float(max(1, work - 1))
            v = y / float(max(1, h - 1))
            top = [corners_mean[0][i] * (1 - u) + corners_mean[1][i] * u
                   for i in range(3)]
            bot = [corners_mean[2][i] * (1 - u) + corners_mean[3][i] * u
                   for i in range(3)]
            ref = [top[i] * (1 - v) + bot[i] * v for i in range(3)]
        else:
            ref = bg
        return (abs(c[0] - ref[0]) + abs(c[1] - ref[1])
                + abs(c[2] - ref[2])) <= CONTENT_TOL

    sx, sy = W / float(work), H / float(h)
    y_limit = int(clamp(band_bottom / sy, 0, h - 1))
    best = None
    for y in range(int(clamp(top / sy, 0, h - 1)), y_limit + 1):
        start = None
        for x in range(work + 1):
            solid = x < work and not is_bg(px[x, y], x, y)
            if solid and start is None:
                start = x
            elif not solid and start is not None:
                if best is None or (x - start) > (best[1] - best[0]):
                    best = (start, x)
                start = None
    if best is None:
        return None
    head_w = max(1.0, (best[1] - best[0]) * sx)
    head_h = head_w * 1.05
    cx = (best[0] + best[1]) * 0.5 * sx
    if head_w < 0.15 * min(W, H):
        return None
    box = (clamp(cx - head_w / 2.0, 0, W - 4),
           clamp(top, 0, H - 4),
           clamp(head_w, 4, W), clamp(head_h, 4, H))
    return {"box": box, "eye_y": top + 0.55 * head_h, "eyes": [],
            "conf": 0.40, "candidates": 1, "method": "content-head",
            "eye_source": "estimated", "profile": False,
            "located_by": "contour", "content": info}


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
            # 无肤色/无眼白（头盔/皮套/纯无脸角色）：走内容轮廓回退，
            # 不再直接 no_face 判死；定位失败才由主流程记 locate_failed。
            head = _content_head(img)
            if head:
                head["candidates"] = multi if multi > 1 else 1
                return head
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

def build_crops(img, face, content=None):
    W, H = img.size
    fx, fy, fw, fh = face["box"]
    eye_y = face["eye_y"]
    face_area = float(fw * fh)
    eye_target = PROFILE_EYE_TARGET if face.get("profile") else EYE_TARGET
    content_top = None
    if (content and content.get("applied") and content.get("reliable")
            and not content.get("empty")):
        content_top = content["bbox"][1]

    def keep_head(top, s):
        """内容最高点必须在画布上沿留出安全边距：禁止裁掉头顶/耳尖。"""
        if content_top is None:
            return top
        return min(top, content_top - HEAD_SAFE_MARGIN * s)

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
        top = keep_head(top, s)
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
    top = clamp(keep_head(ideal_top, s), 0, H - s)
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


def score_crop(img, crop, face, image_content=None):
    left, top, right, bottom = crop["box"]
    s = right - left
    fx, fy, fw, fh = face["box"]
    eye_y = face["eye_y"]
    # 脸框可能有一部分落在裁切框外：先求与裁切框的交集，再算占比。
    # 直接用整张脸框面积会算出 >1 的占比（实测 1.253/1.125），导致闸门失真。
    ix = max(0.0, min(fx + fw, right) - max(fx, left))
    iy = max(0.0, min(fy + fh, bottom) - max(fy, top))
    face_ratio = clamp((ix * iy) / (0.785 * s * s), 0.0, 1.0)
    eye_center_y = (eye_y - top) / s
    margins = [fx - left, right - (fx + fw), fy - top, bottom - (fy + fh)]
    margin = min(margins) / s
    region = img.crop((int(round(left)), int(round(top)),
                       int(round(left + s)), int(round(top + s))))
    edge = _edge_cut(region)
    content = _content_analysis(region)
    content_gate = _content_gate(content, image_content)
    # subject_cut 只在平涂背景可判定时生效：渐变/写实场景的边圈不全是背景，
    # 贴边连续段会被误当主体（实测渐变底会误判）。gradient 只用于 final gate
    # 的留白/占比，不参与截断判死。
    cut_trust = bool(content.get("applied") and content.get("reliable")
                     and content.get("flat"))
    cut_hard = ([e for e in CUT_HARD_EDGES if edge.get(e, {}).get("cut")]
                if cut_trust else [])

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
    reasons.extend(content_gate["reasons"])
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
            "content": {k: content.get(k) for k in
                        ("reliable", "top_blank", "bottom_blank", "left_blank",
                         "right_blank", "share", "top_run_ratio", "bbox")},
            "content_gate": content_gate,
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


def _faceless_head(img, content=None):
    """无脸模式定位：只用头部/头盔轮廓，不用眼部锚点。"""
    head = _content_head(img, content)
    if not head:
        return None
    head = dict(head)
    head["method"] = "contour-head"
    head["face_mode"] = "faceless"
    head["profile"] = False
    head["eyes"] = []
    return head


def _faceless_angle_metrics(img, head, content=None):
    """无脸朝向信号：面罩/高光饱和质心偏移 + 头盔左右轮廓对称性。"""
    info = content or _content_analysis(img)
    W, H = img.size
    hx, hy, hw, hh = head["box"]
    bg = (info or {}).get("bg")
    if not info or not info.get("reliable") or not bg:
        return {"metric_set": "head_contour", "mode": "faceless",
                "contour_asym": None, "visor_offset": None, "sat_weight": 0.0,
                "facing": None, "note": "content not separable"}
    hcx = hx + hw / 2.0
    work = min(CONTENT_WORK, W)
    h = max(1, int(round(H * work / float(W))))
    small = img.convert("RGB").resize((work, h), Image.BILINEAR)
    px = small.load()
    sx, sy = W / float(work), H / float(h)

    def is_bg(c):
        return (abs(c[0] - bg[0]) + abs(c[1] - bg[1])
                + abs(c[2] - bg[2])) <= CONTENT_TOL

    lx0 = int(clamp(hx / sx, 0, work - 1))
    lx1 = int(clamp((hx + hw) / sx, 0, work))
    y0 = int(clamp(hy / sy, 0, h - 1))
    y1 = int(clamp((hy + hh) / sy, 0, h))
    pad = int(0.20 * max(1, y1 - y0))
    asyms = []
    for y in range(y0 + pad, max(y0 + pad + 1, y1 - pad)):
        xs = [x for x in range(lx0, lx1) if not is_bg(px[x, y])]
        if len(xs) < 3:
            continue
        ls = (hcx - xs[0] * sx) / max(1.0, hw)
        rs = (xs[-1] * sx - hcx) / max(1.0, hw)
        asyms.append((rs - ls) / max(1e-6, rs + ls))

    # 面罩/高光：头部上半部高饱和像素质心相对头框中线的偏移
    cw = min(240, W)
    ch = max(1, int(round(H * cw / float(W))))
    s2 = img.convert("RGB").resize((cw, ch), Image.BILINEAR)
    p2 = s2.load()
    x0 = max(0, int(hx * cw / float(W)))
    x1 = min(cw, int((hx + hw) * cw / float(W)))
    yy0 = max(0, int(hy * ch / float(H)))
    yy1 = min(ch, int((hy + 0.60 * hh) * ch / float(H)))
    sw = cxw = 0.0
    for y in range(yy0, yy1):
        for x in range(x0, x1):
            r, g, b = p2[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            sat = (mx - mn) / 255.0
            if sat >= 0.35 and mx >= 40:
                sw += sat
                cxw += x * sat
    visor_offset = None
    if sw >= FACELESS_SAT_MIN:
        visor_offset = (cxw / sw) * W / float(cw) / max(1.0, hw) - hcx / max(1.0, hw)

    asym = None if not asyms else sum(asyms) / len(asyms)
    facing, confidence = None, "low"
    if visor_offset is not None and asym is not None:
        if abs(visor_offset) <= VISOR_FRONT_MAX and abs(asym) <= 0.06:
            facing, confidence = "front", "high"
        elif visor_offset <= -VISOR_SIDE_MIN and asym <= CONTOUR_SIDE_MIN:
            facing, confidence = "left", "high"
        elif visor_offset >= VISOR_SIDE_MIN and asym >= -CONTOUR_SIDE_MIN:
            facing, confidence = "right", "high"
    return {
        "metric_set": "head_contour", "mode": "faceless",
        "contour_asym": None if asym is None else round(asym, 3),
        "visor_offset": None if visor_offset is None else round(visor_offset, 3),
        "sat_weight": round(sw, 1),
        "facing": facing, "confidence": confidence,
    }


def build_crops_faceless(img, head, content=None):
    """无脸模式裁切：以头部宽度占画面 0.50 为目标，不用眼位。"""
    W, H = img.size
    hx, hy, hw, hh = head["box"]
    hcx = hx + hw / 2.0
    content_top = None
    if (content and content.get("applied") and content.get("reliable")
            and not content.get("empty")):
        content_top = content["bbox"][1]

    def keep_head(top, s):
        if content_top is None:
            return top
        return min(top, content_top - HEAD_SAFE_MARGIN * s)

    out = []
    for wr in (0.44, 0.50, 0.56):
        s = hw / wr
        s = min(s, min(W, H))
        ideal_left = hcx - s / 2.0
        ideal_top = hy - 0.08 * s
        left = clamp(ideal_left, 0, W - s)
        top = clamp(keep_head(ideal_top, s), 0, H - s)
        cost = (abs(left - ideal_left) + abs(top - ideal_top)) / s
        out.append({"box": (left, top, left + s, top + s), "target": wr,
                    "cost": round(cost, 4)})
    s = float(min(W, H))
    ideal_left = hcx - s / 2.0
    ideal_top = hy - 0.08 * s
    left = clamp(ideal_left, 0, W - s)
    top = clamp(keep_head(ideal_top, s), 0, H - s)
    out.append({"box": (left, top, left + s, top + s), "target": "full",
                "cost": round((abs(left - ideal_left) + abs(top - ideal_top)) / s, 4)})
    return out


def score_crop_faceless(img, crop, head, image_content=None):
    """无脸评分：头部轮廓占比 + 上下留白 + 居中偏移；面部指标全部不参与。"""
    left, top, right, bottom = crop["box"]
    s = right - left
    hx, hy, hw, hh = head["box"]
    head_ratio = (hw * hh) / (0.785 * s * s)
    head_width_ratio = hw / s
    center_offset = abs((hx + hw / 2.0) - (left + s / 2.0)) / s
    region = img.crop((int(round(left)), int(round(top)),
                       int(round(left + s)), int(round(top + s))))
    edge = _edge_cut(region)
    content = _content_analysis(region)
    content_gate = _content_gate(content, image_content)
    # subject_cut 只在平涂背景可判定时生效：渐变/写实场景的边圈不全是背景，
    # 贴边连续段会被误当主体（实测渐变底会误判）。gradient 只用于 final gate
    # 的留白/占比，不参与截断判死。
    cut_trust = bool(content.get("applied") and content.get("reliable")
                     and content.get("flat"))
    cut_hard = ([e for e in CUT_HARD_EDGES if edge.get(e, {}).get("cut")]
                if cut_trust else [])

    ratio_score = clamp(1 - abs(head_ratio - HEAD_TARGET) / 0.20, 0, 1)
    if content.get("reliable"):
        top_blank = content["top_blank"]
        bottom_blank = content["bottom_blank"]
        blank_score = 1 - (abs(top_blank - 0.08) / 0.12
                           + abs(bottom_blank - 0.05) / 0.16) / 2.0
        blank_score = clamp(blank_score, 0, 1)
    else:
        top_blank = bottom_blank = None
        blank_score = 0.7            # 不可判定记中性，不冒充满分
    center_score = clamp(1 - center_offset / 0.15, 0, 1)
    cost = crop.get("cost", 0.0)
    composition = 100 * (0.45 * ratio_score + 0.30 * blank_score
                         + 0.25 * center_score)
    composition -= COST_PENALTY * clamp(cost / COST_TOL, 0, 1)
    composition = clamp(composition, 0, 100)

    head_in_crop = (hx - left, hy - top, hw, hh)
    sharp = sharpness(region, head_in_crop)
    sharp_score = clamp(100 * (math.log(sharp + 1) - math.log(11.0)) /
                        (math.log(101.0) - math.log(11.0)), 0, 100)
    blown = blown_ratio(region, head_in_crop)
    exposure_score = 100 * (1 - clamp((blown - 0.25) / 0.50, 0, 1))
    face_quality = clamp(0.80 * sharp_score + 0.20 * exposure_score, 0, 100)

    reasons = []
    if not (HEAD_RATIO_MIN <= head_ratio <= HEAD_RATIO_MAX):
        reasons.append("composition_bad:head_ratio")
    if center_offset > CENTER_OFFSET_MAX:
        reasons.append("composition_bad:centering")
    if cut_hard:
        reasons.append("composition_bad:subject_cut")
    reasons.extend(content_gate["reasons"])
    if sharp < BLUR_MIN:
        reasons.append("face_blurry")

    total = (W_FACE_QUALITY * face_quality + W_COMPOSITION * composition) / \
            float(W_FACE_QUALITY + W_COMPOSITION)
    return {
        "box": [round(v, 1) for v in crop["box"]],
        "native_size": int(round(s)),
        "metric_set": "head_contour",
        "face_metrics_applied": False,
        "metrics": {
            "head_ratio": round(head_ratio, 3),
            "head_width_ratio": round(head_width_ratio, 3),
            "center_offset": round(center_offset, 3),
            "top_blank": top_blank,
            "bottom_blank": bottom_blank,
            "face_ratio": None,
            "eye_center_y": None,
            "eye_tilt": None,
            "cut_edges": cut_hard,
            "edge_contact": {k: edge[k]["contact"] for k in ("top", "bottom", "left", "right")},
            "sharpness": round(sharp, 2),
            "blown": round(blown, 3),
            "crop_cost": round(cost, 3),
            "content": {k: content.get(k) for k in
                        ("reliable", "top_blank", "bottom_blank", "left_blank",
                         "right_blank", "share", "top_run_ratio", "bbox")},
            "content_gate": content_gate,
        },
        "scores": {
            "face_quality": round(face_quality, 1),
            "face_quality_parts": {"sharpness": round(sharp_score, 1),
                                   "symmetry": None, "exposure": round(exposure_score, 1)},
            "composition": round(composition, 1),
            "style": None, "emotion": None, "anatomy": None,
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


_ROUND_N = re.compile(r"(?:^|[_-])round[_-]?(\d+)(?=[_.-]|$)", re.I)
_ROUND_R = re.compile(r"(?:^|[_-])r[_-]?(\d+)(?=[_.-]|$)", re.I)


def _canon_round(value):
    """轮次值规范化：1 / r1 / round_01 统一为 r1；vN 只当变体，不当轮次。"""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    m = re.fullmatch(r"(?:round|r)[\s_-]*(\d+)", v, re.I)
    if m:
        return "r" + str(int(m.group(1)))
    if re.fullmatch(r"\d+", v):
        return "r" + str(int(v))
    return v.lower()


def resolve_round_meta(cli_round=None, brief_round=None):
    """flat 结构的轮次只来自元数据：--round > brief.round。

    文件名与目录都不再带轮次，因此取不到时返回 ("__batch__", "unknown")，
    由调用方打印告警，不得从文件名或父目录猜。
    """
    for value, source in ((cli_round, "cli"), (brief_round, "brief")):
        key = _canon_round(value)
        if key:
            return key, source
    return "__batch__", "unknown"


def resolve_round_legacy(name, path, cli_round=None, brief_round=None):
    """旧结构（legacy）轮次解析：--round > brief.round > 文件名 > 父目录。

    只用于复跑历史订单；新订单的 flat 结构只用 resolve_round_meta。
    """
    for value, source in ((cli_round, "cli"), (brief_round, "brief")):
        key = _canon_round(value)
        if key:
            return key, source
    for pat in (_ROUND_N, _ROUND_R):
        m = pat.search(name)
        if m:
            return "r" + str(int(m.group(1))), "filename"
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    for pat in (_ROUND_N, _ROUND_R):
        m = pat.search(parent)
        if m:
            return "r" + str(int(m.group(1))), "dir"
    return "__batch__", "unknown"


_VARIANT = re.compile(r"(?:^|[_-])v(\d+)(?=[_.-]|$)", re.I)
_FLAT_SRC = re.compile(
    r"^(?P<prefix>.+)-avatar-(?P<n>\d+)-src\.(?P<ext>[A-Za-z0-9]+)$", re.I)
_FLAT_FINAL = re.compile(
    r"^(?P<prefix>.+)-avatar-(?P<n>\d+)\.(?:png|jpe?g|webp)$", re.I)
_FLAT_EXPORT = re.compile(
    r"^.+?-avatar-\d+-(?:2k|4k|q\d+)\.(?:png|jpe?g)$", re.I)
_FLAT_ANY_NUMBER = re.compile(
    r"^.+?-avatar-(\d+)(?:-[a-z0-9]+)?\.[A-Za-z0-9]+$", re.I)


def resolve_variant(name):
    m = _VARIANT.search(name or "")
    return int(m.group(1)) if m else None


def is_sheet_name(name):
    """联系表：旧的通用 contact_sheet.jpg 与按订单的 <前缀>-avatar-<起>-<止>-sheet.jpg。"""
    low = (name or "").lower()
    return low == "contact_sheet.jpg" or low.endswith("-sheet.jpg")


def flat_sources(order_dir):
    """订单根里待处理的原图：排除成品、规格导出与联系表。"""
    names = []
    for name in sorted(os.listdir(order_dir)):
        path = os.path.join(order_dir, name)
        if not os.path.isfile(path):
            continue
        if not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        if is_sheet_name(name):
            continue
        if re.match(r".+-ref-\d+\.[A-Za-z0-9]+$", name, re.I):
            continue          # 参考图是生成入参，不是待评分原图
        if _FLAT_EXPORT.match(name) or _FLAT_FINAL.match(name):
            continue
        names.append(name)
    return names


def flat_detect_prefix(order_dir):
    """订单根里已有 <prefix>-avatar-N 文件时，沿用出现最多的前缀。"""
    counts = {}
    pat = re.compile(r"^(?P<p>.+)-avatar-\d+(?:-[a-z0-9]+)?\.[A-Za-z0-9]+$", re.I)
    for name in os.listdir(order_dir):
        if is_sheet_name(name):
            continue
        m = pat.match(name)
        if m:
            p = m.group("p")
            counts[p] = counts.get(p, 0) + 1
    return max(counts, key=counts.get) if counts else None


def flat_max_number(order_dir, prefix=None):
    """订单内已用的最大编号（含成品/原图/导出），跨单跨轮连续递增。"""
    mx = 0
    for name in os.listdir(order_dir):
        if is_sheet_name(name):
            continue
        m = _FLAT_ANY_NUMBER.match(name)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def ledger_order(order_root):
    """读 rounds.json 的当前订单 id 与下单时间；旧台账没有则返回 (None, None)。"""
    path = os.path.join(order_root, "rounds.json")
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, None
    oid = data.get("order_id")
    ordered_at = data.get("ordered_at")
    orders = data.get("orders") or []
    if not oid and orders:
        oid = orders[-1].get("order_id")
        ordered_at = ordered_at or orders[-1].get("ordered_at")
    return oid, ordered_at


def flat_numbers(names, prefix, start=0):
    """给订单内原图分配编号；已带 -src 的沿用原编号，其余从 start 往后接。"""
    nums = [None] * len(names)
    used = set()
    for i, name in enumerate(names):
        m = _FLAT_SRC.match(name)
        if m:
            nums[i] = int(m.group("n"))
            used.add(nums[i])
    nxt = max([start] + list(used)) + 1
    for i in range(len(names)):
        if nums[i] is None:
            while nxt in used:
                nxt += 1
            nums[i] = nxt
            used.add(nxt)
            nxt += 1
    return nums


def flat_rename_source(path, prefix, nn):
    """把原图落到订单根 <prefix>-avatar-<nn>-src.<ext>；已是该名则不动。"""
    _, ext = os.path.splitext(path)
    target = os.path.join(os.path.dirname(path),
                          "%s-avatar-%d-src%s" % (prefix, nn,
                                                  ext.lower() or ".png"))
    if os.path.abspath(path) != os.path.abspath(target):
        if os.path.exists(target):
            raise OSError("目标已存在，拒绝覆盖：%s" % target)
        os.replace(path, target)
    return target


def export_flat(master, native_crop, prefix, nn, order_dir, formats, want_jpg, jpg_q):
    """单层平铺：成品 <prefix>-avatar-N.png；可选 -2k/-4k/-qNN。"""
    outputs = {}
    upscale_info = {"applied": False, "method": "none", "tiers": {}}
    stem = "%s-avatar-%d" % (prefix, nn)

    final_path = os.path.join(order_dir, stem + ".png")
    master.save(final_path)
    outputs["final"] = rel(final_path, order_dir)

    for tier in formats:
        if tier == "1024":
            continue
        size = SIZE_TIERS[tier]
        factor = size / float(native_crop.width)
        big = upscale(native_crop, size)
        p = os.path.join(order_dir, "%s-%s.png" % (stem, tier))
        big.save(p)
        outputs["png_" + tier] = rel(p, order_dir)
        upscale_info["applied"] = True
        upscale_info["method"] = "Pillow LANCZOS (single-pass)"
        upscale_info["tiers"][tier] = {"size": size, "factor": round(factor, 2),
                                       "detail_added": False}

    if want_jpg and "1024" in formats:
        jp = os.path.join(order_dir, "%s-q%d.jpg" % (stem, jpg_q))
        master.convert("RGB").save(jp, quality=jpg_q, optimize=True, progressive=True)
        outputs["jpg_1024_q%d" % jpg_q] = rel(jp, order_dir)

    return outputs, upscale_info


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
    """四角 patch 的主色，作为平涂背景估计；估不出时返回 None。

    先逐角取中位色，再选「与最多角一致」的那一簇，而不是把四角平均。
    四角平均/逐通道中位在「上两角白底、下两角深色装甲」时折成灰，
    既不是背景也不是主体：背景占比被低估、平涂底被判 busy，
    平涂背景下的截断判定也随之失效。角簇打平时优先上方两角
    （胸像头像背景通常在上半部）。
    """
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
    best, best_key = None, None
    for i, c in enumerate(corners):
        near = sum(1 for d in corners
                   if abs(c[0] - d[0]) + abs(c[1] - d[1]) + abs(c[2] - d[2]) <= 60)
        key = (near, 1 if i < 2 else 0)
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


def _background_stats(img, tol=50):
    """用四角中位数估计背景色，再统计接近该色的像素占比与明度波动。

    比「外圈 stddev」稳：紧构图头像的边圈常是头发/衣服，外圈法会把角色误当背景。
    """
    # 轻渐变背景：四角双线性模型能可靠分离主体时判 gradient，
    # 不再因为「不是平涂」直接算 busy（style profile 明确允许 soft_gradient）。
    info = _content_analysis(img)
    if (info.get("applied") and info.get("reliable")
            and info.get("bg_model") == "gradient" and not info.get("empty")):
        return info.get("bg_share", 0.0), 0.0, "gradient"
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
    hist = img.convert("L").histogram()
    total_px = float(w * h)

    def _pct(p):
        target, acc = total_px * p, 0
        for i, c in enumerate(hist):
            acc += c
            if acc >= target:
                return i / 255.0
        return 1.0

    value_p50, value_p90 = round(_pct(0.50), 3), round(_pct(0.90), 3)
    near_white_share = round(sum(hist[245:]) / total_px, 3) if total_px else 0.0

    # 硬边/软边比：赛璐璐以硬边色块为主、软过渡极少；写实厚涂反之。
    # 用相邻像素梯度计数，hard=明显台阶，soft=中间过渡，比值越大越「硬」。
    gp = g.load()
    hard = soft = 0
    for y in range(511):
        for x in range(511):
            v = gp[x, y]
            d = max(abs(gp[x + 1, y] - v), abs(gp[x, y + 1] - v))
            if d >= 40:
                hard += 1
            elif d >= 8:
                soft += 1
    hard_soft_ratio = round(hard / float(max(1, soft)), 3)

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
        "value_p50": value_p50,
        "value_p90": value_p90,
        "hard_soft_ratio": hard_soft_ratio,
        "near_white_share": near_white_share,
        "background_share": round(bg_share, 3),
        "background_stddev": round(bg_std, 1),
        "background_mode": bg_mode,
    }


def check_style(metrics, profile):
    """按风格档案 gate 判定；没有档案时返回 None（不冒充通过）。"""
    if not profile:
        return None
    gate = profile.get("gate") or {}
    if gate.get("applicable") is False:
        return {"passed": None, "applicable": False, "checks": {},
                "reasons": ["style_not_applicable"],
                "note": gate.get("note") or
                        "风格档案声明该维度不适用：记 null，不当作通过"}
    checks = {}
    lo, hi = gate.get("palette_colors", [0, 999])
    checks["palette_colors"] = lo <= metrics["palette_colors"] <= hi
    lo, hi = gate.get("saturation", [0.0, 1.0])
    checks["saturation"] = lo <= metrics["saturation"] <= hi
    lo, hi = gate.get("edge_density", [0.0, 1.0])
    checks["edge_density"] = lo <= metrics["edge_density"] <= hi
    if gate.get("value_p50"):
        lo, hi = gate["value_p50"]
        checks["value_p50"] = lo <= metrics["value_p50"] <= hi
    if gate.get("hard_soft_ratio_max") is not None:
        checks["hard_soft_ratio"] = (metrics["hard_soft_ratio"]
                                     <= gate["hard_soft_ratio_max"])
    if gate.get("near_white_share_max") is not None:
        checks["near_white_share"] = (metrics["near_white_share"]
                                      <= gate["near_white_share_max"])
    if metrics.get("background_mode") == "gradient":
        # 轻渐变是风格档案允许的交付背景；明度波动不再按平涂口径卡。
        checks["background"] = True
    else:
        checks["background"] = (
            metrics["background_stddev"] <= gate.get("background_stddev_max", 999)
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
    if visible == 2:
        fx, fy, fw, fh = face["box"]
        balance = None
        if fw:
            ex = [(e[0] - fx) / float(fw) for e in eyes[:2]]
            balance = abs(sum(ex) / 2.0 - 0.5)
        return {
            "visible_eyes": 2,
            "pupil_edge": None,
            "skin_centroid": None,
            "nose_side": None,
            "facing": "front",
            "is_front": True,
            "is_profile": False,
            "eye_balance": None if balance is None else round(balance, 3),
            "eye_center_y": metrics.get("eye_center_y"),
            "confidence": "high" if (balance is not None
                                     and balance <= FRONT_EYE_BALANCE_MAX) else "medium",
        }
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

    if detected.get("metric_set") == "head_contour":
        off = detected.get("visor_offset")
        asym = detected.get("contour_asym")
        yaw = angle.get("yaw")
        checks = {"signal_available": off is not None and asym is not None}
        if off is not None:
            if yaw == "front":
                checks["visor_centered"] = abs(off) <= VISOR_FRONT_MAX + 0.02
            elif yaw == "left":
                checks["visor_side"] = off <= -VISOR_SIDE_MIN
            elif yaw == "right":
                checks["visor_side"] = off >= VISOR_SIDE_MIN
        if asym is not None:
            if yaw == "front":
                checks["contour_symmetric"] = abs(asym) <= 0.08
            elif yaw == "left":
                checks["contour_side"] = asym <= CONTOUR_SIDE_MIN
            elif yaw == "right":
                checks["contour_side"] = asym >= -CONTOUR_SIDE_MIN
        facing = detected.get("facing")
        checks["facing"] = None if facing is None else (facing == yaw)
        passed = all(v is True for v in checks.values())
        direction_known = facing in ("front", "left", "right")
        reason = None if passed else ("angle_undetermined" if not direction_known
                                      else "angle_mismatch")
        return {"expected": expected_id, "label": angle.get("label"),
                "enabled": True, "detected": detected, "passed": passed,
                "checks": checks, "reason": reason,
                "direction_known": direction_known,
                "metric_set": "head_contour",
                "side_check_note": "无脸判据：面罩/高光横向偏移 + 头盔轮廓左右对称性；"
                                   "偏移/对称信号缺失或冲突即 angle_undetermined"}

    family = angle.get("family") or ("frontal" if expected_id in ("front", "frontal") else "profile")
    if family == "frontal":
        ey = metrics.get("eye_center_y")
        elo, ehi = angle.get("eye_y", FRONT_EYE_Y)
        tilt = metrics.get("eye_tilt")
        bal = detected.get("eye_balance")
        checks = {
            "two_eyes": detected.get("visible_eyes") == 2,
            "facing_front": detected.get("facing") == "front",
            "eye_y": None if ey is None else (elo <= ey <= ehi),
            "eye_tilt": None if tilt is None else (tilt <= FRONT_EYE_TILT_MAX),
            "eye_balance": None if bal is None else (bal <= FRONT_EYE_BALANCE_MAX),
        }
        passed = all(v is True for v in checks.values())
        direction_known = detected.get("facing") in ("front", "left", "right")
        reason = None if passed else ("angle_mismatch" if direction_known
                                      else "angle_undetermined")
        return {"expected": expected_id, "label": angle.get("label"),
                "enabled": True, "detected": detected, "passed": passed,
                "checks": checks, "reason": reason,
                "direction_known": direction_known,
                "side_check_note": "正脸判据：双眼检出 + 眼位 %.2f–%.2f（现有正样本实测 "
                                   "0.303–0.316）+ 双眼水平平衡 ≤%.2f + 眼线倾斜 ≤%.2f；"
                                   "不套用侧脸 0.32–0.46"
                                   % (elo, ehi, FRONT_EYE_BALANCE_MAX, FRONT_EYE_TILT_MAX)}

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
    # 方向必须能判出来：瞳孔落在侧脸判定带内时方向已确定，不要求鼻尖侧；
    # 瞳孔居中（0.42–0.58）且鼻尖侧不可用时仍判 angle_undetermined，不猜。
    # 鼻尖与瞳孔冲突时 facing 本身为 None，仍判 angle_undetermined。
    band_ok = pe is not None and (pe <= PROFILE_LEFT_MAX or pe >= PROFILE_RIGHT_MIN)
    strong_pupil = pe is not None and (pe <= 0.12 or pe >= 0.88)
    nose_not_required = strong_pupil or band_ok
    evaluated = {k: v for k, v in checks.items()
                 if not (k == "nose_side" and v is None and nose_not_required)}
    passed = all(v is True for v in evaluated.values())
    direction_known = (facing in ("left", "right")
                       and (nose in ("left", "right") or nose_not_required))
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
FACE_MODES = ("faced", "faceless")
ANGLE_IDS = ("profile_left", "profile_right", "front")
DEFAULT_ANGLE_BY_GENDER = {"male": "profile_left", "female": "profile_right",
                           "unspecified": "profile_left"}


def normalize_brief(brief, card, budget):
    """补默认值并校验 brief；返回 (normalized, errors)。

    默认：画质 1024 / 张数 2 / 预算 10；朝向按性别（男左、女右，未指定默认左），
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
    if explicit_angle in ("frontal", "front"):
        explicit_angle = "front"
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

    # 无脸/皮套模式：角色卡优先，brief 显式覆盖；默认有脸
    face_mode = (out.get("face_mode") or card.get("face_mode")
                 or (card.get("face") or {}).get("mode") or "faced")
    if face_mode not in FACE_MODES:
        errors.append("face_mode 必须是 %s" % "/".join(FACE_MODES))
        face_mode = "faced"
    out["face_mode"] = face_mode

    # 生成模式：参考图驱动为默认；text2img 必须由用户显式同意
    gen = dict(out.get("generation") or {})
    mode = gen.get("mode") or "reference"
    if mode not in ("reference", "text2img"):
        errors.append("generation.mode 只能是 reference / text2img")
        mode = "reference"
    if mode == "text2img" and gen.get("text2img_consent") is not True:
        errors.append("mode=text2img 需要用户显式同意"
                      "（generation.text2img_consent=true）")
    if mode == "reference" and not (gen.get("reference_images") or []):
        errors.append("参考图模式必须先归档参考图并在 "
                      "generation.reference_images 记账（见 confirm_order.py --reference）")
    out["generation"] = {
        "backend": gen.get("backend") or "agnes-aigc-img2img",
        "mode": mode,
        "reference_images": gen.get("reference_images") or [],
        "strength": None if gen.get("strength") is None else gen.get("strength"),
        "fallback": gen.get("fallback") or "fail",
        "text2img_consent": gen.get("text2img_consent") is True,
    }

    out["size"] = str(out.get("size") or "1024")
    out["formats"] = out.get("formats") or [out["size"]]
    try:
        count = int(out.get("count")) if out.get("count") is not None else 2
    except (TypeError, ValueError):
        count = 2
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
    raw = checklist.get("items") or {}
    if isinstance(raw, list):
        items = {it.get("key"): it for it in raw if isinstance(it, dict)}
    elif isinstance(raw, dict):
        items = raw
    else:
        items = {}
    results, missing = [], []
    for item in card.get("checklist", []):
        key = item.get("key")
        entry = items.get(key) or {}
        ok = bool(entry.get("ok", entry.get("checked")))
        results.append({"key": key, "label": item.get("label"),
                        "required": bool(item.get("required")), "ok": ok,
                        "note": entry.get("note")})
        if item.get("required") and not ok:
            missing.append(key)
    return {"character_id": card.get("character_id"), "passed": not missing,
            "items": results, "missing": missing,
            "reasons": ["character_missing:" + k for k in missing]}


# ---------------------------------------------------------------- 参考图与后端能力

REF_STYLE_INFORMATIONAL = 0.35   # 仅用于报告提示，不作为硬门槛


def _palette_hist(img, bins=4):
    small = img.convert("RGB").resize((128, 128), Image.BILINEAR)
    px = small.load()
    hist = [0] * (bins ** 3)
    for y in range(128):
        for x in range(128):
            r, g, b = px[x, y]
            idx = ((r * bins // 256) * bins + (g * bins // 256)) * bins + (b * bins // 256)
            hist[idx] += 1
    total = float(128 * 128)
    return [v / total for v in hist]


def _subject_hsv(img):
    """主体区域（内容框内，退化为中央 70%）的 HSV 均值，用于参考图对照。

    只做颜色/明度统计，不是身份或风格 embedding。
    """
    info = _content_analysis(img)
    if info.get("applied") and info.get("reliable") and not info.get("empty"):
        x0, y0, x1, y1 = [int(round(v)) for v in info["bbox"]]
        crop = img.crop((max(0, x0), max(0, y0),
                         min(img.width, x1), min(img.height, y1)))
    else:
        W, H = img.size
        crop = img.crop((int(W * 0.15), int(H * 0.10),
                         int(W * 0.85), int(H * 0.95)))
    if crop.width < 4 or crop.height < 4:
        crop = img
    small = crop.convert("HSV").resize((96, 96), Image.BILINEAR)
    px = small.load()
    hx = hy = s_sum = v_sum = cnt = 0.0
    for y in range(96):
        for x in range(96):
            h, s, v = px[x, y]
            if v < 30:
                continue
            rad = h / 255.0 * 2 * math.pi
            hx += math.cos(rad) * s
            hy += math.sin(rad) * s
            s_sum += s
            v_sum += v
            cnt += 1
    if not cnt:
        return {"h": None, "s": 0.0, "v": 0.0}
    hue = (math.atan2(hy, hx) % (2 * math.pi)) / (2 * math.pi) * 360.0
    return {"h": round(hue, 1), "s": round(s_sum / (255.0 * cnt), 3),
            "v": round(v_sum / (255.0 * cnt), 3)}


def reference_similarity(img, refs):
    """参考图相似度：色板 + 主体 HSV + 边缘密度 + 明度，身份无模型记 null。"""
    if not refs:
        return None
    base_hist = _palette_hist(img)
    base = style_metrics(img)
    base_hsv = _subject_hsv(img)
    items = []
    for ref in refs:
        rimg = ref["image"]
        rmet = style_metrics(rimg)
        rhsv = _subject_hsv(rimg)
        inter = sum(min(a, b) for a, b in zip(base_hist, _palette_hist(rimg)))
        sat_sim = 1 - clamp(abs(base["saturation"] - rmet["saturation"]) / 0.35, 0, 1)
        edge_sim = 1 - clamp(abs(base["edge_density"] - rmet["edge_density"]) / 0.12, 0, 1)
        value_sim = 1 - clamp(abs(base["value_p50"] - rmet["value_p50"]) / 0.30, 0, 1)
        if base_hsv["h"] is not None and rhsv["h"] is not None:
            dh = abs(base_hsv["h"] - rhsv["h"])
            dh = min(dh, 360.0 - dh) / 180.0
            ds = abs(base_hsv["s"] - rhsv["s"])
            dv = abs(base_hsv["v"] - rhsv["v"])
            subject_sim = 1 - clamp(0.50 * dh + 0.30 * ds + 0.20 * dv, 0, 1)
        else:
            dh = ds = dv = None
            subject_sim = 0.0
        style_sim = (0.40 * inter + 0.25 * subject_sim
                     + 0.20 * edge_sim + 0.15 * value_sim)
        items.append({
            "path": ref.get("path"), "role": ref.get("role"),
            "sha256": ref.get("sha256"),
            "palette_intersection": round(inter, 3),
            "subject_hsv_delta": (None if dh is None else
                                  {"h_deg": round(dh * 180.0, 1),
                                   "s": round(ds, 3), "v": round(dv, 3)}),
            "subject_similarity": round(subject_sim, 3),
            "saturation_delta": round(abs(base["saturation"] - rmet["saturation"]), 3),
            "edge_density_delta": round(abs(base["edge_density"] - rmet["edge_density"]), 3),
            "value_p50_delta": round(abs(base["value_p50"] - rmet["value_p50"]), 3),
            "value_similarity": round(value_sim, 3),
            "style_similarity": round(style_sim, 3),
        })
    mean_style = sum(i["style_similarity"] for i in items) / len(items)
    return {
        "style_similarity": round(mean_style, 3),
        "subject_similarity": round(sum(i["subject_similarity"] for i in items)
                                    / len(items), 3),
        "identity_similarity": None,
        "identity_note": ("无身份 embedding 模型，identity 相似度记 null；"
                          "不能用色板/主体 HSV 相似度冒充身份一致，请按人工清单核对"),
        "manual_identity_checklist": ["发型/发绺走向", "头饰/帽型与徽标",
                                      "服装配色与领口", "标志性细节（如脸颊纹）",
                                      "肤色与年龄感"],
        "per_reference": items,
        "informational": mean_style < REF_STYLE_INFORMATIONAL,
        "note": "style_similarity 只比较色板/主体 HSV/边缘/明度的像素统计，"
                "不是风格或身份 embedding",
    }


def default_backends_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "references", "backends.json")


def default_angles_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "references", "angles.json")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_generation(gen, registry, refs=None, order_dir=None):
    """调用前能力检测：核对后端能力，并核对本单是否真的把参考图传了进去。

    `identity_unverified=false` 只在「后端声明支持参考图 + 本单参考图可读 +
    sha256 与 brief 记录一致」时成立；否则一律 true，绝不假绿。
    text2img 只有在 `generation.text2img_consent=true` 时才放行。
    """
    if not gen:
        return None
    backends = {b.get("id"): b for b in (registry or {}).get("backends", [])}
    bid = gen.get("backend")
    backend = backends.get(bid)
    refs = refs or []
    declared = _active_entries(gen.get("reference_images"))
    mode = gen.get("mode") or ("reference" if declared else "text2img")
    fallback = gen.get("fallback") or "fail"
    consent = gen.get("text2img_consent") is True
    ref_cap = (backend or {}).get("reference_images") or {}
    supported = bool(ref_cap.get("supported"))
    max_refs = int(ref_cap.get("max") or 0)
    errors, warnings = [], []
    if backend is None:
        errors.append("未知后端 %r：先在 references/backends.json 登记并跑能力检测"
                      % bid)
    elif backend.get("available") is False:
        errors.append("后端 %r 未接入（available=false），不得当作可用" % bid)

    if mode not in ("reference", "text2img"):
        errors.append("generation.mode 只能是 reference / text2img，收到 %r" % mode)

    if mode == "reference":
        if not supported:
            if fallback == "text2img" and consent:
                warnings.append("后端 %r 不支持参考图入参，按用户显式同意关闭参考图；"
                                "本单 identity_unverified=true，报告中必须显著标注"
                                % bid)
            else:
                errors.append("后端 %r 未暴露参考图入参，默认 fallback=fail："
                              "不得调用生成，也不得静默退回文生图" % bid)
        if not declared and not refs:
            errors.append("mode=reference 但 brief.generation.reference_images 为空："
                          "参考图模式必须先把参考图归档进订单目录并记账")
        if max_refs and len(declared or refs) > max_refs:
            errors.append("参考图 %d 张超过后端上限 %d 张"
                          % (len(declared or refs), max_refs))
    elif mode == "text2img":
        if not consent:
            errors.append("mode=text2img 需要用户显式同意（"
                          "generation.text2img_consent=true），否则默认参考图模式")
        if declared:
            warnings.append("已登记参考图但 mode=text2img：参考图未参与生成")

    bad_refs = [r for r in refs if not r.get("verified")]
    if refs and bad_refs:
        for r in bad_refs:
            errors.append("参考图未通过核对：%s（%s）"
                          % (r.get("path"),
                             r.get("verify_note") or "sha256/路径不一致"))
    if order_dir and refs:
        base = os.path.abspath(order_dir)
        outside = [r for r in refs
                   if not os.path.abspath(r.get("abs_path") or r.get("path") or "")
                   .startswith(base + os.sep)]
        if outside:
            errors.append("参考图必须归档在订单目录内（平铺），不得引用目录外文件：%s"
                          % ", ".join(os.path.basename(r.get("path") or "")
                                      for r in outside))
    strength = gen.get("strength")
    rng = ref_cap.get("strength_range")
    if strength is not None and ref_cap.get("strength_range") is None:
        errors.append("后端 %r 没有 strength 入参，strength 必须为 null" % bid)
    elif strength is not None and rng and not (rng[0] <= float(strength) <= rng[1]):
        errors.append("strength=%s 超出后端范围 %s" % (strength, rng))

    ref_ok = bool(mode == "reference" and supported and refs
                  and not bad_refs)
    if mode == "text2img":
        identity_unverified = True
    else:
        identity_unverified = not ref_ok
    return {
        "backend": bid,
        "backend_name": (backend or {}).get("display_name"),
        "backend_verified": bool((backend or {}).get("verified")),
        "mode": mode,
        "reference_images": [{"path": r.get("path"), "role": r.get("role"),
                              "sha256": r.get("sha256")} for r in refs]
        or declared,
        "reference_input_supported": supported,
        "reference_param": ref_cap.get("param"),
        "reference_verified": ref_ok,
        "max_reference_images": max_refs,
        "strength": strength,
        "fallback": fallback,
        "text2img_consent": consent,
        "identity_unverified": identity_unverified,
        "errors": errors,
        "warnings": warnings,
        "host_note": "参考图能力取决于宿主/后端；换宿主必须重新跑一次能力检测",
    }


def _active_entries(entries):
    """只取 active=true 的参考图；没有显式 active 标记时全部参与（兼容旧单）。"""
    entries = [e for e in (entries or []) if e]
    active = [e for e in entries
              if isinstance(e, dict) and e.get("active") is True]
    return active if active else entries


def load_reference_images(arg_refs, brief, raw_dir, brief_path):
    """按 brief.generation.reference_images 的记账读取参考图（订单目录平铺）。

    参考图归档在订单目录内，命名 `<prefix>-ref-N.png`，brief 记录
    `path` / `sha256` / `role`。不再扫描 `refs/` 子目录。
    """
    base = os.path.dirname(os.path.abspath(brief_path)) if brief_path else raw_dir
    items = []
    if arg_refs:
        for p in arg_refs.split(","):
            p = p.strip()
            if p:
                items.append({"path": p, "role": "identity", "sha256": None})
    elif brief:
        recorded = (brief.get("generation") or {}).get("reference_images") or []
        for it in _active_entries(recorded):
            if isinstance(it, str):
                items.append({"path": it, "role": "identity", "sha256": None})
            elif isinstance(it, dict) and it.get("path"):
                items.append({"path": it["path"], "role": it.get("role"),
                              "sha256": it.get("sha256"),
                              "active": it.get("active")})
    if not items:
        # 兜底：订单根里已有的平铺参考图（旧单未记账时也能读）
        if os.path.isdir(base):
            for f in sorted(os.listdir(base)):
                if re.match(r".+-ref-\d+\.(png|jpe?g|webp)$", f, re.I):
                    items.append({"path": f, "role": "identity", "sha256": None})
    out = []
    base_abs = os.path.abspath(base)
    for it in items:
        p = it["path"]
        if not os.path.isabs(p):
            p = os.path.join(base_abs, p)
        p = os.path.abspath(p)
        entry = {"path": (os.path.relpath(p, base_abs)
                          if p.startswith(base_abs + os.sep) else p),
                 "abs_path": p, "role": it.get("role"),
                 "recorded_sha256": it.get("sha256"), "sha256": None,
                 "verified": False, "verify_note": None}
        if not os.path.isfile(p):
            entry["verify_note"] = "文件不存在"
            print("reference missing: %s" % p)
            out.append(entry)
            continue
        try:
            entry["sha256"] = _sha256_file(p)
        except OSError as exc:
            entry["verify_note"] = "sha256 计算失败: %s" % exc
            out.append(entry)
            continue
        if it.get("sha256") and entry["sha256"] != it["sha256"]:
            entry["verify_note"] = "sha256 与 brief 记录不一致（图被替换或改过）"
            print("reference sha256 mismatch: %s" % p)
        else:
            entry["verified"] = True
        try:
            im = Image.open(p)
            im.load()
            entry["image"] = im.convert("RGB")
        except Exception as exc:
            entry["verified"] = False
            entry["verify_note"] = "图片不可读: %s" % exc
            print("reference unreadable: %s (%s)" % (p, exc))
        out.append(entry)
    return out


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


RAW_QUALITY_MIN = 60


def raw_quality(face, content_gate, style_gate, angle_gate, face_mode):
    """原图生成质量分（与裁切后交付分分开）。

    只回答「这张生成图本身够不够好」，不评价后处理。不达标时正确动作是重抽，
    不是调裁切参数或放宽阈值。四个维度：面部/轮廓 40、构图 30、风格 20、角度 10。
    """
    conf = float(face.get("conf") or 0.0)
    face_score = 40 if conf >= 0.6 else (25 if conf >= CONF_MIN else 0)

    if content_gate.get("passed") is True:
        comp_score = 30
    elif content_gate.get("passed") is None:
        comp_score = 15
    else:
        comp_score = 0

    if style_gate is None:
        style_score = 10
    else:
        style_score = 20 if style_gate["passed"] else 0

    if angle_gate is None:
        angle_score = 5
    elif angle_gate["passed"]:
        angle_score = 10
    else:
        angle_score = 0

    reasons = []
    if face.get("candidates", 1) > 1:
        reasons.append("raw:multi_face")
    if conf < CONF_MIN:
        reasons.append("raw:low_conf")
    if content_gate.get("passed") is False:
        reasons.extend("raw:" + r for r in content_gate.get("reasons", []))
    if style_gate is not None and not style_gate["passed"]:
        reasons.extend("raw:" + r for r in style_gate.get("reasons", []))
    if angle_gate is not None and not angle_gate["passed"]:
        reasons.append("raw:" + angle_gate["reason"])

    total = face_score + comp_score + style_score + angle_score
    return {
        "scope": "generation",
        "total": float(total),
        "scores": {"face": face_score, "composition": comp_score,
                   "style": style_score, "angle": angle_score},
        "status": "qualified" if total >= RAW_QUALITY_MIN else "unqualified",
        "threshold": RAW_QUALITY_MIN,
        "reasons": reasons,
        "note": "原图生成质量分；不达标应换提示词/方向/追加候选重抽，"
                "不靠裁切、放大或放宽阈值补救。",
    }


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="anime avatar crop + score + export pipeline")
    ap.add_argument("--raw", required=True,
                    help="flat 下为订单根（图片平铺）；legacy 下为 raw 目录")
    ap.add_argument("--out", default=None,
                    help="输出目录；legacy 必填。flat 下可选，默认写回订单根")
    ap.add_argument("--layout", choices=("flat", "legacy"), default="flat",
                    help="flat=订单内图片平铺 <前缀>-avatar-N（默认，新订单）；legacy=旧 crop/native 结构")
    ap.add_argument("--prefix", default=None,
                    help="flat 文件名前缀（默认取角色卡 character_id 或 brief.character_id）")
    ap.add_argument("--order-id", default=None,
                    help="当前订单 id（默认读 brief.order_id > rounds.json 当前订单）")
    ap.add_argument("--brief", default=None, help="brief.json（可选，用于报告头）")
    ap.add_argument("--formats", default="1024", help="逗号分隔：1024,2k,4k")
    ap.add_argument("--jpg", action="store_true", help="额外导出 JPG 压缩版")
    ap.add_argument("--jpg-quality", type=int, default=88, help="JPG 质量（默认 88）")
    ap.add_argument("--style", default=None, help="风格档案 JSON（styles/<style-id>.json）")
    ap.add_argument("--character", default=None, help="角色卡 JSON（characters/<character-id>.json）")
    ap.add_argument("--angles", default=None, help="角度矩阵 JSON（默认 references/angles.json）")
    ap.add_argument("--checklist", default=None, help="人工角色核对清单 JSON（缺省则闸门为 null）")
    ap.add_argument("--expected-angle", default=None, help="文件名无角度前缀时的期望角度 id")
    ap.add_argument("--round", default=None,
                    help="显式轮次（最高优先级）；缺省读 brief.round；flat 不从文件名/目录猜")
    ap.add_argument("--budget", type=int, default=10,
                    help="本单生成预算（默认 10，保守模式；量产需显式调大）")
    ap.add_argument("--allow-over-budget", action="store_true",
                    help="超预算仍继续（必须先取得用户确认）")
    ap.add_argument("--face-mode", choices=FACE_MODES, default=None,
                    help="faced / faceless；缺省读 brief 或角色卡（默认 faced）")
    ap.add_argument("--backends", default=None,
                    help="生成后端能力注册表 JSON（默认 references/backends.json）")
    ap.add_argument("--references", default=None,
                    help="参考图路径，逗号分隔；缺省读 brief.generation.reference_images"
                         "（订单目录平铺，<prefix>-ref-N.png）")
    args = ap.parse_args()

    raw_dir, out_dir = args.raw, args.out
    layout = args.layout
    if layout == "legacy" and not out_dir:
        print("--layout legacy 需要 --out")
        return 2
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

    if layout == "legacy":
        for sub in ("crop", "native", "preview"):
            os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    profile = load_json(args.style)
    card = load_json(args.character)
    if not args.angles:
        args.angles = default_angles_path()
    angle_matrix = load_json(args.angles)
    checklist = load_json(args.checklist)
    char_gate = check_character(checklist, card)
    norm, brief_errors = normalize_brief(brief, card, args.budget)
    if brief_errors:
        print("brief invalid: " + "; ".join(brief_errors))
        return 4
    expected_default = args.expected_angle or (norm or {}).get("angle")
    if expected_default == "frontal":
        expected_default = "front"
    face_mode = args.face_mode or (norm or {}).get("face_mode") or "faced"

    # 轮次解析：flat 只看 --round / brief.round；文件名与目录都不带轮次。
    # 都取不到时打印警告并在 manifest 记 source=unknown，不猜。
    cli_round = args.round
    brief_round = (norm or {}).get("round")
    prefix = None
    current_order = None
    current_ordered_at = None
    prev_manifest_doc = {}
    if layout == "flat":
        order_root = out_dir or os.path.abspath(raw_dir)
        os.makedirs(order_root, exist_ok=True)
        ledger_oid, ledger_at = ledger_order(order_root)
        current_order = (args.order_id or (brief or {}).get("order_id")
                         or ledger_oid)
        current_ordered_at = ((brief or {}).get("ordered_at") or ledger_at)
        if current_order and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*",
                                              current_order):
            print("order id invalid: %s（只允许字母/数字/下划线/点/连字符）"
                  % current_order)
            return 2
        prefix = (args.prefix or (card or {}).get("character_id")
                  or (norm or {}).get("character_id")
                  or flat_detect_prefix(order_root) or "avatar")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", prefix):
            print("prefix invalid: %s（只允许字母/数字/下划线/连字符）" % prefix)
            return 2
        prev_meta = {}
        prev_manifest = os.path.join(order_root, "manifest.json")
        if os.path.exists(prev_manifest):
            try:
                with open(prev_manifest, "r", encoding="utf-8-sig") as fh:
                    prev_manifest_doc = json.load(fh) or {}
                for it in prev_manifest_doc.get("items") or []:
                    if it.get("file"):
                        prev_meta[it["file"]] = it
            except Exception:
                prev_manifest_doc = {}
                prev_meta = {}
        # 同目录已有别的订单的 manifest 记录时，无归属的旧 -src 才按历史处理，
        # 避免把上一单的图算进本单预算。
        has_history = any(it.get("order_id") and it.get("order_id") != current_order
                          for it in prev_meta.values())
        names = flat_sources(order_root)
        start = flat_max_number(order_root, prefix)
        nums = flat_numbers(names, prefix, start)
        work = []
        for name, nn in zip(names, nums):
            path = os.path.join(order_root, name)
            old = prev_meta.get(name) or {}
            item_order = old.get("order_id")
            if item_order is None and current_order:
                item_order = (None if _FLAT_SRC.match(name) and has_history
                              else current_order)
            if old.get("round"):
                rkey, rsrc = old["round"], old.get("round_source") or "manifest"
            else:
                rkey, rsrc = resolve_round_meta(cli_round, brief_round)
            angle = resolve_angle(name, angle_matrix, expected_default)
            variant = old.get("variant")
            if variant is None:
                variant = resolve_variant(name)
            try:
                path = flat_rename_source(path, prefix, nn)
                name = os.path.basename(path)
            except OSError as exc:
                print("WARNING: 原图改名失败（%s），保留原名。" % exc)
            work.append({"name": name, "path": path, "round_dir": order_root,
                         "nn": nn, "round": rkey, "round_source": rsrc,
                         "order_id": item_order, "angle": old.get("angle") or angle,
                         "variant": variant, "file": rel(path, order_root),
                         "id": "%s-avatar-%d" % (prefix, nn)})
        out_dir = order_root
        ref_dir = order_root
    else:
        files = [f for f in sorted(os.listdir(raw_dir))
                 if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        work = []
        for name in files:
            path = os.path.join(raw_dir, name)
            rkey, rsrc = resolve_round_legacy(name, path, cli_round, brief_round)
            work.append({"name": name, "path": path, "round_dir": out_dir, "nn": None,
                         "round": rkey, "round_source": rsrc, "order_id": None,
                         "angle": resolve_angle(name, angle_matrix, expected_default),
                         "variant": resolve_variant(name),
                         "file": name, "id": os.path.splitext(name)[0]})
        ref_dir = raw_dir

    if not work:
        print("no images in %s" % raw_dir)
        return 2

    batch_round, _batch_source = resolve_round_meta(cli_round, brief_round)
    if layout == "flat" and current_order:
        # 同目录长期累积多单；预算只算当前订单，历史图片不累加
        used = len([w for w in work if w["order_id"] == current_order])
        budget_source = "order_id"
    elif layout == "flat" and batch_round != "__batch__":
        # 没有订单台账时退回按本批轮次计数
        used = len([w for w in work if w["round"] == batch_round])
        budget_source = "batch_round"
    else:
        used = len(work)
        budget_source = "raw_count"
    budget = {"limit": args.budget, "used": used, "remaining": max(0, args.budget - used),
              "exceeded": used > args.budget,
              "source": budget_source}
    if layout == "legacy":
        with open(os.path.join(out_dir, "budget.json"), "w", encoding="utf-8") as fh:
            json.dump(budget, fh, ensure_ascii=False, indent=2)
    if budget["exceeded"] and not args.allow_over_budget:
        print("budget exceeded: raw=%d budget=%d，已停止评分。"
              "请确认是否继续，确认后加 --allow-over-budget。" % (used, args.budget))
        return 3

    unknown_rounds = [w for w in work if w["round_source"] == "unknown"]
    if unknown_rounds:
        print("WARNING: 轮次未知（%d/%d 张未识别到轮次），已按单轮处理，"
              "可能误判重复；建议用 --round 或 brief.round 显式指定。"
              % (len(unknown_rounds), len(work)))

    # 参考图能力检测（调用前置）：先读本单参考图并核对 sha256，再判后端能力。
    # identity_unverified=false 只在「后端支持 + 参考图可读 + sha 对得上」时成立。
    refs = load_reference_images(args.references, brief, ref_dir, brief_path)
    backends = load_json(args.backends or default_backends_path())
    gen_check = check_generation((brief or {}).get("generation"), backends,
                                 refs=refs,
                                 order_dir=(os.path.dirname(os.path.abspath(brief_path))
                                            if brief_path else ref_dir))
    if gen_check and gen_check["errors"]:
        for e in gen_check["errors"]:
            print("generation capability: " + e)
        print("已停止：生成能力/参考图核对不满足，退不退回文生图只能由用户显式决定。")
        return 4
    if gen_check:
        if not gen_check["backend_verified"]:
            print("WARNING: 后端 %s 标记 verified=false —— 尚未在真实环境跑通，"
                  "本次产物不能当作身份/画风已验证。" % gen_check["backend"])
        if gen_check["identity_unverified"]:
            print("WARNING: 本单 identity_unverified=true —— 参考图未真正参与生成，"
                  "只在报告里标注，不能当作身份一致。")

    results = []
    for item in work:
        name = item["name"]
        item_id = item["id"]
        path = item["path"]
        item_round = item["round"]
        item_round_source = item["round_source"]
        expected_angle = item["angle"]
        try:
            img = Image.open(path)
            img.load()
            img = img.convert("RGB")
        except Exception as exc:
            results.append({"id": item_id, "file": item["file"],
                            "error": "open_failed:%s" % exc,
                            "verdict": "ERROR", "total": 0.0, "reasons": ["open_failed"],
                            "raw_quality": {"scope": "generation", "total": 0.0,
                                            "scores": {}, "status": "unreadable",
                                            "threshold": RAW_QUALITY_MIN,
                                            "reasons": ["raw:open_failed"],
                                            "note": "原图不可读，无法评估生成质量。"},
                            "round": item_round,
                            "round_source": item_round_source,
                            "order_id": item.get("order_id"),
                            "nn": item.get("nn")})
            continue

        image_content = _content_analysis(img)
        if face_mode == "faceless":
            face = _faceless_head(img, image_content)
        else:
            face = detect_face(img)
        if not face:
            results.append({
                "id": item_id, "file": item["file"], "verdict": "REJECT", "total": 0.0,
                "metrics": {}, "scores": {}, "gates": {"face": False},
                "reasons": ["locate_failed"],
                "face_mode": face_mode,
                "locate": {"stage": "locate",
                           "from": "faceless_locate" if face_mode == "faceless" else "no_face",
                           "note": ("无脸轮廓回退也未定位到头部；"
                                    if face_mode == "faceless" else
                                    "眼睛/肤色/头部轮廓回退都未定位到主体；")
                                   + "这是定位失败，不等于构图不合格"},
                "raw": {"content": image_content},
                "outputs": {}, "detect": None,
                "raw_quality": {"scope": "generation", "total": 0.0,
                                "scores": {}, "status": "unqualified",
                                "threshold": RAW_QUALITY_MIN,
                                "reasons": ["raw:locate_failed"],
                                "note": "定位失败：属于生成图问题，应重抽而不是调裁切。"},
                "round": item_round,
                "round_source": item_round_source})
            continue

        best = None
        best_any = None
        candidates = (build_crops_faceless(img, face, image_content)
                      if face_mode == "faceless"
                      else build_crops(img, face, image_content))
        for cand in candidates:
            sc = (score_crop_faceless(img, cand, face, image_content)
                  if face_mode == "faceless"
                  else score_crop(img, cand, face, image_content))
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

        # 闸门在最终成品上重跑一遍：内容留白 / 主体占比 / 头顶平切
        # 以成品为准，原图与成品分别记录，避免「只跑部分指标就判 PASS」。
        final_content = _content_analysis(master)
        final_content_gate = _content_gate(final_content, image_content)
        # 输入本身就是成品方图时（例如返修他人裁好的成品），
        # 原图的头被切平/留白越界无法靠再裁一次还原，必须按原图拦下。
        input_content_gate = _content_gate(image_content, None)
        input_square = abs(img.size[0] - img.size[1]) <= 2

        smetrics = style_metrics(master)
        style_gate = check_style(smetrics, profile)
        cmetrics = canvas_metrics(master)
        canvas_gate = check_canvas(cmetrics)
        angle_metrics = None
        if face_mode == "faceless":
            angle_metrics = _faceless_angle_metrics(img, face, image_content)
            detected_angle = {
                "visible_eyes": None, "pupil_edge": None,
                "skin_centroid": None, "nose_side": None,
                "facing": angle_metrics["facing"],
                "is_profile": False,
                "is_front": angle_metrics["facing"] == "front",
                "eye_center_y": None,
                "confidence": angle_metrics["confidence"],
                "metric_set": "head_contour",
                "visor_offset": angle_metrics["visor_offset"],
                "contour_asym": angle_metrics["contour_asym"],
                "sat_weight": angle_metrics["sat_weight"],
            }
        else:
            detected_angle = detect_angle(img, face, best["metrics"])
        angle_gate = check_angle(expected_angle, detected_angle, best["metrics"], angle_matrix)
        rawq = raw_quality(face, input_content_gate, style_gate, angle_gate, face_mode)
        ref_sim = reference_similarity(master, refs) if refs else None

        tid, tedge, tcontrast = thumb_readability(master)
        content_codes = ("composition_bad:whitespace",
                         "composition_bad:subject_share",
                         "composition_bad:head_cut")
        # 成品内容闸门是最终口径：先剔除候选阶段的同类原因，再按成品重记
        reasons = [r for r in best["reasons"] if r not in content_codes]
        reasons.extend(final_content_gate["reasons"])
        if input_square and input_content_gate["passed"] is False:
            for r in input_content_gate["reasons"]:
                if r not in reasons:
                    reasons.append(r)
        if tid < THUMB_MIN:
            reasons.append("thumb_unreadable")
        if face.get("candidates", 1) > 1:
            reasons.append("multi_face")
        if face["conf"] < CONF_MIN:
            reasons.append("low_conf")
        if style_gate and not style_gate["passed"]:
            reasons.extend(style_gate["reasons"])
        if face_mode == "faceless" and style_gate is None:
            # 无脸模式不允许把「没有风格档案」当成风格通过
            reasons.append("style_unverified")
        if char_gate and not char_gate["passed"]:
            reasons.extend(char_gate["reasons"])
        if not canvas_gate["passed"]:
            reasons.extend(canvas_gate["reasons"])
        if angle_gate and not angle_gate["passed"]:
            reasons.append(angle_gate["reason"])
        verdict = "PASS" if not reasons else "REJECT"

        if layout == "flat":
            outputs, upinfo = export_flat(master, native_crop, prefix, item["nn"],
                                          item["round_dir"], formats,
                                          args.jpg, args.jpg_quality)
        else:
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
            "file": item["file"],
            "verdict": verdict,
            "total": best["total"],
            "metrics": best["metrics"],
            "scores": best["scores"],
            "native_size": best["native_size"],
            "gates": {
                "face": True if face_mode == "faced" else None,
                "head_contour": True if face_mode == "faceless" else None,
                "composition": not any(r.startswith("composition_bad") for r in reasons),
                "content": final_content_gate["passed"],
                "content_input": input_content_gate["passed"],
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
            "face_mode": face_mode,
            "metric_set": best.get("metric_set", "face_metrics"),
            "face_metrics_applied": best.get("face_metrics_applied", True),
            "round": item_round,
            "round_source": item_round_source,
            "order_id": item.get("order_id"),
            "nn": item.get("nn"),
            "variant": item.get("variant"),
            "format": {"master": "png", "tiers": list(formats),
                       "jpg": args.jpg, "jpg_quality": args.jpg_quality
                       if args.jpg else None},
            "expected_angle": expected_angle,
            "raw_quality": rawq,
            "angle_metrics": angle_metrics,
            "reference_similarity": ref_sim,
            "generation": gen_check,
            "gate_scope": "final_crop",
            "final_gate": {
                "metric_set": "content_bbox",
                "content": final_content,
                "gate": final_content_gate,
            },
            "raw": {
                "content": image_content,
                "content_gate": input_content_gate,
                "detect_method": face.get("method"),
                "face_box": [round(v, 1) for v in face["box"]],
            },
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

    # 去重：只在同一轮、同一目标角度的候选之间判近重复。
    # 跨轮（含历史版本）与不同角度不是重复候选，历史版本一律保留。
    for i in range(len(results)):
        if "ahash" not in results[i]:
            continue
        for j in range(i + 1, len(results)):
            if "ahash" not in results[j]:
                continue
            if results[i].get("round") != results[j].get("round"):
                continue
            if results[i].get("order_id") != results[j].get("order_id"):
                continue
            if results[i].get("expected_angle") != results[j].get("expected_angle"):
                continue
            if hamming(results[i]["ahash"], results[j]["ahash"]) <= 3:
                loser = j if results[i]["total"] >= results[j]["total"] else i
                results[loser]["verdict"] = "REJECT"
                if "duplicate" not in results[loser]["reasons"]:
                    results[loser]["reasons"].append("duplicate")

    passed = [r for r in results if r.get("verdict") == "PASS"]
    ranked = sorted(results, key=lambda r: r.get("total", 0), reverse=True)

    # 联系表按订单生成，文件名带本单编号区间，后一单不覆盖前一单。
    if layout == "flat" and current_order:
        sheet_scope = [r for r in ranked
                       if "image" in r and r.get("order_id") == current_order]
    else:
        sheet_scope = [r for r in ranked if "image" in r]
    sheet_items = [{"image": r["image"], "total": r["total"], "verdict": r["verdict"],
                    "tag": r["id"], "reason": (r["reasons"][0] if r["reasons"] else "")}
                   for r in sheet_scope]
    sheet_name = None
    if sheet_items:
        nums = [r["nn"] for r in sheet_scope if r.get("nn") is not None]
        if layout == "flat" and prefix and nums:
            sheet_name = "%s-avatar-%d-%d-sheet.jpg" % (prefix, min(nums), max(nums))
        else:
            sheet_name = "contact_sheet.jpg"
        sheet_path = os.path.join(out_dir, sheet_name)
        draw_sheet(sheet_items, sheet_path)
    else:
        sheet_path = os.path.join(out_dir, "contact_sheet.jpg")

    manifest = {
        "meta": {
            "tool": "anime-avatar/avatar_pipeline.py",
            "version": VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "agent-side",
            "reproducible": False,
            "native_limit": NATIVE_LIMIT,
            "layout": layout,
            "order_id": current_order,
            "ordered_at": current_ordered_at,
            "order_dir": os.path.basename(os.path.normpath(out_dir)),
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
            "face_mode": face_mode,
            "generation": gen_check,
            "reference_images": [{"path": r["path"], "role": r.get("role"),
                                  "sha256": r.get("sha256"),
                                  "verified": bool(r.get("verified"))}
                                 for r in refs],
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
                          "content_whitespace_share_check", "head_cut_check",
                          "content_head_fallback", "final_crop_gate",
                          "faceless_head_contour_mode", "front_angle_gate",
                          "reference_similarity", "backend_capability_check",
                          "character_checklist_gate"],
            "unavailable": ["ocr_text", "nsfw", "emotion", "style_embedding", "anatomy",
                            "real_esrgan_superres"],
            "note": "风格闸门是粗指标（主色数/饱和度/边缘密度/背景平涂度），只能拦明显异体系，"
                    "不等于风格 embedding；截断与边框闸门也是像素启发式，不是语义分割；"
                    "内容留白/主体占比/头顶平切闸门只在平涂背景可判定时生效，"
                    "不可判定记 null，不当作通过；所有构图闸门在最终成品上重跑，"
                    "manifest 同时记录原图(raw)与成品(final_gate)；"
                    "角色闸门依赖人工清单；无 OCR/NSFW/情绪/解剖/超分模型，"
                    "对应闸门记为 null。分数按可用维度(face_quality 40 + composition 60)归一化",
        },
        "summary": {
            "total": len(results),
            "pass": len(passed),
            "reject": len(results) - len(passed),
            "best_total": ranked[0]["total"] if ranked else 0,
            "face_mode": face_mode,
            "order_id": current_order,
            "identity_unverified": bool(gen_check and gen_check["identity_unverified"]),
            "reference_verified": bool(gen_check and gen_check["reference_verified"]),
            "backend_verified": bool(gen_check and gen_check["backend_verified"]),
            "contact_sheet": sheet_name,
        },
        "items": [],
    }
    new_items = [{k: v for k, v in r.items() if k not in ("image", "ahash")}
                 for r in ranked]
    if layout == "flat" and prev_manifest_doc:
        # 同目录累积多单：本次未处理的旧订单记录原样保留，新订单不覆盖历史
        current_files = {it.get("file") for it in new_items if it.get("file")}
        old_items = [it for it in prev_manifest_doc.get("items") or []
                     if it.get("file") and it["file"] not in current_files]
        new_items = old_items + new_items
    manifest["items"] = new_items
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
               "（已超预算，经确认继续）" if budget["exceeded"] else ""), ""]
    if gen_check and gen_check["identity_unverified"]:
        if gen_check.get("mode") == "text2img":
            lines += ["> **警告：本单 identity_unverified=true。** 已按用户显式同意关闭参考图"
                      "（`mode=text2img`）；参考图未参与生成，不能据此认为身份一致。", ""]
        else:
            lines += ["> **警告：本单 identity_unverified=true。** 后端 `%s` 的参考图"
                      "未通过核对（缺失/sha256 不一致/未支持）；不能据此认为身份一致。"
                      % gen_check.get("backend"), ""]
    if gen_check and not gen_check.get("backend_verified", True):
        lines += ["> 后端 `%s` 标记 `verified=false`：尚未在真实环境跑通并回传产物，"
                  "本单结果不能当作后端能力已验证。" % gen_check.get("backend"), ""]
    if gen_check and gen_check["warnings"]:
        lines += ["> " + w for w in gen_check["warnings"]] + [""]
    if face_mode == "faceless":
        lines += ["无脸/皮套模式（`face_mode=faceless`）：指标集为 `head_contour`，"
                  "眼部/双侧对称/面部占比不参与评分，改为头部轮廓占比 + 上下留白 + 居中偏移；"
                  "角度用面罩/高光偏移 + 头盔轮廓对称性判定。", ""]
    lines += ["| # | 图 | 角度 | 总分 | 脸占比 | 眼睛位置 | 截断 | 画布 | 风格 | 角色 | 角度判定 | 结论 | 原因 |",
              "|-|-|-|-|-|-|-|-|-|-|-|-|-|"]
    for i, r in enumerate(ranked, 1):
        m = r.get("metrics", {})
        ac = r.get("angle_check") or {}
        det = ac.get("detected") or {}
        if det:
            face_txt = {"left": "朝左", "right": "朝右",
                        "front": "正面"}.get(det.get("facing"), "方向未定")
            angle_txt = ("%s/轮廓" % face_txt
                         if det.get("metric_set") == "head_contour"
                         else "%s/%s眼" % (face_txt, det.get("visible_eyes", "-")))
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
    lines += ["", "| 图 | 上留白 | 下留白 | 主体占比 | 顶部平切比 | 内容闸门 |",
              "|-|-|-|-|-|-|"]
    for r in ranked:
        fg = r.get("final_gate") or {}
        c = fg.get("content") or {}
        gate = fg.get("gate") or {}
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            r["id"], c.get("top_blank", "-"), c.get("bottom_blank", "-"),
            c.get("share", "-"), c.get("top_run_ratio", "-"),
            "n/a" if gate.get("passed") is None
            else ("PASS" if gate.get("passed") else "FAIL")))
    if any(r.get("face_mode") == "faceless" for r in ranked):
        lines += ["", "| 图 | 头占比 | 头宽比 | 居中偏移 | 面罩偏移 | 轮廓不对称 | 指标集 |",
                  "|-|-|-|-|-|-|-|"]
        for r in ranked:
            if r.get("face_mode") != "faceless":
                continue
            m = r.get("metrics") or {}
            am = r.get("angle_metrics") or {}
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                r["id"], m.get("head_ratio", "-"), m.get("head_width_ratio", "-"),
                m.get("center_offset", "-"), am.get("visor_offset", "-"),
                am.get("contour_asym", "-"), r.get("metric_set", "-")))
    lines += ["", "| 图 | 主色数 | 饱和度 | 边缘密度 | 明度p50 | 硬软边比 | 近白占比 | 背景占比 | 背景 stddev | 背景 |",
              "|-|-|-|-|-|-|-|-|-|-|"]
    for r in ranked:
        s = r.get("style") or {}
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], s.get("palette_colors", "-"), s.get("saturation", "-"),
            s.get("edge_density", "-"), s.get("value_p50", "-"),
            s.get("hard_soft_ratio", "-"), s.get("near_white_share", "-"),
            s.get("background_share", "-"),
            s.get("background_stddev", "-"), s.get("background_mode", "-")))
    if refs:
        lines += ["", "| 图 | 参考风格相似度 | 主体相似度 | 身份相似度 | 说明 |",
                  "|-|-|-|-|-|"]
        for r in ranked:
            rs = r.get("reference_similarity") or {}
            lines.append("| %s | %s | %s | %s | %s |" % (
                r["id"], rs.get("style_similarity", "-"),
                rs.get("subject_similarity", "-"),
                "null（无 embedding）" if rs.get("identity_similarity") is None else
                rs.get("identity_similarity"),
                "像素统计（色板/主体 HSV/边缘/明度），不是身份一致"))
        lines += ["", "身份核对需人工过清单：发型/发绺、头饰/帽型与徽标、服装配色与领口、"
                  "标志性细节、肤色与年龄感。",
                  "参考图是否真正参与生成：看 manifest `generation.reference_verified` "
                  "与 `backend_verified`；换宿主必须重新跑能力检测。"]
    lines += ["", "原因码释义：" + "；".join("%s=%s" % (k, v) for k, v in REASON_LABEL.items()),
              "",
              "> 已紧构图的原图会选中「全幅」候选，不再为凑目标脸占比二次放大裁切；",
              "> 排序以眼位为主（构图 60%、其中眼位 65%），脸占比只作弱约束；",
              "> 满出血模式下不再按留边距离一刀切：贴边但主体完整放行，只有贴边连续段过宽且向内延续才判截断；",
              "> 画布闸门检查方形与边框/装饰圈；带圆环的画框会记 canvas_bad:frame；",
              "> 风格闸门是粗指标，只拦明显异体系；角色闸门依赖人工清单，缺必填项不放行；",
              "> 角度判定覆盖侧脸双向：朝左 pupil_edge ≤0.42 + 鼻尖侧左；朝右 ≥0.58 + 鼻尖侧右；"
              "正脸需双眼检出 + 眼位 %.2f–%.2f（现有正样本 0.303–0.316）+ 水平平衡/倾斜；"
              "3/4 与仰俯仍 reserved，命中记 angle_mismatch；"
              "方向判不出记 angle_undetermined，交人工确认；" % (FRONT_EYE_Y[0], FRONT_EYE_Y[1]),
              "> 默认朝向按性别：男左、女右、未指定默认左；用户可覆盖；带 brief 时必须有 confirm.confirmed，否则拒绝执行；",
              "> 单眼判定仍可能把远眼未检出的 3/4 脸当成侧脸，800px 人工复查不可省。",
              "> 情绪、解剖、风格 embedding 无本地模型，记 null，不参与总分。"]
    if layout == "legacy":
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
